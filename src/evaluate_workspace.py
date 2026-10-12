"""Equal-budget stress test of the current fit-on-package review pipeline.

Definitions are fixed in docs/benchmark_protocol.txt. Known alterations are not
adjudicated errors; this module never writes the source microdata or queue policy.
"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import platform
import time

import numpy as np
import pandas as pd
import sklearn

from .workspace import analyze_package

SEEDS = (17, 29, 43)
BASE_BUDGET = 250
TOTAL_BUDGET = 300
ALTERATION_FRACTION_PER_TYPE = .01
SOFT_SIGNALS = {'ROBUST_OUTLIER', 'IFOREST_CANDIDATE'}
SCENARIOS = {
    'gross': ('living_exceeds_total', 'area_unit_x10', 'rooms_x8'),
    'moderate': ('area_scale_x1_4', 'rooms_plus_one', 'living_share_shift'),
}
METHODS = ('robust_300', 'base_250_ml_50', 'base_250_random_50')


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def select_queues(package, seed, base_budget=BASE_BUDGET, total_budget=TOTAL_BUDGET):
    """Reconstruct the engine ordering from exact internal scores, equally capped."""
    if not 0 < base_budget < total_budget:
        raise ValueError('Budgets must satisfy 0 < base < total')
    records = package['all_records_by_id']
    priority = [key for key, row in records.items() if set(row['signal_ids']) - SOFT_SIGNALS]
    priority.sort(key=lambda key: ('AREA_ORDER' not in records[key]['signal_ids'], key))
    if len(priority) > base_budget:
        raise ValueError('Explicit signals exceed common base budget; do not truncate rules')
    eligible = [key for key, row in records.items() if row.get('robust_score') is not None]
    if len(priority) + len(eligible) < total_budget:
        raise ValueError('Insufficient records to compare equal full review budgets')
    robust = sorted(eligible, key=lambda key: (-records[key]['robust_score'], key))
    if any(records[key].get('ml_score') is None for key in eligible):
        raise ValueError('ML must be enabled for equal-budget comparison')
    ml = sorted(eligible, key=lambda key: (-records[key]['ml_score'], key))
    base = priority + robust[:base_budget - len(priority)]
    base_set = set(base)
    extra = total_budget - base_budget
    robust_extra = [key for key in robust if key not in base_set][:extra]
    ml_extra = [key for key in ml if key not in base_set][:extra]
    remaining = sorted(key for key in eligible if key not in base_set)
    random_extra = np.random.default_rng(seed + 10000).choice(remaining, extra, replace=False).tolist()
    additions = dict(zip(METHODS, (robust_extra, ml_extra, random_extra)))
    queues = {method: base + additions[method] for method in METHODS}
    for queue in queues.values():
        if len(queue) != total_budget or len(set(queue)) != total_budget:
            raise AssertionError('Review budgets must be equal with no duplicates')
        if not set(priority).issubset(queue):
            raise AssertionError('Every method must retain all explicit signals')
    return dict(base=base, priority=priority, additions=additions, queues=queues)


def alter_package(housing_text, eligible_ids, regime, seed):
    """Mutate disjoint eligible rows in a CSV copy before fitting; return known labels."""
    if regime not in SCENARIOS:
        raise ValueError('Unknown preset regime')
    reader = csv.DictReader(io.StringIO(housing_text.lstrip('\ufeff')))
    fieldnames = reader.fieldnames
    rows = list(reader)
    by_id = {row['NOMER']: row for row in rows}
    eligible_ids = sorted(eligible_ids)
    count = int(len(eligible_ids) * ALTERATION_FRACTION_PER_TYPE)
    if count < 1:
        raise ValueError('Need at least 100 eligible original records')
    selected = np.random.default_rng(seed).choice(eligible_ids, count * 3, replace=False).tolist()
    labels = {}
    for offset, scenario in enumerate(SCENARIOS[regime]):
        for key in selected[offset * count:(offset + 1) * count]:
            row = by_id[key]
            total, living, rooms = (float(row[field]) for field in ('OB_PL', 'J_PL', 'KOL_K'))
            if scenario == 'living_exceeds_total':
                row['J_PL'] = repr(total * 1.25)
            elif scenario in ('area_unit_x10', 'area_scale_x1_4'):
                factor = 10 if scenario == 'area_unit_x10' else 1.4
                row['OB_PL'], row['J_PL'] = repr(total * factor), repr(living * factor)
            elif scenario == 'rooms_x8':
                row['KOL_K'] = repr(rooms * 8)
            elif scenario == 'rooms_plus_one':
                row['KOL_K'] = repr(rooms + 1)
            else:
                share = living / total
                row['J_PL'] = repr(total * (share + .15 if share <= .65 else share - .15))
            labels[key] = scenario
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue(), labels


def measure(selected, labels, regime, seed):
    types = SCENARIOS[regime]
    known = set(labels)
    base_found = len(set(selected['base']) & known)
    extra_sets = {method: set(ids) for method, ids in selected['additions'].items()}
    rows = []
    for method in METHODS:
        reviewed = set(selected['queues'][method])
        found = reviewed & known
        supplementary = extra_sets[method]
        rows.append(dict(regime=regime, seed=seed, method=method,
            reviewed=len(reviewed), explicit_reviewed=len(selected['priority']),
            base_reviewed=len(selected['base']), supplemental_reviewed=len(supplementary),
            altered=len(labels), found=len(found), recall=len(found) / len(labels),
            alteration_precision=len(found) / len(reviewed), unchanged_reviewed=len(reviewed - known),
            base_found=base_found, supplemental_found=len(supplementary & known),
            by_scenario={kind: dict(altered=sum(v == kind for v in labels.values()),
                found=sum(labels[key] == kind for key in found)) for kind in types},
            unique_supplemental_vs={other: len((supplementary - extra_sets[other]) & known)
                for other in METHODS if other != method},
            supplemental_overlap={other: len(supplementary & extra_sets[other])
                for other in METHODS if other != method}))
    ml, robust, random = rows[1], rows[0], rows[2]
    marginal = dict(regime=regime, seed=seed, base_found=base_found,
        ml_found=ml['supplemental_found'], next_robust_found=robust['supplemental_found'],
        random_found=random['supplemental_found'],
        ml_minus_next_robust=ml['supplemental_found'] - robust['supplemental_found'],
        ml_minus_random=ml['supplemental_found'] - random['supplemental_found'])
    return rows, marginal


def summarize(rows):
    summary = []
    for regime in SCENARIOS:
        for method in METHODS:
            parts = [row for row in rows if row['regime'] == regime and row['method'] == method]
            summary.append(dict(regime=regime, method=method, runs=len(parts),
                found_mean=float(np.mean([row['found'] for row in parts])),
                recall_mean=float(np.mean([row['recall'] for row in parts])),
                recall_min=min(row['recall'] for row in parts),
                recall_max=max(row['recall'] for row in parts),
                alteration_precision_mean=float(np.mean([row['alteration_precision'] for row in parts])),
                supplemental_found_mean=float(np.mean([row['supplemental_found'] for row in parts])),
                unchanged_reviewed_mean=float(np.mean([row['unchanged_reviewed'] for row in parts]))))
    return summary


def run(data_dir, output_dir, project_root=None):
    started = time.perf_counter()
    project_root = project_root or Path(__file__).resolve().parents[1]
    protocol = project_root / 'docs' / 'benchmark_protocol.txt'
    if not protocol.is_file():
        raise ValueError('The frozen benchmark protocol must exist before evaluation')
    source_paths = dict(housing=data_dir / 'd006/2024/rio_2024.csv',
                        roster=data_dir / 'd008/2024/kontr_k.csv')
    source_bytes = {name: path.read_bytes() for name, path in source_paths.items()}
    housing, roster = (source_bytes[name].decode('utf-8-sig') for name in ('housing', 'roster'))
    manifest = dict(protocol_version=1, started_at_utc=datetime.now(timezone.utc).isoformat(),
        protocol_sha256=sha256(protocol.read_bytes()),
        source_sha256={name: sha256(body) for name, body in source_bytes.items()},
        code_sha256={name: sha256((project_root / 'src' / name).read_bytes())
                     for name in ('workspace.py', 'evaluate_workspace.py')},
        versions=dict(python=platform.python_version(), numpy=np.__version__,
                      pandas=pd.__version__, scikit_learn=sklearn.__version__),
        parameters=dict(seeds=list(SEEDS), base_budget=BASE_BUDGET, total_budget=TOTAL_BUDGET,
            alteration_fraction_per_type=ALTERATION_FRACTION_PER_TYPE,
            scenarios={key: list(value) for key, value in SCENARIOS.items()},
            mode='alter before fit; train and score current full package; no label input to model'))
    original = analyze_package(housing, roster, label='Original unchanged benchmark input')
    eligible = [key for key, row in original['all_records_by_id'].items() if row['robust_score'] is not None]
    original_selection = select_queues(original, SEEDS[0])
    original_summary = {key: original['summary'][key] for key in
        ('housing_rows', 'training_rows', 'rule_flags', 'validation_flags', 'queue_size',
         'base_queue_size', 'statistical_queue', 'ml_queue', 'input_sha256', 'runtime_seconds')}
    original_summary.update(label_status='No adjudicated truth; no error metrics for original rows',
        supplemental_overlap_ml_robust=len(set(original_selection['additions'][METHODS[1]]) &
                                          set(original_selection['additions'][METHODS[0]])))
    manifest['model_parameters'] = original['summary']['model_metadata']
    rows, packages, marginals = [], [], []
    for regime in SCENARIOS:
        for seed in SEEDS:
            altered, labels = alter_package(housing, eligible, regime, seed)
            package = analyze_package(altered, roster, label=f'Internal stress test {regime}, seed {seed}')
            selected = select_queues(package, seed)
            measured, marginal = measure(selected, labels, regime, seed)
            rows.extend(measured)
            marginals.append(marginal)
            packages.append(dict(regime=regime, seed=seed,
                input_sha256=package['summary']['input_sha256'],
                housing_text_sha256=sha256(altered.encode('utf-8')),
                training_rows=package['summary']['training_rows'],
                explicit_signals=package['summary']['explicit_signal_records'],
                runtime_seconds=package['summary']['runtime_seconds'],
                production_ml_queue_matches_evaluated=(
                    [r['id'] for r in package['records']] == selected['queues'][METHODS[1]])))
            print(json.dumps(dict(regime=regime, seed=seed,
                found={row['method']: row['found'] for row in measured},
                marginal=marginal), ensure_ascii=False), flush=True)
    if not all(item['production_ml_queue_matches_evaluated'] for item in packages):
        raise AssertionError('Evaluated hybrid must match the unchanged production queue')
    source_unchanged = all(path.read_bytes() == source_bytes[name] for name, path in source_paths.items())
    if not source_unchanged:
        raise AssertionError('Original source files changed during evaluation')
    manifest.update(source_unchanged_verified=True, completed_at_utc=datetime.now(timezone.utc).isoformat(),
                    total_runtime_seconds=round(time.perf_counter() - started, 3))
    result = dict(version=1, manifest=manifest, original_package=original_summary,
        packages=packages, runs=rows, marginal_comparisons=marginals, summary=summarize(rows),
        caveats=[
            'Known artificial alterations, not adjudicated real errors; unchanged controls may contain errors.',
            'Moderate alterations may remain valid records. Detection is not proof an answer is wrong.',
            'Scenarios fixed before results without expert approval; three seeds are not independent real deployments.',
            'No real-error accuracy, time-saving, or population inference is measured.',
            'Production policy unchanged; this evaluation does not automatically choose the ML method.'])
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / 'workspace_benchmark.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    flat_rows = []
    for row in rows:
        flat = {key: value for key, value in row.items() if not isinstance(value, dict)}
        for regime_types in SCENARIOS.values():
            for scenario in regime_types:
                values = row['by_scenario'].get(scenario, {})
                for metric in ('altered', 'found'):
                    flat[f'{scenario}_{metric}'] = values.get(metric, '')
        for other in METHODS:
            flat[f'unique_supplemental_vs_{other}'] = row['unique_supplemental_vs'].get(other, '')
        flat_rows.append(flat)
    with (output_dir / 'workspace_benchmark.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(flat_rows[0]))
        writer.writeheader()
        writer.writerows(flat_rows)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, default=Path('reports'))
    args = parser.parse_args()
    result = run(args.data_dir, args.output_dir)
    print(json.dumps(dict(summary=result['summary'],
                         runtime_seconds=result['manifest']['total_runtime_seconds']), indent=2))
