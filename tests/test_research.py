import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from src.research import (ResearchService, ResearchError, calculate, prepare_rows,
                          validate_plan, question_guard)
from src.model_provider import ModelProvider, ProviderError, validate_config


HOUSING = "NOMER;OB_PL;J_PL;KOL_K\n001;60;30;2\n002;100;110;4\n003;90;45;3\n004;40;20;0\n005;70;40;2\n"
ROSTER = "NOMER;NOMP;KOL_CHL\n001;1;1\n002;1;2\n002;2;2\n003;1;3\n003;2;3\n003;3;3\n004;1;1\n005;1;2\n999;1;1\n"
READY = {"status": "ready", "reason": "Медиана площади на человека по группам домохозяйств.",
         "year": 2024, "metrics": ["area_per_person"], "aggregation": "median", "grouping": "household_size"}
QUESTION = "Сравни медианную площадь на человека по группам размера домохозяйства за 2024 год"


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(__file__).resolve().parents[1]
        self.service = ResearchService(self.repo, storage_root=self.temp.name)
        self.dataset = self.service.upload({"housing_csv": HOUSING, "roster_csv": ROSTER})

    def tearDown(self):
        self.temp.cleanup()

    def make_report(self):
        with patch.object(ModelProvider, "propose", return_value=READY):
            plan = self.service.plan({"dataset_id": self.dataset["id"], "question": QUESTION})
        return self.service.execute({"plan_id": plan["id"]})

    def test_actual_calculation_quality_and_sensitivity(self):
        report = self.make_report()
        self.assertEqual(report["table"][0]["values"]["area_per_person"], 55)
        self.assertEqual(report["table"][1]["values"]["area_per_person"], 30)
        self.assertEqual(report["sensitivity"]["table"][0]["values"]["area_per_person"], 60)
        self.assertEqual(report["quality"]["invalid_numeric_excluded"], 1)
        self.assertEqual(report["quality"]["roster_mismatch_excluded"], 1)
        self.assertEqual(report["quality"]["unused_roster_households"], 1)
        self.assertEqual(report["quality"]["area_order"], 1)
        json.dumps(report, allow_nan=False)

    def test_changed_input_changes_result(self):
        original = self.make_report()
        changed = self.service.upload({"housing_csv": HOUSING.replace("001;60;30", "001;80;30"), "roster_csv": ROSTER})
        self.dataset = changed
        revised = self.make_report()
        self.assertEqual(revised["table"][0]["values"]["area_per_person"], 65)
        self.assertNotEqual(original["dataset"]["fingerprint"], revised["dataset"]["fingerprint"])
        self.assertEqual(self.service.report(original["id"])["table"], original["table"])

    def test_model_gets_no_rows_and_execution_requires_stored_plan(self):
        with patch.object(ModelProvider, "propose", return_value=READY) as call:
            plan = self.service.plan({"dataset_id": self.dataset["id"], "question": QUESTION})
        self.assertNotIn("001;60;30", str(call.call_args))
        self.assertNotIn("housing_rows", str(call.call_args))
        with self.assertRaises(ResearchError):
            self.service.execute({"plan_id": plan["id"], "metrics": ["rooms"]})
        self.assertEqual(len(self.service.state()["reports"]), 0)

    def test_no_fake_model_fallback_and_unsupported_plan_cannot_run(self):
        with patch.object(ModelProvider, "propose", side_effect=ProviderError("offline")):
            with self.assertRaises(ProviderError):
                self.service.plan({"dataset_id": self.dataset["id"], "question": QUESTION})
        self.assertEqual(self.service.state()["plans"], [])
        with patch.object(ModelProvider, "propose", return_value=READY):
            plan = self.service.plan({"dataset_id": self.dataset["id"], "question": "Сравни площадь и доход за 2023 год"})
        self.assertEqual(plan["status"], "unsupported")
        with self.assertRaises(ResearchError):
            self.service.execute({"plan_id": plan["id"]})

    def test_model_plan_schema_and_semantic_guards(self):
        for change in ({"year": 2023}, {"code": "print(1)"}, {"status": []}, {"metrics": ["income"]}, {"metrics": []}):
            with self.subTest(change=change), self.assertRaises(ResearchError):
                validate_plan({**READY, **change})
        with self.assertRaises(ResearchError):
            question_guard(QUESTION, {**READY, "metrics": ["total_area"]})
        with self.assertRaises(ResearchError):
            question_guard(QUESTION, {**READY, "aggregation": "mean"})
        self.assertEqual(question_guard("What is the average total area?", {**READY, "aggregation": "mean", "metrics": ["total_area"], "grouping": "all"})["status"], "ready")
        self.assertEqual(question_guard("Рассчитай среднее и медиану общей площади", READY)["status"], "unsupported")
        self.assertEqual(question_guard("Площадь только у домохозяйств с четырьмя комнатами", READY)["status"], "unsupported")
        self.assertEqual(question_guard("Площадь только среди семей из трех человек", READY)["status"], "unsupported")
        with self.assertRaises(ResearchError):
            question_guard("Покажи медиану общей площади и площади на человека за 2024 год", {**READY, "metrics": ["area_per_person"]})
        self.assertEqual(question_guard("Медиана общей площади на человека", {**READY, "metrics": ["area_per_person"]})["status"], "ready")

    def test_bounded_model_correction_never_calculates_or_fills_in_plan(self):
        incorrect = {**READY, "metrics": ["rooms"]}
        with patch.object(ModelProvider, "propose", side_effect=[incorrect, READY]) as call:
            plan = self.service.plan({"dataset_id": self.dataset["id"], "question": QUESTION})
        self.assertEqual(call.call_count, 2)
        self.assertIn("area_per_person", call.call_args.args[1])
        self.assertEqual(plan["metrics"], READY["metrics"])
        self.assertEqual(plan["model_attempts"], 2)
        self.assertEqual(len(plan["validation_feedback"]), 1)
        self.assertEqual(self.service.state()["reports"], [])
        with patch.object(ModelProvider, "propose", return_value=incorrect) as call:
            with self.assertRaises(ResearchError):
                self.service.plan({"dataset_id": self.dataset["id"], "question": QUESTION})
        self.assertEqual(call.call_count, 2)

    def test_vague_question_does_not_authorize_an_invented_analysis(self):
        vague = question_guard("Есть что-то интересное?", READY)
        self.assertEqual(vague["status"], "clarify")
        self.assertEqual(vague["metrics"], [])
        self.assertEqual(question_guard("Что-нибудь интересное о площади на человека", READY)["status"], "ready")
        self.assertEqual(question_guard("Интересна медиана квадратных метров на одного члена домохозяйства", READY)["status"], "ready")
        self.assertEqual(question_guard("Что-нибудь о квадратных метрах на одного члена домохозяйства", READY)["status"], "ready")
        with self.assertRaisesRegex(ResearchError, "area_per_person"):
            question_guard("Бір адамға шаққандағы тұрғын үй ауданының медианасы", {**READY, "metrics": ["living_share"]})

    def test_malformed_quoted_csv_is_rejected_without_repairing_values(self):
        for value in ('"6"0', '"60'):
            with self.subTest(value=value), self.assertRaisesRegex(ResearchError, "повреждён"):
                self.service.upload({'housing_csv': 'NOMER,OB_PL,J_PL,KOL_K\n001,' + value + ',30,2\n',
                                     'roster_csv': 'NOMER,NOMP\n001,1\n'})

    def test_source_snapshot_tampering_and_path_traversal_blocked(self):
        with patch.object(ModelProvider, "propose", return_value=READY):
            plan = self.service.plan({"dataset_id": self.dataset["id"], "question": QUESTION})
        source = self.service.root / "datasets" / self.dataset["id"] / "d006.csv"
        source.write_text(HOUSING.replace("60", "61"), encoding="utf-8")
        with self.assertRaisesRegex(ResearchError, "изменился"):
            self.service.execute({"plan_id": plan["id"]})
        with self.assertRaises(ResearchError):
            self.service.report("../../provider")

    def test_secret_memory_only_and_restart_loses_key(self):
        config = {"provider": "openai_compatible", "base_url": "https://example.com/v1", "model": "test",
                  "api_key": "PRIVATE-SECRET", "allow_external": True}
        self.assertTrue(self.service.configure(config)["api_key_set"])
        self.assertNotIn("PRIVATE-SECRET", json.dumps(self.service.state()))
        self.assertNotIn("PRIVATE-SECRET", (self.service.root / "provider.json").read_text())
        restarted = ResearchService(self.repo, self.temp.name)
        self.assertFalse(restarted.state()["provider"]["api_key_set"])

    def test_exports_reproduce_bom_crlf_and_html_escaped(self):
        self.dataset = self.service.upload({"housing_csv": "\ufeff" + HOUSING.replace("\n", "\r\n"),
                                            "roster_csv": ROSTER, "label": "<script>alert(1)</script>"})
        report = self.make_report()
        raw, _, _ = self.service.export(report["id"], "html")
        self.assertNotIn(b"<script>", raw)
        self.assertIn(b"<svg", raw)
        csv_bytes, _, _ = self.service.export(report["id"], "csv")
        self.assertIn(b"exclude_area_order", csv_bytes)
        script, _, _ = self.service.export(report["id"], "python")
        script_path = Path(self.temp.name) / "reproduce.py"
        script_path.write_bytes(script)
        h, r = Path(self.temp.name) / "h.csv", Path(self.temp.name) / "r.csv"
        h.write_bytes(("\ufeff" + HOUSING.replace("\n", "\r\n")).encode("utf-8"))
        r.write_text(ROSTER, encoding="utf-8")
        # Execute in a subprocess with the repository explicitly on the import path.
        import os
        env = dict(os.environ, PYTHONPATH=str(self.repo))
        result = subprocess.run([sys.executable, "-X", "utf8", str(script_path), str(h), str(r)],
                                cwd=self.repo, env=env, capture_output=True, text=True, encoding="utf-8", check=True)
        self.assertEqual(json.loads(result.stdout)["table"], report["table"])
        script_path.write_bytes(script.replace(report['calculation_code_sha256'].encode(), b'0' * 64))
        mismatch = subprocess.run([sys.executable, "-X", "utf8", str(script_path), str(h), str(r)],
                                  cwd=self.repo, env=env, capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(mismatch.returncode, 0)
        self.assertIn('Calculation code version changed', mismatch.stderr)

    def test_duplicate_missing_and_nonfinite_inputs(self):
        with self.assertRaises(ResearchError):
            prepare_rows(HOUSING + "001;10;5;1\n", ROSTER)
        with self.assertRaises(ResearchError):
            prepare_rows(HOUSING, ROSTER + "001;1;1\n")
        with self.assertRaises(ResearchError):
            prepare_rows("NOMER,OB_PL,J_PL,KOL_K\nx,NaN,1,1\n", "NOMER,NOMP\nx,1\n")
        with self.assertRaises(ResearchError):
            self.service.upload({"housing_csv": HOUSING, "roster_csv": ROSTER, "year": 2023})


class ProviderTests(unittest.TestCase):
    def test_external_opt_in_and_safe_url(self):
        valid = {"provider": "openai_compatible", "model": "test", "base_url": "https://example.com/v1", "allow_external": True}
        self.assertFalse(validate_config(valid)["is_local"])
        for change in ({"allow_external": False}, {"base_url": "http://example.com/v1"},
                       {"base_url": "https://user:password@example.com/v1"},
                       {"base_url": "https://example.com/v1?key=secret"}, {"provider": "ollama"}):
            with self.subTest(change=change), self.assertRaises(ProviderError):
                validate_config({**valid, **change})

    def test_ollama_and_compatible_payloads_are_real_structured_calls(self):
        from src.research import SYSTEM_PROMPT, PLAN_SCHEMA
        provider = ModelProvider()
        with patch.object(provider, "_request", side_effect=[
                {"version": "0.40.3"}, {"models": [{"name": "qwen3:1.7b"}]},
                {"details": {"format": "gguf"}, "model_info": {}},
                {"message": {"content": json.dumps(READY)}}]) as call:
            self.assertEqual(provider.propose(QUESTION, SYSTEM_PROMPT, PLAN_SCHEMA), READY)
        self.assertEqual([args.args[0] for args in call.call_args_list],
                         ["/api/version", "/api/tags", "/api/show", "/api/chat"])
        self.assertEqual(call.call_args_list[2].args[1], {"model": "qwen3:1.7b:local"})
        endpoint, body = call.call_args.args
        self.assertEqual(endpoint, "/api/chat")
        self.assertEqual(body["model"], "qwen3:1.7b:local")
        self.assertIs(body["stream"], False)
        self.assertIs(body["think"], False)
        self.assertEqual(body["format"], PLAN_SCHEMA)
        provider = ModelProvider({"provider": "openai_compatible", "base_url": "http://localhost:1234/v1", "model": "test"})
        with patch.object(provider, "_request", return_value={"choices": [{"message": {"content": json.dumps(READY)}}]}) as call:
            self.assertEqual(provider.propose(QUESTION, SYSTEM_PROMPT, PLAN_SCHEMA), READY)
        self.assertEqual(call.call_args.args[0], "/chat/completions")
        self.assertEqual(call.call_args.args[1]["response_format"]["type"], "json_schema")

    def test_cloud_sources_rejected_but_local_alias_names_allowed(self):
        base = {"provider": "ollama", "base_url": "http://127.0.0.1:11434"}
        for name in ("gpt-oss:cloud", "qwen3:480b-cloud", "x:CLOUD", "x:local:cloud", "x:cloud:local"):
            with self.subTest(name=name), self.assertRaises(ProviderError):
                validate_config({**base, "model": name})
        for name in ("my-cloud-notes:latest", "private-alias:local", "registry.example:5000/team/model:8b"):
            with self.subTest(name=name):
                self.assertEqual(validate_config({**base, "model": name})["model"], name)

    def test_remote_alias_metadata_blocks_before_question_is_sent(self):
        provider = ModelProvider({"provider": "ollama", "base_url": "http://localhost:11434", "model": "office"})
        for remote in ({"remote_host": "https://ollama.com"}, {"remote_model": "cloud-upstream"}):
            with self.subTest(remote=remote), patch.object(provider, "_request", side_effect=[
                    {"version": "0.40.3"}, {"models": [{"name": "office:latest", **remote}]}]) as call:
                with self.assertRaisesRegex(ProviderError, "удалённую"):
                    provider.propose("PRIVATE QUESTION", "catalog", {})
                self.assertEqual(call.call_count, 2)
                self.assertNotIn("PRIVATE QUESTION", str(call.call_args_list))
        with patch.object(provider, "_request", side_effect=[
                {"version": "0.40.3"}, {"models": [{"name": "office:latest"}]},
                {"details": {}, "model_info": {}, "remote_host": "https://ollama.com"}]) as call:
            with self.assertRaisesRegex(ProviderError, "удалённую"):
                provider.propose("PRIVATE QUESTION", "catalog", {})
            self.assertEqual(call.call_count, 3)
            self.assertNotIn("PRIVATE QUESTION", str(call.call_args_list))

    def test_probe_only_confirms_verified_local_model_and_filters_remote_aliases(self):
        provider = ModelProvider()
        with patch.object(provider, "_request", side_effect=[
                {"models": [{"name": "qwen3:1.7b"}, {"name": "office:latest", "remote_model": "upstream"},
                            {"name": "other:cloud"}, {"name": "my-cloud-notes:latest"}]},
                {"version": "0.40.3"}, {"details": {"format": "safetensors"}, "model_info": {}}]) as call:
            result = provider.probe()
        self.assertTrue(result["ok"])
        self.assertTrue(result["local_inference_verified"])
        self.assertEqual(result["models"], ["qwen3:1.7b", "my-cloud-notes:latest"])
        self.assertEqual(call.call_args.args, ("/api/show", {"model": "qwen3:1.7b:local"}))

    def test_old_ollama_cannot_fallback_to_unqualified_model(self):
        provider = ModelProvider()
        with patch.object(provider, "_request", return_value={"version": "0.17.7"}) as call:
            with self.assertRaisesRegex(ProviderError, "0.40.3"):
                provider.propose("PRIVATE QUESTION", "catalog", {})
        self.assertEqual(call.call_count, 1)
        self.assertNotIn("PRIVATE QUESTION", str(call.call_args_list))
        with patch.object(provider, "_request", side_effect=[{"models": []}, {"version": "0.17.7"}]):
            result = provider.probe()
        self.assertFalse(result["ok"])
        self.assertFalse(result["local_inference_verified"])
        self.assertIn("0.40.3", result["message"])

    def test_missing_or_incomplete_model_metadata_never_sends_chat(self):
        provider = ModelProvider()
        for response in (ProviderError("Модель не найдена"), {}):
            with self.subTest(response=response), patch.object(provider, "_request", side_effect=[
                    {"version": "0.40.3"}, {"models": []}, response]) as call:
                with self.assertRaises(ProviderError):
                    provider.propose("PRIVATE QUESTION", "catalog", {})
                self.assertEqual(call.call_count, 3)
                self.assertNotIn("PRIVATE QUESTION", str(call.call_args_list))
        with patch.object(provider, "_request", side_effect=[
                {"models": []}, {"version": "0.40.3"}, ProviderError("Модель ещё не загружена")]):
            self.assertFalse(provider.probe()["ok"])

    def test_unexpected_remote_chat_response_is_not_accepted(self):
        provider = ModelProvider()
        with patch.object(provider, "_request", side_effect=[
                {"version": "0.40.3"}, {"models": []}, {"details": {}, "model_info": {}},
                {"message": {"content": json.dumps(READY)}, "remote_host": "https://ollama.com"}]):
            with self.assertRaisesRegex(ProviderError, "удалённом выполнении"):
                provider.propose(QUESTION, "catalog", {})


if __name__ == "__main__":
    unittest.main()
