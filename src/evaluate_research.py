"""Run the published research scenarios against the configured real model.

Uses an isolated evaluation folder, never writes into the user's dataset/history.
Run: python -m src.evaluate_research [--case 01] [--output PATH]
Results are technical regression evidence, not an independent validation study.
"""
import argparse
import json
from pathlib import Path
import time

from .research import ResearchService
from .model_provider import ModelProvider

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', action='append', dest='selected')
    parser.add_argument('--output', type=Path, default=ROOT / 'private_outputs' / 'research-evaluation.json')
    args = parser.parse_args()
    suite = json.loads((ROOT / 'examples/research_questions.json').read_text(encoding='utf-8'))
    app = ResearchService(ROOT, ROOT / 'private_outputs' / 'research-evaluation')
    config = ROOT / 'private_outputs/research/provider.json'
    if config.exists():
        app.provider = ModelProvider(json.loads(config.read_text(encoding='utf-8')))
    dataset = app.load_demo()
    real_propose = app.provider.propose
    results = []
    for case in suite['cases']:
        if args.selected and case['id'] not in args.selected:
            continue
        started = time.perf_counter()
        entry = {'id': case['id'], 'question': case['question'], 'expected': case['expect']}
        entry['model_outputs'] = []
        def recorded_propose(*values, **options):
            proposed = real_propose(*values, **options)
            entry['model_outputs'].append(proposed)
            return proposed
        app.provider.propose = recorded_propose
        try:
            plan = app.plan({'dataset_id': dataset['id'], 'question': case['question']})
            expected = case['expect']
            checks = []
            for key, value in expected.items():
                if key == 'status_in':
                    checks.append(plan['status'] in value)
                elif key == 'metrics':
                    checks.append(set(plan[key]) == set(value))
                else:
                    checks.append(plan[key] == value)
            entry.update(passed=all(checks), actual={k: plan[k] for k in ('status', 'reason', 'metrics', 'aggregation', 'grouping')})
            if plan['status'] == 'ready':
                report = app.execute({'plan_id': plan['id']})
                entry['table'] = report['table']
                entry['report_id'] = report['id']
        except Exception as exc:
            entry.update(passed=False, error=str(exc))
        entry['seconds'] = round(time.perf_counter() - started, 2)
        results.append(entry)
        output = {'purpose': suite['purpose'], 'model': app.provider.public_config(),
                  'total': len(results), 'passed': sum(r['passed'] for r in results), 'cases': results}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'case': entry['id'], 'passed': entry['passed'], 'seconds': entry['seconds'],
                          'error': entry.get('error')}, ensure_ascii=False), flush=True)
    print(f"Passed {sum(r['passed'] for r in results)}/{len(results)}. Results: {args.output}", flush=True)
    return 0 if results and all(r['passed'] for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
