"""Real local HTTP flow; only the model planner is explicitly stubbed."""

import base64
import csv
from io import StringIO
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from src.model_provider import ProviderError
from src.serve import Handler, ROOT, make_server
from src.table_import import inspect_file


CSV = "ID,Category,Value,Private note\n0001,North,10,PRIVATE-HTTP-ROW-ONE\n0002,North,30,PRIVATE-HTTP-ROW-TWO\n0003,South,5,PRIVATE-HTTP-ROW-THREE\n"
CANDIDATE = {"status": "ready", "reason": "HTTP test plan", "metrics": ["c2"], "aggregation": "mean", "group_by": "c1"}


class TabularHTTPTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.log_patch = patch.object(Handler, "log_message", return_value=None)
        self.log_patch.start()
        self.start_server()

    def start_server(self):
        self.server = make_server(0, Path(self.folder.name))
        self.server.research.provider.config["model"] = "TEST-STUB-http-integration"
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=2)

    def tearDown(self):
        self.stop_server()
        self.log_patch.stop()
        self.folder.cleanup()

    def call(self, path, payload=None, headers=None, raw=None):
        supplied_headers = dict(headers or {})
        if payload is not None:
            raw = json.dumps(payload).encode("utf-8")
            supplied_headers.setdefault("Content-Type", "application/json")
        request = Request(self.base + path, data=raw, headers=supplied_headers)
        with urlopen(request, timeout=15) as response:
            data = response.read()
            result = json.loads(data) if response.headers["Content-Type"].startswith("application/json") else data
            return result, dict(response.headers)

    def upload_and_register(self):
        imported, _ = self.call("/api/tables/inspect", {"filename": "sample.csv", "content_base64": base64.b64encode(CSV.encode()).decode()})
        selection = {"import_id": imported["id"], "table_id": "csv:0", "header_row": 1}
        preview, _ = self.call("/api/tables/preview", selection)
        roles = ["identifier", "category", "number", "ignore"]
        columns = [{"id": item["id"], "label": item["source_name"], "role": roles[index],
                    "unit": "units" if roles[index] == "number" else "", "decimal_separator": "."}
                   for index, item in enumerate(preview["columns"])]
        dataset, _ = self.call("/api/tables/register", {**selection, "label": "HTTP test data",
            "observation_unit": "One record", "period": "2025", "columns": columns})
        return imported, preview, dataset

    def make_report(self, dataset):
        with patch.object(self.server.research.provider, "propose", return_value=dict(CANDIDATE)) as model:
            plan, _ = self.call("/api/tables/plan", {"dataset_id": dataset["id"], "question": "Average Value by Category"})
        report, _ = self.call("/api/tables/execute", {"plan_id": plan["id"]})
        return plan, report, model

    def test_home_housing_static_assets_and_real_example_downloads(self):
        home, headers = self.call("/")
        self.assertEqual(home, (ROOT / "app" / "tables.html").read_bytes())
        self.assertIn(b'/tables.js', home)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        housing, _ = self.call("/housing/")
        self.assertEqual(housing, (ROOT / "app" / "index.html").read_bytes())
        self.assertIn(b'/research.js', housing)
        for asset, mime in [("/tables.js", "text/javascript"), ("/tables.css", "text/css")]:
            with self.subTest(asset=asset):
                body, response_headers = self.call(asset)
                self.assertGreater(len(body), 100)
                self.assertIn(mime, response_headers["Content-Type"])
        for filename, format_name in [("schools.xlsx", "xlsx"), ("schools.csv", "csv"), ("budget.html", "html")]:
            with self.subTest(filename=filename):
                raw, response_headers = self.call("/examples/import/" + filename)
                self.assertEqual(raw, (ROOT / "examples" / "import" / filename).read_bytes())
                self.assertIn('attachment; filename="' + filename + '"', response_headers["Content-Disposition"])
                inspection = inspect_file(filename, raw)
                self.assertEqual(inspection["format"], format_name)
                self.assertGreater(len(inspection["tables"]), 0)

    def test_full_http_import_confirm_plan_execute_and_five_exports(self):
        initial, _ = self.call("/api/tables/state")
        self.assertEqual(initial["datasets"], [])
        self.assertEqual(initial["reports"], [])
        imported, preview, dataset = self.upload_and_register()
        self.assertEqual(preview["row_count"], 3)
        self.assertEqual(preview["preview"][0][0], "0001")
        with patch.object(self.server.research.provider, "propose", return_value=dict(CANDIDATE)) as model:
            plan, _ = self.call("/api/tables/plan", {"dataset_id": dataset["id"], "question": "Average Value by Category"})
        self.assertEqual(plan["model"], "TEST-STUB-http-integration")
        self.assertTrue(plan["requires_confirmation"])
        self.assertEqual(self.call("/api/tables/state")[0]["reports"], [])
        self.assertNotIn("PRIVATE-HTTP", str(model.call_args))
        self.assertNotIn("North", str(model.call_args))
        report, _ = self.call("/api/tables/execute", {"plan_id": plan["id"]})
        self.assertEqual([row["values"]["c2"] for row in report["table"]], [20, 5])
        self.assertEqual(report["sources"][0]["sha256"], imported["sha256"])
        stored, _ = self.call("/api/tables/report?id=" + report["id"])
        self.assertEqual(stored, report)
        for format_name, mime in [("html", "text/html"), ("csv", "text/csv"),
                                  ("json", "application/json"), ("python", "text/x-python"), ("source", "text/csv")]:
            with self.subTest(format_name=format_name):
                result, headers = self.call("/api/tables/export?id=" + report["id"] + "&format=" + format_name)
                self.assertIn(mime, headers["Content-Type"])
                self.assertIn("attachment;", headers["Content-Disposition"])
                if format_name == "json":
                    self.assertEqual(result, report)
                else:
                    self.assertGreater(len(result), 20)
                if format_name == "source":
                    rows = list(csv.reader(StringIO(result.decode("utf-8"))))
                    self.assertEqual(rows[0], ["c0", "c1", "c2", "c3"])
                    self.assertEqual(rows[1][0], "0001")
                if format_name == "python":
                    self.assertIn(b"Source SHA-256 mismatch", result)
        state, _ = self.call("/api/tables/state")
        self.assertEqual(len(state["datasets"]), 1)
        self.assertEqual(len(state["reports"]), 1)
        serialized = json.dumps(state)
        for private_value in ("PRIVATE-HTTP", "0001", "North", "South"):
            self.assertNotIn(private_value, serialized)
        self.stop_server()
        self.start_server()
        reopened, _ = self.call("/api/tables/state")
        self.assertEqual(reopened["datasets"][0]["id"], dataset["id"])
        self.assertEqual(reopened["reports"][0]["id"], report["id"])
        self.assertEqual(self.call("/api/tables/report?id=" + report["id"])[0], report)

    def test_cross_origin_host_path_and_arbitrary_execution_rejected(self):
        cases = [
            ("/api/tables/inspect", {}, {"Origin": "https://elsewhere.example"}, 403),
            ("/api/tables/state", None, {"Host": "attacker.example"}, 403),
            ("/api/tables/report?id=../../secrets", None, {}, 400),
            ("/api/tables/export?id=..%2F..%2Fsecrets&format=source", None, {}, 400),
            ("/api/tables/preview", {"import_id": "../../secrets", "table_id": "csv:0"}, {}, 400),
            ("/api/tables/execute", {"plan_id": "a" * 32, "code": "print(1)"}, {}, 400),
            ("/../README.md", None, {}, 404),
            ("/api/tables/unknown", {}, {}, 404),
        ]
        for endpoint, payload, headers, expected in cases:
            with self.subTest(endpoint=endpoint), self.assertRaises(HTTPError) as caught:
                self.call(endpoint, payload, headers)
            self.assertEqual(caught.exception.code, expected)
            self.assertIn("error", json.loads(caught.exception.read()))

    def test_python_upload_and_malformed_json_are_explicit_client_errors(self):
        with self.assertRaises(HTTPError) as caught:
            self.call("/api/tables/inspect", {"filename": "analysis.py", "content_base64": base64.b64encode(b"print('must not run')").decode()})
        self.assertEqual(caught.exception.code, 400)
        self.assertIn("Python", json.loads(caught.exception.read())["error"])
        for raw in (b"{", b"[]", b"null"):
            with self.subTest(raw=raw), self.assertRaises(HTTPError) as caught:
                self.call("/api/tables/inspect", headers={"Content-Type": "application/json"}, raw=raw)
            self.assertEqual(caught.exception.code, 400)
        with self.assertRaises(HTTPError) as caught:
            self.call("/api/tables/inspect", headers={"Content-Type": "text/plain"}, raw=b"{}")
        self.assertEqual(caught.exception.code, 415)
        self.assertEqual(self.call("/api/tables/state")[0]["datasets"], [])

    def test_model_failure_returns_502_without_fabricated_plan_or_report(self):
        _, _, dataset = self.upload_and_register()
        with patch.object(self.server.research.provider, "propose", side_effect=ProviderError("Test model is unavailable")):
            with self.assertRaises(HTTPError) as caught:
                self.call("/api/tables/plan", {"dataset_id": dataset["id"], "question": "Average Value by Category"})
        self.assertEqual(caught.exception.code, 502)
        self.assertEqual(json.loads(caught.exception.read())["error"], "Test model is unavailable")
        self.assertEqual(self.call("/api/tables/state")[0]["reports"], [])
        self.assertFalse(list((Path(self.folder.name) / "private_outputs" / "tabular" / "plans").glob("*.json")))


if __name__ == "__main__":
    unittest.main()
