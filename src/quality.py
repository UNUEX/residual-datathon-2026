"""Documented checks + Isolation Forest; aggregate exports and local review queue."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.model_selection import train_test_split

FIELDS = ['OB_PL', 'J_PL', 'KOL_K', 'household_size']
FEATURES = FIELDS + ['area_per_person', 'area_per_room', 'living_share']
NAMES = ['Общая площадь, м²', 'Жилая площадь, м²', 'Комнаты', 'Члены семьи',
         'Площадь на человека, м²', 'Площадь на комнату, м²', 'Доля жилой площади']
SEEDS = [17, 29, 43, 71, 101]


def rule_flags(d):
    return (d.J_PL > d.OB_PL) | (d[FIELDS] <= 0).any(axis=1) | d[FIELDS].isna().any(axis=1)


def features(d):
    x = d[FIELDS].astype(float).copy()
    x['area_per_person'] = x.OB_PL / x.household_size
    x['area_per_room'] = x.OB_PL / x.KOL_K
    x['living_share'] = x.J_PL / x.OB_PL
    return x.replace([np.inf, -np.inf], np.nan)


def model_fit(train, seed):
    x = np.log1p(features(train))
    median = x.median()
    spread = (x.quantile(.75) - x.quantile(.25)).clip(lower=.05)
    model = IsolationForest(n_estimators=200, max_samples=min(256, len(train)), contamination='auto',
                            random_state=seed, n_jobs=1)
    model.fit(x.fillna(median))
    return model, median, spread


def scores(d, model, median, spread):
    x = np.log1p(features(d)).fillna(median)
    return -model.score_samples(x), ((x - median).abs() / spread).max(axis=1).to_numpy()


def injected_copy(test, seed):
    """Alter 6% of held-out rows: equal counts in 3 fixed stress scenarios."""
    rng = np.random.default_rng(seed)
    out = test.copy()
    labels = np.full(len(out), 'unchanged', dtype=object)
    n = max(1, int(len(out) * .02))
    positions = rng.choice(len(out), 3*n, replace=False)
    for j, kind in enumerate(['living_exceeds_total', 'area_unit_x10', 'rooms_x8']):
        idx = positions[j*n:(j+1)*n]
        keys = out.index[idx]
        labels[idx] = kind
        if j == 0:
            out.loc[keys, 'J_PL'] = out.loc[keys, 'OB_PL'] * 1.25
        elif j == 1:
            out.loc[keys, ['OB_PL', 'J_PL']] *= 10
        else:
            out.loc[keys, 'KOL_K'] *= 8
    return out, labels


def benchmark(clean):
    runs = []
    for seed in SEEDS:
        train, test = train_test_split(clean, test_size=.3, random_state=seed)
        model, med, spread = model_fit(train, seed)
        changed, labels = injected_copy(test, seed + 1000)
        ml, robust = scores(changed, model, med, spread)
        rules = rule_flags(changed).to_numpy()
        # All methods review exactly the same number of records. Random tie-breaks
        # are seeded independently of corruption positions and shared across methods.
        tie = np.random.default_rng(seed + 2000).random(len(test))
        k = int(np.ceil(len(test) * .1))
        for name, values in [('rules', tie), ('rules_robust', robust), ('rules_ml', ml)]:
            order = np.lexsort((tie, -values, -rules.astype(int)))[:k]
            hit = labels[order] != 'unchanged'
            item = dict(seed=seed, method=name, train=len(train), test=len(test), reviewed=k,
                        injected=int((labels != 'unchanged').sum()), found=int(hit.sum()),
                        recall=float(hit.sum()/(labels != 'unchanged').sum()),
                        precision=float(hit.mean()), unchanged_reviewed=int((~hit).sum()))
            for kind in ['living_exceeds_total', 'area_unit_x10', 'rooms_x8']:
                item[kind] = float((labels[order] == kind).sum()/(labels == kind).sum())
            runs.append(item)
    summaries = []
    for name in ['rules', 'rules_robust', 'rules_ml']:
        parts = [r for r in runs if r['method'] == name]
        summaries.append(dict(method=name, recall_mean=float(np.mean([r['recall'] for r in parts])),
            recall_min=min(r['recall'] for r in parts), recall_max=max(r['recall'] for r in parts),
            precision_mean=float(np.mean([r['precision'] for r in parts])),
            by_scenario={k:float(np.mean([r[k] for r in parts])) for k in
                         ['living_exceeds_total','area_unit_x10','rooms_x8']}))
    return dict(review_fraction=.1, injection_fraction=.06, seeds=SEEDS, runs=runs, summary=summaries,
                caveat='Controlled perturbations of held-out synthetic records; not accuracy on real errors. Unchanged controls may contain unknown errors.')


def run(data_dir, report_dir=Path('reports'), private_dir=Path('private_outputs')):
    start = time.perf_counter()
    housing_path = data_dir / 'd006/2024/rio_2024.csv'
    roster_path = data_dir / 'd008/2024/kontr_k.csv'
    h = pd.read_csv(housing_path, dtype='string')
    roster = pd.read_csv(roster_path, dtype='string')
    if h.NOMER.isna().any() or h.NOMER.duplicated().any():
        raise ValueError('D006: missing or duplicate household key')
    from .eda import household_roster
    hh = household_roster(roster)
    d = h.set_index('NOMER')[['OB_PL','J_PL','KOL_K']].apply(pd.to_numeric, errors='raise')
    d = d.join(hh[['household_size']], validate='one_to_one').astype(float)
    if d.household_size.isna().any():
        raise ValueError('D006 has keys absent from D008')
    flags = rule_flags(d)
    clean = d.loc[~flags].copy()
    if len(clean) < 100:
        raise ValueError('Insufficient complete records for ML')
    evaluation = benchmark(clean)
    model, med, spread = model_fit(clean, 17)
    ml, robust = scores(d, model, med, spread)
    order = np.lexsort((d.index.to_numpy(), -ml, -flags.to_numpy().astype(int)))
    raw_features = features(d)
    deviations = (np.log1p(raw_features).fillna(med)-med).abs()/spread
    ranges = features(clean).quantile([.25,.5,.75])
    percentile = pd.Series(ml).rank(pct=True).to_numpy()*100
    queue = []
    for position in order[:250]:
        key = str(d.index[position]); row = d.iloc[position]
        reasons = []
        if row.J_PL > row.OB_PL:
            reasons.append(f'Жилая площадь {row.J_PL:g} м² больше общей {row.OB_PL:g} м².')
        if row.isna().any() or (row <= 0).any():
            reasons.append('Отсутствующее или неположительное значение: проверить исходную запись.')
        evidence = []
        for field in deviations.iloc[position].sort_values(ascending=False).index[:3]:
            value = raw_features.iloc[position][field]
            evidence.append(dict(field=field, name=NAMES[FEATURES.index(field)],
                value=None if pd.isna(value) else round(float(value),3),
                q25=round(float(ranges.loc[.25,field]),3),median=round(float(ranges.loc[.5,field]),3),
                q75=round(float(ranges.loc[.75,field]),3)))
        queue.append(dict(id=key, kind='rule' if flags.iloc[position] else 'ml',
            rank=len(queue)+1, score=round(float(ml[position]),5),
            percentile=round(float(percentile[position]),1), reasons=reasons,
            evidence=evidence, values={k:None if pd.isna(v) else float(v) for k,v in row.items()},
            source='d006/2024/rio_2024.csv + d008/2024/kontr_k.csv',
            status='pending'))
    hist = []
    for low, high in zip([0,10,20,30,40,60,100],[10,20,30,40,60,100,np.inf]):
        hist.append(dict(label=f'{low}–{int(high)}' if np.isfinite(high) else '100+',
            count=int(raw_features.area_per_person.between(low,high,inclusive='left').sum())))
    fingerprint = hashlib.sha256(housing_path.read_bytes()+roster_path.read_bytes()).hexdigest()
    report = dict(version='0.2', year=2024, scope='D006 + D008', model='Isolation Forest',
        n_estimators=200,max_samples=256,features=FEATURES,
        housing_rows=len(d),training_rows=len(clean),rule_flags=int(flags.sum()),
        living_exceeds_total=int((d.J_PL>d.OB_PL).sum()),
        missing_or_nonpositive=int((d[FIELDS].isna().any(axis=1)|(d[FIELDS]<=0).any(axis=1)).sum()),
        queue_size=len(queue),ml_queue=int(sum(q['kind']=='ml' for q in queue)),
        input_sha256=fingerprint,histogram=hist,benchmark=evaluation)
    report_dir.mkdir(parents=True,exist_ok=True); private_dir.mkdir(parents=True,exist_ok=True)
    (report_dir/'quality_summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    pd.DataFrame(evaluation['runs']).to_csv(report_dir/'quality_benchmark.csv',index=False)
    local=dict(summary=report, records=queue)
    (private_dir/'quality.json').write_text(json.dumps(local,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(dict(rows=len(d),flags=int(flags.sum()),benchmark=evaluation['summary'],
                         seconds=round(time.perf_counter()-start,2)),ensure_ascii=False,indent=2))
    return report


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir',type=Path,required=True)
    p.add_argument('--report-dir',type=Path,default=Path('reports'))
    p.add_argument('--private-dir',type=Path,default=Path('private_outputs'))
    a=p.parse_args();run(a.data_dir,a.report_dir,a.private_dir)
