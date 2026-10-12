"""Standalone brief charts must preserve signs, missingness and untrusted labels."""
from copy import deepcopy
from html.parser import HTMLParser
import math
import re
import unittest
import xml.etree.ElementTree as ET

from src.tabular import TabularService


NS = {"s": "http://www.w3.org/2000/svg"}


def example_report(values, *, groups=None, metrics=None):
    metrics = metrics or ["c1"]
    groups = groups or [f"Группа {index + 1}" for index in range(len(values))]
    return {
        "question": "Сравни среднее значение по группам",
        "created_at": "2026-10-12T00:00:00Z",
        "plan": {"metrics": metrics, "aggregation": "mean", "model": "local-model"},
        "dataset": {"label": "Набор для проверки", "period": "2025",
                    "observation_unit": "учреждение", "synthetic_data": False},
        "table": [{"group": group, "rows": 1,
                   "values": {metric: value for metric in metrics},
                   "valid_counts": {metric: int(value is not None) for metric in metrics},
                   "missing_counts": {metric: int(value is None) for metric in metrics}}
                  for group, value in zip(groups, values)],
        "summary": ["Среднее «Значение»: проверяемые числа из расчётного ядра."],
        "methodology": "Среднее по доступным значениям каждого столбца отдельно, без весов.",
        "formulas": {metric: {"label": f"Значение {metric}", "unit": "тыс. тенге",
                               "formula": "sum(valid values) / count(valid values)"}
                     for metric in metrics},
        "quality": {"total_rows": len(values), "grouping_missing_rows": 0,
                    "invalid_numeric": {metric: 0 for metric in metrics}},
        "limitations": ["Описательная статистика выбранных строк."],
        "sources": [{"filename": "example.csv", "table": "Таблица 1", "sha256": "a" * 64}],
        "calculation_code_sha256": "b" * 64,
    }


def charts(document):
    return [ET.fromstring(value) for value in re.findall(r"<svg\b.*?</svg>", document, re.S)]


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class BriefHtmlTests(unittest.TestCase):
    def test_mixed_signs_zero_and_missing_have_different_shapes(self):
        report = example_report([-10, 20, 0, None])
        original = deepcopy(report)
        document = TabularService._html(report)
        svg = charts(document)[0]
        axis = float(svg.find("s:line[@class='zero-axis']", NS).attrib["x1"])
        rows = svg.findall("s:g[@class='chart-row']", NS)
        negative, positive = [row.find("s:rect", NS) for row in rows[:2]]
        self.assertAlmostEqual(float(negative.attrib["x"]) + float(negative.attrib["width"]), axis, places=5)
        self.assertLess(float(negative.attrib["x"]), axis)
        self.assertAlmostEqual(float(positive.attrib["x"]), axis, places=5)
        self.assertAlmostEqual(float(positive.attrib["width"]), 2 * float(negative.attrib["width"]), places=5)
        zero = rows[2].find("s:circle", NS)
        self.assertAlmostEqual(float(zero.attrib["cx"]), axis)
        self.assertIsNone(rows[2].find("s:rect", NS))
        self.assertIsNone(rows[3].find("s:rect", NS))
        self.assertIsNone(rows[3].find("s:circle", NS))
        self.assertEqual([row.find("s:text[@class='chart-value']", NS).text for row in rows],
                         ["-10", "20", "0", "нет данных"])
        self.assertEqual(report, original, "Rendering must not alter the calculated report")

    def test_one_sided_series_keep_zero_at_the_correct_edge(self):
        for values, expected_axis in (([-4, -2], 720), ([2, 4], 250)):
            with self.subTest(values=values):
                svg = charts(TabularService._html(example_report(values)))[0]
                self.assertAlmostEqual(float(svg.find("s:line", NS).attrib["x1"]), expected_axis)
                self.assertEqual(len(svg.findall(".//s:rect", NS)), 2)

    def test_all_zero_and_all_missing_do_not_invent_bars_or_values(self):
        for values, expected_points in (([0, 0], 2), ([None, None], 0)):
            with self.subTest(values=values):
                svg = charts(TabularService._html(example_report(values)))[0]
                self.assertEqual(len(svg.findall(".//s:rect", NS)), 0)
                self.assertEqual(len(svg.findall(".//s:circle", NS)), expected_points)
                self.assertEqual(len(svg.findall("s:g", NS)), 2)
                self.assertTrue(math.isfinite(float(svg.find("s:line", NS).attrib["x1"])))
                labels = [element.text for element in svg.findall(".//s:text[@class='chart-value']", NS)]
                self.assertEqual(labels, ["0", "0"] if expected_points else ["нет данных", "нет данных"])

    def test_chart_for_each_metric_uses_real_values_and_escaped_units(self):
        report = example_report([12.5, -3], metrics=["c1", "c2"])
        report["table"][0]["values"]["c2"] = 99
        report["formulas"]["c2"]["unit"] = '<img src=x onerror="bad()"> & %'
        document = TabularService._html(report)
        svgs = charts(document)
        self.assertEqual(len(svgs), 2)
        self.assertEqual(svgs[0].find(".//s:text[@class='chart-value']", NS).text, "12,5")
        self.assertEqual(svgs[1].find(".//s:text[@class='chart-value']", NS).text, "99")
        self.assertIn(report["formulas"]["c2"]["unit"], svgs[1].find("s:title", NS).text)
        parser = Elements()
        parser.feed(document)
        self.assertFalse(any(tag in {"script", "img", "iframe", "link"} for tag, _ in parser.elements))
        self.assertFalse(any(key.startswith("on") or key in {"src", "href"}
                             for _, attrs in parser.elements for key in attrs))

    def test_thirty_groups_and_long_hostile_labels_stay_complete_in_titles_and_table(self):
        hostile = '<script>alert("x")</script> & ' + "длинное название " * 15
        groups = [hostile] + [f"Группа {index}" for index in range(1, 30)]
        report = example_report(list(range(30)), groups=groups)
        report["formulas"]["c1"]["label"] = '<svg onload="bad()">Показатель & значение'
        report["question"] = '<script>alert("question")</script>'
        report["sources"][0]["filename"] = '<img src="private">.csv'
        document = TabularService._html(report)
        svg = charts(document)[0]
        rows = svg.findall("s:g", NS)
        self.assertEqual(len(rows), 30)
        self.assertIn(hostile, rows[0].find("s:title", NS).text)
        self.assertEqual(rows[-1].find("s:text[@class='chart-value']", NS).text, "29")
        self.assertLessEqual(len(rows[0].findall("s:text[@class='group-label']/s:tspan", NS)), 3)
        self.assertGreater(float(svg.attrib["viewBox"].split()[-1]), 30 * 60)
        parser = Elements()
        parser.feed(document)
        self.assertEqual(sum(tag == "svg" for tag, _ in parser.elements), 1)
        self.assertFalse(any(tag in {"script", "img"} for tag, _ in parser.elements))
        self.assertIn("&lt;script&gt;", document)

    def test_extreme_opposite_values_have_finite_geometry(self):
        svg = charts(TabularService._html(example_report([-1e308, 1e308])))[0]
        for element in svg.iter():
            for attribute in ("x", "x1", "x2", "width", "height", "cx"):
                if attribute in element.attrib:
                    self.assertTrue(math.isfinite(float(element.attrib[attribute])), element.attrib)
        self.assertAlmostEqual(float(svg.find("s:line", NS).attrib["x1"]), 485)

    def test_synthetic_heading_russian_methodology_and_calculated_summary(self):
        report = example_report([1, 2])
        report["dataset"]["synthetic_data"] = True
        for operation, method in (("mean", "Среднее: сумма пригодных значений"),
                                  ("median", "Медиана: середина упорядоченного ряда"),
                                  ("sum", "Сумма: все пригодные значения складываются")):
            with self.subTest(operation=operation):
                report["plan"]["aggregation"] = operation
                document = TabularService._html(report)
                self.assertIn("Учебные или синтетические данные", document)
                self.assertIn("не являются официальными оценками населения", document)
                self.assertIn(method, document)
                self.assertIn(report["summary"][0], document)
                self.assertNotIn("sum(valid values)", document)
                self.assertIn("a" * 64, document)
                self.assertIn("b" * 64, document)
        report["dataset"]["synthetic_data"] = False
        self.assertNotIn("Учебный пример · вымышленные данные", TabularService._html(report))


if __name__ == "__main__":
    unittest.main()
