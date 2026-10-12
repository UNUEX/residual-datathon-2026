"""Service integration tests with a declared stub planner; no live model calls."""

import base64
import copy
import csv
from io import BytesIO, StringIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from src import table_calculation as calculation
from src.tabular import TabularService, TableError


READY = {"status": "ready", "reason": "Approved test aggregation", "metrics": ["c2"],
         "aggregation": "mean", "group_by": "c1"}
DATA = "ID;Industry;Employees;Notes\n0001;Manufacturing;10;PRIVATE-ROW-ALPHA\n0002;Manufacturing;30;PRIVATE-ROW-BETA\n0003;Services;20;PRIVATE-ROW-GAMMA\n0004;Services;bad;PRIVATE-ROW-DELTA\n0005;Services;;PRIVATE-ROW-EPSILON\n"


class StubPlanner:
    """Only the model transport is stubbed; imports/storage/calculation are real."""

    def __init__(self):
        self.config = {"provider": "ollama", "model": "TEST-STUB-unit-integration", "base_url": "http://127.0.0.1:11434"}
        self.answer = copy.deepcopy(READY)
        self.calls = []

    def public_config(self):
        return dict(self.config)

    def propose(self, question, prompt, schema):
        self.calls.append({"question": question, "prompt": prompt, "schema": schema})
        return copy.deepcopy(self.answer)


class TabularServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.provider = StubPlanner()
        self.service = TabularService(self.root, lambda: self.provider)

    def tearDown(self):
        self.temp.cleanup()

    def register(self, name="employment.csv", content=None, table_id="csv:0", header_row=1,
                 label="Employment", roles=None, labels=None):
        raw = DATA.encode() if content is None else content
        imported = self.service.inspect({"filename": name, "content_base64": base64.b64encode(raw).decode()})
        selection = {"import_id": imported["id"], "table_id": table_id, "header_row": header_row}
        preview = self.service.preview(selection)
        roles = roles or ["identifier", "category", "number", "ignore"]
        columns = [{"id": profile["id"], "label": (labels or {}).get(profile["id"], profile["source_name"]),
                    "role": roles[index], "unit": "people" if roles[index] == "number" else "",
                    "decimal_separator": profile["suggested_decimal_separator"]}
                   for index, profile in enumerate(preview["columns"])]
        dataset = self.service.register({**selection, "label": label, "observation_unit": "One establishment",
                                         "period": "2025", "columns": columns})
        return imported, preview, dataset

    def report(self, dataset, question="Average Employees by Industry"):
        plan = self.service.plan({"dataset_id": dataset["id"], "question": question})
        self.assertEqual(plan["status"], "ready")
        report = self.service.execute({"plan_id": plan["id"]})
        return plan, report

    def test_registration_profiles_real_values_and_requires_separate_execution(self):
        imported, preview, dataset = self.register()
        self.assertEqual(preview["row_count"], 5)
        self.assertEqual(preview["columns"][0]["sample"][0], "0001")
        self.assertEqual(dataset["columns"][2]["missing_count"], 1)
        self.assertEqual(dataset["columns"][2]["invalid_count"], 1)
        self.assertEqual(self.service.state()["reports"], [])
        plan = self.service.plan({"dataset_id": dataset["id"], "question": "Average Employees by Industry"})
        self.assertTrue(plan["requires_confirmation"])
        self.assertEqual(self.service.state()["reports"], [])
        self.assertFalse(list((self.root / "reports").glob("*.json")))
        with self.assertRaises(TableError):
            self.service.execute({"plan_id": plan["id"], "metrics": ["c0"]})
        report = self.service.execute({"plan_id": plan["id"]})
        self.assertEqual([row["values"]["c2"] for row in report["table"]], [20, 20])
        self.assertEqual(report["quality"]["invalid_numeric"], {"c2": 1})
        self.assertEqual(report["table"][1]["valid_counts"], {"c2": 1})
        self.assertEqual(report["table"][1]["missing_counts"], {"c2": 1})
        self.assertEqual(report["sources"][0]["sha256"], imported["sha256"])
        self.assertEqual(report["model_used_for"], "plan_only")
        self.assertEqual(self.service.report(report["id"]), report)
        fresh = TabularService(self.root, lambda: self.provider)
        self.assertEqual(fresh.state()["reports"][0]["id"], report["id"])
        self.assertEqual(fresh.state()["datasets"][0]["id"], dataset["id"])

    def test_prompt_contains_only_confirmed_metadata_no_samples_or_row_values(self):
        _, _, dataset = self.register(labels={"c2": "Staff"})
        self.report(dataset, "Average Staff by Industry")
        sent = json.dumps(self.provider.calls, ensure_ascii=False)
        for forbidden in ("PRIVATE-ROW", "0001", "Manufacturing", "Services", '"sample"', '"preview"', '"Notes"'):
            self.assertNotIn(forbidden, sent)
        self.assertIn("Staff", sent)
        self.assertIn("One establishment", sent)
        self.assertIn("2025", sent)
        self.assertEqual(len(self.provider.calls), 1)
        state = self.service.state()
        self.assertFalse(state["capabilities"]["python_upload"])
        self.assertIn("html", state["capabilities"]["formats"])

    def test_new_subject_and_schema_from_html_without_application_code_changes(self):
        html = b"""<html><table><caption>Overview</caption><tr><th>A</th></tr><tr><td>1</td></tr></table>
        <table><caption>Schools</caption><tr><th>School code</th><th>District</th><th>Pupils</th></tr>
        <tr><td>001</td><td>North</td><td>120</td></tr><tr><td>002</td><td>North</td><td>180</td></tr>
        <tr><td>003</td><td>South</td><td>70</td></tr></table></html>"""
        imported, _, dataset = self.register("schools.html", html, "html:1", label="Education",
                                             roles=["identifier", "category", "number"])
        self.assertEqual(len(imported["tables"]), 2)
        self.provider.answer = {**READY, "aggregation": "sum"}
        _, report = self.report(dataset, "Sum Pupils by District")
        self.assertEqual(report["dataset"]["source"]["table"], "Schools")
        self.assertEqual([row["values"]["c2"] for row in report["table"]], [300, 70])

    def test_xlsx_sheet_header_selection_and_distinct_healthcare_dataset(self):
        import openpyxl
        book = openpyxl.Workbook()
        book.active.title = "Readme"
        book.active.append(["Information"])
        book.active.append(["Choose Clinics sheet"])
        sheet = book.create_sheet("Clinics")
        sheet.append(["Survey 2025"])
        sheet.append(["Clinic code", "Region", "Visits"])
        sheet.append(["010", "West", 30])
        sheet.append(["020", "West", 10])
        sheet.append(["030", "East", 5])
        stream = BytesIO()
        book.save(stream)
        book.close()
        _, preview, dataset = self.register("clinics.xlsx", stream.getvalue(), "sheet:1", header_row=2,
                                           label="Healthcare", roles=["identifier", "category", "number"])
        self.assertEqual(preview["preview"][0][0], "010")
        self.provider.answer = {**READY, "aggregation": "median"}
        _, report = self.report(dataset, "Median Visits by Region")
        self.assertEqual([row["values"]["c2"] for row in report["table"]], [20, 5])
        self.assertEqual(report["sources"][0]["header_row"], 2)

    def test_clarification_cannot_be_executed_and_invalid_model_output_is_bounded(self):
        _, _, dataset = self.register()
        self.provider.answer = {**READY, "status": "clarify", "reason": "Choose a numeric field", "metrics": [], "group_by": "none"}
        plan = self.service.plan({"dataset_id": dataset["id"], "question": "What can we learn?"})
        self.assertFalse(plan["requires_confirmation"])
        with self.assertRaises(TableError):
            self.service.execute({"plan_id": plan["id"]})
        self.provider.answer = {**READY, "metrics": ["c0"]}
        before = len(self.provider.calls)
        with self.assertRaises(calculation.CalculationError):
            self.service.plan({"dataset_id": dataset["id"], "question": "Average Employees by Industry"})
        self.assertEqual(len(self.provider.calls) - before, 2)
        self.assertEqual(self.service.state()["reports"], [])

    def test_changed_original_file_rejected_before_preview_register_and_execute(self):
        imported, _, dataset = self.register()
        plan = self.service.plan({"dataset_id": dataset["id"], "question": "Average Employees by Industry"})
        path = self.root / "imports" / (imported["id"] + ".bin")
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaises(TableError):
            self.service.preview({"import_id": imported["id"], "table_id": "csv:0"})
        with self.assertRaises(TableError):
            self.service.register({"import_id": imported["id"], "table_id": "csv:0"})
        with self.assertRaises(TableError):
            self.service.execute({"plan_id": plan["id"]})

    def test_changed_normalized_source_and_metadata_rejected_after_plan(self):
        _, _, dataset = self.register()
        plan = self.service.plan({"dataset_id": dataset["id"], "question": "Average Employees by Industry"})
        source = self.root / "datasets" / (dataset["id"] + ".csv")
        original = source.read_bytes()
        source.write_bytes(original.replace(b",10,", b",999,"))
        with self.assertRaises(TableError):
            self.service.execute({"plan_id": plan["id"]})
        source.write_bytes(original)
        metadata = self.root / "datasets" / (dataset["id"] + ".json")
        modified = json.loads(metadata.read_text(encoding="utf-8"))
        modified["columns"][2]["unit"] = "a different unit"
        metadata.write_text(json.dumps(modified), encoding="utf-8")
        with self.assertRaises(TableError):
            self.service.execute({"plan_id": plan["id"]})

    def test_old_plan_rejected_if_calculator_version_changes(self):
        _, _, dataset = self.register()
        plan = self.service.plan({"dataset_id": dataset["id"], "question": "Average Employees by Industry"})
        self.service.code_hash = "0" * 64
        with self.assertRaises(TableError):
            self.service.execute({"plan_id": plan["id"]})

    def test_downloaded_python_reproduces_real_calculation_and_rejects_changed_source(self):
        _, _, dataset = self.register()
        _, report = self.report(dataset)
        script, mime, script_name = self.service.export(report["id"], "python")
        source, _, source_name = self.service.export(report["id"], "source")
        work = self.root / "standalone"
        work.mkdir()
        script_path, source_path = work / script_name, work / source_name
        script_path.write_bytes(script)
        source_path.write_bytes(source)
        result = subprocess.run([sys.executable, "-I", "-X", "utf8", str(script_path), str(source_path)],
                                cwd=work, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = json.loads(result.stdout)
        for key in ("table", "quality", "summary", "methodology", "formulas"):
            self.assertEqual(actual[key], report[key], key)
        self.assertEqual(actual["limitations"], report["limitations"][1:])
        source_path.write_bytes(source + b"\n")
        altered = subprocess.run([sys.executable, "-I", "-X", "utf8", str(script_path), str(source_path)],
                                 cwd=work, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertNotEqual(altered.returncode, 0)
        self.assertIn("SHA-256 mismatch", altered.stderr)

    def test_saved_code_corruption_blocks_python_download(self):
        _, _, dataset = self.register()
        _, report = self.report(dataset)
        (self.root / "reports" / (report["id"] + ".py")).write_bytes(b"print('untrusted')")
        with self.assertRaises(TableError):
            self.service.export(report["id"], "python")

    def test_python_export_treats_code_shaped_metadata_as_data(self):
        hostile = "'); __import__('pathlib').Path('unexpected.txt').write_text('ran'); #"
        _, _, dataset = self.register(labels={"c2": hostile})
        _, report = self.report(dataset, "Calculate the average by Industry")
        script, _, name = self.service.export(report["id"], "python")
        source, _, _ = self.service.export(report["id"], "source")
        (self.root / name).write_bytes(script)
        source_path = self.root / "input.csv"
        source_path.write_bytes(source)
        process = subprocess.run([sys.executable, "-I", "-X", "utf8", str(self.root / name), str(source_path)],
                                 cwd=self.root, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertFalse((self.root / "unexpected.txt").exists())
        self.assertEqual(json.loads(process.stdout)["formulas"]["c2"]["label"], hostile)

    def test_html_export_escapes_user_labels_categories_and_question(self):
        raw = 'ID;Category;Value\n001;<svg onload=alert(1)>;2\n002;Safe;4\n'.encode()
        _, _, dataset = self.register("<img onerror=alert(1)>.csv", raw, roles=["identifier", "category", "number"],
                                       label="<script>alert(2)</script>", labels={"c2": "<img onerror=alert(3)>"})
        _, report = self.report(dataset, "Calculate the average by Category")
        document, _, _ = self.service.export(report["id"], "html")
        rendered = document.decode("utf-8")
        for tag in ("<script", "<svg onload=", "<img"):
            self.assertNotIn(tag, rendered)
        for text in ("&lt;script&gt;", "&lt;svg onload=alert(1)&gt;", "&lt;img onerror=alert(3)&gt;"):
            self.assertIn(text, rendered)
        questioned = self.service._html({**report, "question": '<script>alert("question")</script>'})
        self.assertNotIn("<script", questioned)
        self.assertIn("&lt;script&gt;", questioned)

    def test_result_csv_neutralizes_formula_cells_in_labels_and_groups(self):
        raw = b"ID;Category;Value\n001;=1+1;2\n002;Safe;4\n"
        _, _, dataset = self.register(content=raw, roles=["identifier", "category", "number"], labels={"c2": "=1+1"})
        _, report = self.report(dataset, "Calculate the average by Category")
        exported, _, _ = self.service.export(report["id"], "csv")
        rows = list(csv.reader(StringIO(exported.decode("utf-8-sig"))))
        self.assertTrue(rows[0][2].startswith("'="), rows[0][2])
        self.assertTrue(rows[1][0].startswith("'="), rows[1][0])

    def test_bundled_example_keeps_synthetic_label_after_renaming_and_reload(self):
        raw = (Path(__file__).resolve().parents[1] / "examples" / "import" / "schools.csv").read_bytes()
        imported, _, dataset = self.register("renamed.csv", raw, label="Renamed by reviewer")
        self.assertTrue(imported["synthetic_data"])
        self.assertTrue(dataset["synthetic_data"])
        _, report = self.report(dataset, "Среднее «Учащиеся» по «Тип школы»")
        self.assertTrue(report["dataset"]["synthetic_data"])
        self.assertIn("Учебные вымышленные данные", report["limitations"][0])
        reloaded = TabularService(self.root, lambda: self.provider)
        self.assertTrue(reloaded.state()["datasets"][0]["synthetic_data"])
        self.assertTrue(reloaded.report(report["id"])["dataset"]["synthetic_data"])
        html, _, _ = reloaded.export(report["id"], "html")
        self.assertIn("Учебные", html.decode("utf-8"))
        _, _, custom = self.register()
        self.assertFalse(custom["synthetic_data"])

    def test_input_and_identifier_validation(self):
        for payload in ({"filename": "a.csv", "content_base64": "!not base64"},
                        {"filename": "script.py", "content_base64": base64.b64encode(b"print(1)").decode()}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.service.inspect(payload)
        for identifier in ("../secrets", "", None, "f" * 31):
            with self.subTest(identifier=identifier), self.assertRaises(TableError):
                self.service.report(identifier)


if __name__ == "__main__":
    unittest.main()
