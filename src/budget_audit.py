"""Audit D004 coverage before constructing expenditure features; export aggregates only."""
import argparse
import json
from pathlib import Path
import pandas as pd
from .eda import make_target


def run(data_dir, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    subject = pd.read_csv(data_dir / 'd002/2024/subject.csv', dtype='string')
    housing = pd.read_csv(data_dir / 'd006/2024/rio_2024.csv', dtype='string')
    roster = pd.read_csv(data_dir / 'd008/2024/kontr_k.csv', dtype='string')
    base = set(subject.NOMER) & set(housing.NOMER) & set(roster.NOMER)
    positive = set(subject.loc[make_target(subject.GR21).eq(1), 'NOMER']) & base
    rows, quarters, participation_sets = [], [], []
    for quarter in range(1, 5):
        folder = data_dir / f'd004/2024/{quarter}kv'
        visit = pd.read_csv(folder / 'kv_vopr0.csv', dtype='string')
        if visit.NOMER.isna().any() or visit.NOMER.duplicated().any():
            raise ValueError('Visit table must have one row per household')
        if not visit.REZ.dropna().isin(['1', '2']).all():
            raise ValueError('Unexpected participation code')
        participants = set(visit.loc[visit.REZ.eq('1'), 'NOMER'])
        participation_sets.append(participants)
        quarters.append(dict(quarter=quarter, visit_households=len(visit),
            participants=len(participants), refused=int(visit.REZ.eq('2').sum()),
            missing_response=int(visit.REZ.isna().sum()),
            base_participants=len(base & participants),
            positive_participants=len(positive & participants)))
        for path in sorted(folder.glob('*.csv')):
            d = pd.read_csv(path, dtype='string')
            if d.NOMER.isna().any() or not set(d.NOMER) <= set(roster.NOMER):
                raise ValueError(f'Invalid household key: {path}')
            if not d.GOD.eq('2024').all() or not d.KVARTAL.eq(str(quarter)).all():
                raise ValueError(f'Unexpected period: {path}')
            keys = set(d.NOMER)
            item = dict(quarter=quarter, table=path.stem, rows=len(d),
                households=len(keys), base_households=len(keys & base),
                positive_households=len(keys & positive),
                households_without_participation=len(keys - participants),
                duplicate_rows=int(d.duplicated().sum()), columns='|'.join(d.columns))
            if 'STOIMK' in d:
                amounts = pd.to_numeric(d.STOIMK, errors='raise')
                item.update(amount_missing=int(amounts.isna().sum()),
                    amount_zero=int(amounts.eq(0).sum()),
                    amount_negative=int(amounts.lt(0).sum()))
            if 'KODNU' in d:
                codes = d.KODNU.dropna()
                item.update(code_missing=int(d.KODNU.isna().sum()),
                    code_with_dot=int(codes.str.contains('.', regex=False).sum()),
                    code_lengths='|'.join(map(str, sorted(codes.str.len().unique()))))
            rows.append(item)
    all_quarters = set.intersection(*participation_sets)
    summary = dict(base_households=len(base), base_positive=len(positive),
        participants_all_quarters=len(all_quarters),
        base_participants_all_quarters=len(base & all_quarters),
        positive_participants_all_quarters=len(positive & all_quarters),
        quarters=quarters,
        note='Coverage only: absent section rows are not assumed to mean zero expenditure.')
    pd.DataFrame(rows).to_csv(output_dir / 'budget_coverage.csv', index=False, encoding='utf-8-sig')
    (output_dir / 'budget_audit.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, default=Path('reports'))
    args = parser.parse_args()
    run(args.data_dir, args.output_dir)
