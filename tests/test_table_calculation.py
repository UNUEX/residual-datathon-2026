import json
import math
import unittest

from src.table_calculation import (CalculationError, build_prompt, calculate, create_schema,
    is_missing, parse_number, profile_columns, question_guard, validate_columns, validate_plan)


class TableCalculationTests(unittest.TestCase):
    def setUp(self):
        self.rows = [["001", "Север", "-5", "1,5"], ["002", "Север", "0", "2,5"],
            ["003", "Юг", "10", ""], ["004", "Юг", "oops", "3,5"], ["005", "", "100", "9,5"]]
        self.profiles = profile_columns(["Код", "Регион", "Доход", "Площадь"], self.rows)
        self.columns = validate_columns(self.profiles, [
            {"id": "c0", "label": "Код", "role": "identifier", "unit": ""},
            {"id": "c1", "label": "Регион", "role": "category", "unit": ""},
            {"id": "c2", "label": "Доход", "role": "number", "unit": "тенге"},
            {"id": "c3", "label": "Площадь", "role": "number", "unit": "м²", "decimal_separator": ","}])
        self.dataset = {"columns": self.columns, "period": "2024", "observation_unit": "домохозяйство"}
        self.plan = {"status": "ready", "reason": "По подтверждённым полям", "metrics": ["c2", "c3"],
            "aggregation": "mean", "group_by": "c1"}

    def test_explicit_decimal_convention_and_grouping(self):
        self.assertEqual(parse_number("1,234", ","), 1.234)
        self.assertIsNone(parse_number("1,234", "."))
        self.assertIsNone(parse_number("1.234,56", ","))
        self.assertEqual(parse_number("-1\u202f234,50", ","), -1234.5)
        self.assertEqual(parse_number("+1 234 567.25", "."), 1234567.25)
        for value in ("12 34", "1,2,3", "Infinity", "1e999", "7%", "USD 12", "1\t234"):
            self.assertIsNone(parse_number(value, "."))
        self.assertEqual(parse_number("0", "."), 0)
        self.assertEqual(parse_number("-2.5e2", "."), -250)

    def test_profiles_preserve_leading_zero_identifiers_and_missing(self):
        self.assertEqual(self.profiles[0]["inferred_role"], "identifier")
        self.assertEqual(self.profiles[0]["sample"], ["001", "002", "003"])
        self.assertEqual(self.profiles[3]["suggested_decimal_separator"], ",")
        self.assertEqual(self.profiles[3]["missing_count"], 1)
        self.assertEqual(self.profiles[3]["numeric_count"], 4)
        self.assertTrue(is_missing(" N/A "))
        self.assertFalse(is_missing("0"))
        self.assertEqual(profile_columns(["measure"], [["001"], ["002"]])[0]["inferred_role"], "identifier")

    def test_confirmed_columns_match_ids_and_keep_source_names(self):
        reordered = [dict(column) for column in reversed(self.columns)]
        reordered[0]["source_name"] = "untrusted replacement"
        result = validate_columns(self.profiles, reordered)
        self.assertEqual(result[3]["source_name"], "Площадь")
        for invalid in (self.columns[:-1], self.columns[:-1] + [self.columns[0]],
                [dict(column, id="x") if i == 0 else column for i, column in enumerate(self.columns)],
                [dict(column, label="Одинаково") for column in self.columns],
                [dict(column, role="category") for column in self.columns]):
            with self.assertRaises(CalculationError):
                validate_columns(self.profiles, invalid)
        for field, value in (("id", []), ("role", {}), ("decimal_separator", []), ("unit", "x" * 61)):
            invalid = [dict(column) for column in self.columns]
            invalid[0][field] = value
            with self.assertRaises(CalculationError):
                validate_columns(self.profiles, invalid)

    def test_active_column_and_metric_limits(self):
        profiles = profile_columns([f"m{i}" for i in range(16)], [["1"] * 16])
        columns = [{"id": profile["id"], "label": profile["source_name"], "role": "number", "unit": ""} for profile in profiles]
        with self.assertRaises(CalculationError):
            validate_columns(profiles, columns)
        with self.assertRaises(CalculationError):
            validate_plan(dict(self.plan, metrics=[f"c{i}" for i in range(6)]), {"columns": columns})

    def test_plan_schema_and_prompt_do_not_contain_rows_or_samples(self):
        dataset = dict(self.dataset, rows=[["PRIVATE123"]], samples=["SECRET"])
        prompt = build_prompt(dataset)
        self.assertNotIn("PRIVATE123", prompt)
        self.assertNotIn("SECRET", prompt)
        schema = create_schema(self.columns)
        self.assertEqual(schema["properties"]["metrics"]["items"]["enum"], ["c2", "c3"])
        self.assertEqual(schema["properties"]["group_by"]["enum"], ["none", "c1"])
        self.assertFalse(schema["additionalProperties"])

    def test_plan_rejects_unknown_fields_extras_and_invalid_types(self):
        for patch in ({"metrics": ["c0"]}, {"metrics": ["c2", "c2"]}, {"metrics": "c2"},
                {"metrics": []}, {"group_by": "c0"}, {"group_by": []}, {"aggregation": "eval"},
                {"status": "clarify"}, {"reason": "x" * 1001}, {"script": "print(1)"}):
            with self.assertRaises(CalculationError):
                validate_plan(dict(self.plan, **patch), self.dataset)
        self.assertIsNone(validate_plan(dict(self.plan, group_by="none"), self.dataset)["group_by"])
        self.assertIsNone(validate_plan(dict(self.plan, group_by=None), self.dataset)["group_by"])
        self.assertEqual(validate_plan(dict(self.plan, status="clarify", metrics=[]), self.dataset)["status"], "clarify")

    def test_per_column_available_cases_and_excluded_missing_group(self):
        result = calculate(self.rows, self.columns, self.plan)
        north, south = result["table"]
        self.assertEqual(north["values"], {"c2": -2.5, "c3": 2.0})
        self.assertEqual(south["values"], {"c2": 10, "c3": 3.5})
        self.assertEqual(south["valid_counts"], {"c2": 1, "c3": 1})
        self.assertEqual(south["missing_counts"], {"c2": 0, "c3": 1})
        self.assertEqual(result["quality"], {"total_rows": 5, "grouping_missing_rows": 1, "invalid_numeric": {"c2": 1, "c3": 0}})

    def test_zero_negative_median_sum_and_no_valid_values(self):
        for aggregation, expected in (("mean", -2.5), ("median", -2.5), ("sum", -5)):
            result = calculate(self.rows[:2], self.columns, dict(self.plan, aggregation=aggregation, group_by=None))
            self.assertEqual(result["table"][0]["values"]["c2"], expected)
        result = calculate([["01", "A", "", ""]], self.columns, dict(self.plan, aggregation="sum"))
        self.assertIsNone(result["table"][0]["values"]["c2"])
        result = calculate([], self.columns, dict(self.plan, group_by=None))
        self.assertIsNone(result["table"][0]["values"]["c2"])

    def test_summary_answers_from_calculated_groups_and_per_metric_counts(self):
        result = calculate(self.rows, self.columns, dict(self.plan, reason="Модель выдумала итог: 9999999"))
        self.assertEqual(result["summary"][0],
            "Среднее арифметическое «Доход»: Север — -2,5 тенге (2 из 2 строк); Юг — 10 тенге (1 из 2 строк).")
        self.assertEqual(result["summary"][1],
            "Среднее арифметическое «Площадь»: Север — 2 м² (2 из 2 строк); Юг — 3,5 м² (1 из 2 строк).")
        self.assertEqual(result["summary"][-1], "Исходных строк: 5. Строк в расчёте: 4.")
        self.assertNotIn("9999999", " ".join(result["summary"]))

    def test_summary_distinguishes_zero_negative_and_missing_without_losing_precision(self):
        for value, expected in (("0", "0 тенге"), ("-123456.123456", "-123456,123456 тенге"),
                ("0.000001", "0,000001 тенге"), ("", "нет пригодных значений")):
            with self.subTest(value=value):
                result = calculate([["01", "Север", value, "1"]], self.columns,
                    dict(self.plan, aggregation="sum", metrics=["c2"], group_by=None))
                count = 0 if value == "" else 1
                self.assertEqual(result["summary"][0],
                    f"Сумма «Доход»: Вся таблица — {expected} ({count} из 1 строк).")

    def test_summary_limits_groups_but_keeps_the_complete_calculation(self):
        rows = [[str(i), f"Группа {i}", str(i), "1"] for i in range(1, 9)]
        result = calculate(rows, self.columns, dict(self.plan, metrics=["c2"], aggregation="median"))
        self.assertEqual(len(result["table"]), 8)
        self.assertIn("Группа 6 — 6 тенге (1 из 1 строк)", result["summary"][0])
        self.assertNotIn("Группа 7", result["summary"][0])
        self.assertIn("первые 6 из 8 групп; все значения — в таблице", result["summary"][1])

    def test_summary_reports_no_groups_when_all_categories_are_missing(self):
        result = calculate([["01", "", "5", "1"]], self.columns, dict(self.plan, metrics=["c2"]))
        self.assertEqual(result["summary"][0], "Среднее арифметическое «Доход»: нет групп для расчёта.")
        self.assertEqual(result["summary"][-1], "Исходных строк: 1. Строк в расчёте: 0.")

    def test_numeric_overflow_never_returns_infinity(self):
        rows = [["1", "A", "1e308", "1"], ["2", "A", "1e308", "1"]]
        for aggregation in ("mean", "median"):
            value = calculate(rows, self.columns, dict(self.plan, aggregation=aggregation))["table"][0]["values"]["c2"]
            self.assertTrue(math.isfinite(value))
        with self.assertRaises(CalculationError):
            calculate(rows, self.columns, dict(self.plan, aggregation="sum"))
        rows.append(["3", "A", "-1e308", "1"])
        value = calculate(rows, self.columns, dict(self.plan, aggregation="sum"))["table"][0]["values"]["c2"]
        self.assertEqual(value, 1e308)

    def test_group_cardinality_and_bad_row_width(self):
        rows = [[str(i), str(i), "1", "1"] for i in range(31)]
        with self.assertRaisesRegex(CalculationError, "30"):
            calculate(rows, self.columns, self.plan)
        with self.assertRaises(CalculationError):
            calculate([["1"]], self.columns, self.plan)

    def test_question_guard_rejects_unsupported_asks(self):
        for question in ("Взвешенный средний доход", "Прогноз дохода", "Корреляция дохода и площади",
                "Как доход влияет на площадь?", "Средний доход только по северу", "Доход > 10",
                "Средний доход за 2023 год", "Медианная площадь на человека", "Максимальный доход",
                "Минимум площади", "Count records by region", "Сколько наблюдений по регионам?", "Число строк"):
            self.assertEqual(question_guard(question, self.plan, self.dataset)["status"], "unsupported", question)
        self.assertEqual(question_guard("Среднее и медиана дохода", self.plan, self.dataset)["status"], "clarify")
        period = dict(self.dataset, period="2023–2024")
        self.assertEqual(question_guard("Средний доход за 2024 год", self.plan, period)["status"], "unsupported")
        period = dict(self.dataset, period="Не указан")
        self.assertEqual(question_guard("Средний доход за 2024 год", dict(self.plan, metrics=["c2"]), period)["status"], "ready")

    def test_question_guard_preserves_explicit_operation_and_fields(self):
        plan = dict(self.plan, metrics=["c2"], aggregation="median", group_by="none")
        with self.assertRaisesRegex(CalculationError, "mean"):
            question_guard("Средний Доход", plan, self.dataset)
        self.assertEqual(plan["aggregation"], "median")
        plan["aggregation"] = "mean"
        result = question_guard("Средний Доход", plan, self.dataset)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(question_guard("Средний Доход и Площадь", plan, self.dataset)["status"], "clarify")
        self.assertEqual(question_guard('Среднее поле "Выручка"', plan, self.dataset)["status"], "clarify")
        self.assertEqual(question_guard("Посчитай типичный размер выплаты", plan, self.dataset)["status"], "clarify")
        self.assertEqual(question_guard("Только средний Доход", plan, self.dataset)["status"], "ready")

    def test_overlapping_labels_do_not_invent_additional_requested_metrics(self):
        dataset = {**self.dataset, "columns": [dict(column, label="Доход после налогов") if column["id"] == "c3" else column for column in self.columns]}
        plan = dict(self.plan, metrics=["c3"], group_by="none")
        self.assertEqual(question_guard("Средний Доход после налогов", plan, dataset)["status"], "ready")


if __name__ == "__main__":
    unittest.main()
