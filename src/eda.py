"""Run from repository root: python -m src.eda --data-dir PATH."""
from pathlib import Path
import argparse
import json
import pandas as pd

FORMS = ('d002', 'd004', 'd006', 'd008')
LABELS = {
    'GR1': 'Жизнь в целом', 'GR2': 'Условия жизни', 'GR3': 'Здоровье',
    'GR4': 'Финансовое положение', 'GR5': 'Профессиональная деятельность',
    'GR8': 'Качество жилья',
}


def clean_scale(series):
    """D002 part 1: 1..10 are valid, 89 is not applicable / unsure."""
    numeric = pd.to_numeric(series, errors='raise')
    unexpected = numeric.notna() & ~numeric.isin(list(range(1, 11)) + [89])
    if unexpected.any():
        raise ValueError(f'Unexpected scale codes: {numeric[unexpected].unique()}')
    return numeric.where(numeric.between(1, 10))


def make_target(series):
    """Research definition: GR21 codes 1/2 = low or below-average self-rated means.

    This is not an official poverty classification. Missing labels stay missing.
    """
    numeric = pd.to_numeric(series, errors='raise')
    if (numeric.notna() & ~numeric.isin(range(1, 7))).any():
        raise ValueError('Unexpected GR21 codes')
    return numeric.isin([1, 2]).astype('Int64').where(numeric.notna())


def household_roster(roster):
    if roster[['NOMER', 'NOMP']].isna().any().any():
        raise ValueError('Missing person key')
    if roster.duplicated(['NOMER', 'NOMP']).any():
        raise ValueError('Duplicate person key')
    if (roster.groupby('NOMER').KOL_CHL.nunique() != 1).any():
        raise ValueError('Inconsistent household size')
    out = roster.groupby('NOMER').agg(
        household_size=('NOMP', 'size'), declared_size=('KOL_CHL', 'first'),
        TE=('TE', 'first'), K=('K', 'first'))
    if not (out.household_size == pd.to_numeric(out.declared_size)).all():
        raise ValueError('Declared household size differs from roster count')
    return out


def run(data_dir, output_dir, year=2024):
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = pd.read_csv(data_dir / 'MANIFEST.csv').set_index('path')
    tables, profiles, columns = {}, [], []
    expected = manifest[(manifest.form.isin(FORMS)) & (manifest.year == year)]
    for rel, row in expected.iterrows():
        path = data_dir / rel
        if not path.is_file():
            raise FileNotFoundError(f'Missing input: {path}')
        d = pd.read_csv(path, dtype='string')
        if len(d) != row.rows_synthetic or len(d.columns) != row.columns_synthetic:
            raise ValueError(f'Manifest mismatch: {rel}')
        tables[rel] = d
        profiles.append(dict(table=rel, rows=len(d), columns=len(d.columns),
            households=d.NOMER.nunique(), missing_key=int(d.NOMER.isna().sum()),
            full_duplicate_rows=int(d.duplicated().sum()),
            empty_columns=int(d.isna().all().sum())))
        for c in d:
            columns.append(dict(table=rel, column=c, missing=int(d[c].isna().sum()),
                missing_pct=float(d[c].isna().mean()*100), unique=d[c].nunique()))
    roster = next(d for p, d in tables.items() if p.startswith('d008/'))
    housing = next(d for p, d in tables.items() if p.startswith('d006/'))
    subject = tables[f'd002/{year}/subject.csv']
    ocenka = tables[f'd002/{year}/ocenka.csv']
    hh = household_roster(roster)
    joins = []
    for rel, d in tables.items():
        matched = d.merge(hh[['TE', 'K']], left_on='NOMER', right_index=True,
                          how='left', validate='many_to_one', suffixes=('', '_roster'))
        has = matched.NOMER.isin(hh.index)
        joins.append(dict(table=rel, unique_keys=d.NOMER.nunique(),
            unmatched_keys=len(set(d.NOMER.dropna()) - set(hh.index)),
            te_disagreements=int((matched.loc[has, 'TE'] != matched.loc[has, 'TE_roster']).sum()),
            k_disagreements=int((matched.loc[has, 'K'] != matched.loc[has, 'K_roster']).sum())))
    for name, d in [('D006', housing), ('subject', subject), ('ocenka', ocenka)]:
        if d.NOMER.isna().any() or d.NOMER.duplicated().any():
            raise ValueError(f'{name}: expected one row per household')
    scores = []
    for c, label in LABELS.items():
        raw = pd.to_numeric(subject[c], errors='raise')
        clean = clean_scale(subject[c])
        scores.append(dict(column=c, label=label, total=len(raw), valid=int(clean.count()),
            code89=int(raw.eq(89).sum()), blank=int(raw.isna().sum()),
            mean_valid=float(clean.mean()), mean_naive=float(raw.mean())))
    target = make_target(subject.GR21)
    counts = subject.GR21.value_counts().sort_index()
    target_distribution = [{'code':int(k), 'count':int(v), 'pct':float(v/len(subject)*100)} for k,v in counts.items()]
    complete = set(hh.index) & set(housing.NOMER) & set(subject.NOMER)
    positive_keys = set(subject.loc[target.eq(1).fillna(False), 'NOMER'])
    findings = dict(year=year, tables=len(tables), household_count=len(hh), persons=len(roster),
        housing_households=len(housing), subject_respondents=len(subject),
        complete_households=len(complete), complete_coverage_pct=len(complete)/len(hh)*100,
        target_valid=int(target.count()), target_positive=int(target.sum()),
        target_positive_pct=float(target.mean()*100),
        always_negative_accuracy_pct=float((1-target.mean())*100),
        target_positive_in_complete=len(positive_keys & complete),
        target_distribution=target_distribution,
        housing_area_contradictions=int((pd.to_numeric(housing.J_PL)>pd.to_numeric(housing.OB_PL)).sum()),
        scale_summary=scores)
    for name, records in [('table_profile',profiles),('column_profile',columns),('join_audit',joins),
                          ('satisfaction_summary',scores),('target_distribution',target_distribution)]:
        pd.DataFrame(records).to_csv(output_dir/f'{name}.csv',index=False,encoding='utf-8-sig')
    (output_dir/'findings.json').write_text(json.dumps(findings,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(findings,ensure_ascii=False,indent=2))
    return findings


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, default=Path('reports'))
    # File names and field definitions were validated only for this release/year.
    parser.add_argument('--year', type=int, choices=[2024], default=2024)
    args=parser.parse_args()
    run(args.data_dir,args.output_dir,args.year)

