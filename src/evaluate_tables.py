"""One-pass live acceptance run against local Ollama, in isolated private storage.

python -m src.evaluate_tables --output reports/final_acceptance.json
No model download, remote endpoint, prompt tuning, or user-storage modification.
Questions and exact planning expectations live in examples/acceptance_cases.json.
The arithmetic reference below uses Decimal and does not import the app calculator.
"""
from __future__ import annotations

import argparse
import base64
import csv
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import time
from uuid import uuid4

from .model_provider import ModelProvider
from .tabular import TabularService, TableError


ROOT = Path(__file__).resolve().parents[1]


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def reference_table(raw, expected):
    """Independent, intentionally small reference for this authored numeric CSV.

    Empty string alone is missing in this fixture; every other numeric cell must
    parse as Decimal. Unknown/non-finite numeric values fail rather than being
    silently skipped. No production parsing, grouping, or aggregation is reused.
    """
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))[1:]
    group_index = None if expected["group_by"] is None else int(expected["group_by"][1:])
    groups = {}
    for row in rows:
        group = None if group_index is None else row[group_index]
        groups.setdefault(group, []).append(row)
    result = []
    for group, members in groups.items():
        values, counts, missing = {}, {}, {}
        for metric in expected["metrics"]:
            index = int(metric[1:])
            numbers = [Decimal(row[index]) for row in members if row[index] != ""]
            if any(not number.is_finite() for number in numbers):
                raise ValueError("Fixture contains a non-finite value")
            if not numbers:
                value = None
            elif expected["aggregation"] == "sum":
                value = sum(numbers, Decimal(0))
            elif expected["aggregation"] == "mean":
                value = sum(numbers, Decimal(0)) / len(numbers)
            elif expected["aggregation"] == "median":
                ordered = sorted(numbers)
                middle = len(ordered) // 2
                value = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
            else:
                raise ValueError("Unexpected reference operation")
            values[metric] = None if value is None else float(value.quantize(Decimal("0.000001")))
            counts[metric] = len(numbers)
            missing[metric] = len(members) - len(numbers)
        result.append({"group": group, "rows": len(members), "values": values,
                       "valid_counts": counts, "missing_counts": missing})
    return result


def plan_matches(plan, expected):
    for key, value in expected.items():
        if key == "calculation_allowed":
            if plan["requires_confirmation"] != value:
                return False
        elif key == "status_in":
            if plan["status"] not in value:
                return False
        elif key == "metrics":
            if set(plan[key]) != set(value):
                return False
        elif plan[key] != value:
            return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "private_outputs" / "tables-acceptance.json")
    parser.add_argument("--reference-only", action="store_true", help="Check fixture/reference without calling a model")
    parser.add_argument("--regression-of", type=Path, help="Declare a repeated regression run against a saved initial report")
    args = parser.parse_args()
    suite_path = ROOT / "examples" / "acceptance_cases.json"
    suite_raw = suite_path.read_bytes()
    suite = json.loads(suite_raw)
    source = ROOT / suite["data"]["path"]
    raw = source.read_bytes()
    references = {case["id"]: reference_table(raw, case["expected"])
                  for case in suite["cases"] if case["expected"].get("status") == "ready"}
    if args.reference_only:
        print(json.dumps(references, ensure_ascii=False, indent=2))
        return 0
    baseline = None
    if args.regression_of:
        if args.regression_of.resolve() == args.output.resolve():
            raise ValueError("A regression must not overwrite its baseline")
        baseline_raw = args.regression_of.read_bytes()
        baseline_result = json.loads(baseline_raw)
        if baseline_result["suite_sha256"] != sha256(suite_raw) or baseline_result["source_sha256"] != sha256(raw):
            raise ValueError("Regression suite or source differs from the frozen initial run")
        baseline = {"report": args.regression_of.name, "sha256": sha256(baseline_raw),
            "passed_cases": baseline_result["passed_cases"],
            "change": "Deterministic guard now asks for clarification when the aggregation is not explicitly chosen; model prompt and frozen questions unchanged.",
            "interpretation": "Repeat regression after a fix motivated by this open suite. Not independent model accuracy or a new held-out evaluation."}

    provider = ModelProvider()  # Fixed loopback-only default. No persisted API credentials.
    probe = provider.probe()
    if not probe["ok"]:
        raise RuntimeError(probe["message"])
    started_at = datetime.now(timezone.utc).isoformat()
    store = ROOT / "private_outputs" / "tables-acceptance" / uuid4().hex
    service = TabularService(store, lambda: provider)
    inspection = service.inspect({"filename": source.name, "content_base64": base64.b64encode(raw).decode()})
    dataset = service.register({"import_id": inspection["id"], "table_id": "csv:0", "header_row": 1,
        **{key: suite["data"][key] for key in ("label", "observation_unit", "period", "columns")},
        "declared_synthetic": suite["data"]["synthetic_data"]})
    result = {
        "suite": suite["suite"], "purpose": suite["purpose"], "protocol": suite["protocol"],
        "run_kind": "post_fix_open_suite_regression" if baseline else "initial_frozen_acceptance",
        "regression_of": baseline,
        "suite_frozen_at_utc": suite["frozen_at_utc"], "started_at_utc": started_at,
        "suite_sha256": sha256(suite_raw), "source_sha256": sha256(raw),
        "calculation_code_sha256": service.code_hash,
        "planner_source_sha256": sha256((ROOT / "src" / "tabular.py").read_bytes()),
        "provider_source_sha256": sha256((ROOT / "src" / "model_provider.py").read_bytes()),
        "model": provider.public_config(), "mocked": False, "synthetic_data": True,
        "execution": "Sequential direct calls to the actual application service and local model; not a browser test.",
        "reference": "Independent Decimal arithmetic over the original authored CSV; values, group sizes, valid counts and missing counts checked.",
        "limitations": ["12 open internally authored cases; not model accuracy on arbitrary questions.",
            "No independent statistician or organisational pilot; no measured employee time savings.",
            "Unsupported-method acceptance includes deterministic application guards and is not evidence of model-only refusal reliability.",
            "Durations are internal observations on one local machine, not a comparative performance benchmark.",
            "The evaluation explicitly confirms successful plans to test calculation; normal users must review their plans."],
        "cases": [],
    }
    real_propose = provider.propose
    for case in suite["cases"]:
        started = time.perf_counter()
        entry = {key: case[key] for key in ("id", "kind", "question", "expected")}
        entry["model_outputs"] = []

        def recorded_propose(*values, **options):
            proposed = real_propose(*values, **options)
            entry["model_outputs"].append(proposed)
            return proposed

        provider.propose = recorded_propose
        try:
            plan = service.plan({"dataset_id": dataset["id"], "question": case["question"]})
            entry["actual"] = {key: plan[key] for key in ("status", "reason", "metrics", "aggregation", "group_by")}
            entry["model_attempts"] = plan["model_attempts"]
            entry["validation_feedback"] = plan["validation_feedback"]
            entry["plan_matches_expected"] = plan_matches(plan, case["expected"])
            entry["calculation_executed"] = False
            if case["id"] in references:
                entry["expected_table"] = references[case["id"]]
                entry["numbers_independently_verified"] = False
                if entry["plan_matches_expected"]:
                    report = service.execute({"plan_id": plan["id"]})
                    entry["calculation_executed"] = True
                    entry["actual_table"] = report["table"]
                    entry["numbers_independently_verified"] = report["table"] == references[case["id"]]
                entry["passed"] = entry["plan_matches_expected"] and entry["numbers_independently_verified"]
            else:
                # Verify that a refused/ambiguous stored plan also cannot be executed.
                # Never execute a mismatching ready plan, even in this synthetic check.
                entry["execution_blocked"] = False
                if plan["status"] != "ready":
                    try:
                        service.execute({"plan_id": plan["id"]})
                    except TableError:
                        entry["execution_blocked"] = True
                entry["passed"] = entry["plan_matches_expected"] and entry["execution_blocked"]
        except Exception as exc:
            entry.update(passed=False, error=str(exc), error_type=type(exc).__name__)
        entry["seconds"] = round(time.perf_counter() - started, 3)
        result["cases"].append(entry)
        result["completed_cases"] = len(result["cases"])
        result["passed_cases"] = sum(item["passed"] for item in result["cases"])
        result["completed"] = len(result["cases"]) == len(suite["cases"])
        result["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"case": entry["id"], "passed": entry["passed"], "seconds": entry["seconds"],
            "status": entry.get("actual", {}).get("status"), "error": entry.get("error")}, ensure_ascii=False), flush=True)
        if sha256(suite_path.read_bytes()) != result["suite_sha256"] or sha256(source.read_bytes()) != result["source_sha256"]:
            raise RuntimeError("Frozen acceptance definitions changed during the run")
    print(f"Acceptance: {result['passed_cases']}/{result['completed_cases']}", flush=True)
    return 0 if all(case["passed"] for case in result["cases"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
