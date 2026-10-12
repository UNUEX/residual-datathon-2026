"""Reproduce one descriptive housing brief; Python standard library only.

Usage: python reproduce.py --data-dir PATH [--output-dir PATH]
This calculation example contains no AI planner or generated conclusions.
"""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics

GROUPS = ("1–2", "3–4", "5+")


class DataError(ValueError):
    """The input cannot support the declared calculation."""


def load_rows(path, required):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise DataError(f"{path}: missing or duplicate column names")
        missing = set(required) - set(reader.fieldnames)
        if missing:
            raise DataError(f"{path}: missing columns {sorted(missing)}")
        rows = []
        for line, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise DataError(f"{path}, row {line}: invalid CSV width")
            rows.append({key: value.strip() for key, value in row.items()})
        if not rows:
            raise DataError(f"{path}: no data rows")
        return rows


def positive_number(value, name):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise DataError(f"{name}: expected a positive finite number") from exc
    if not math.isfinite(number) or number <= 0:
        raise DataError(f"{name}: expected a positive finite number")
    return number


def identifier(row, column):
    value = row.get(column, "").strip()
    if not value:
        raise DataError(f"Empty identifier {column}")
    return value  # Keep identifiers as text, including leading zeroes.


def prepare(housing, roster):
    members, declared, person_keys = {}, {}, set()
    for row in roster:
        household, person = identifier(row, "NOMER"), identifier(row, "NOMP")
        if (household, person) in person_keys:
            raise DataError(f"Duplicate person key: {household}/{person}")
        person_keys.add((household, person))
        size = positive_number(row.get("KOL_CHL"), "KOL_CHL")
        if not size.is_integer():
            raise DataError(f"KOL_CHL must be an integer: {household}")
        if household in declared and declared[household] != int(size):
            raise DataError(f"Inconsistent KOL_CHL: {household}")
        declared[household] = int(size)
        members[household] = members.get(household, 0) + 1
    for household, count in members.items():
        if count != declared[household]:
            raise DataError(f"KOL_CHL differs from roster count: {household}")
    seen, records = set(), []
    for row in housing:
        household = identifier(row, "NOMER")
        if household in seen:
            raise DataError(f"Duplicate household key: {household}")
        seen.add(household)
        if household not in members:
            raise DataError(f"Housing household missing from roster: {household}")
        total = positive_number(row.get("OB_PL"), "OB_PL")
        living = positive_number(row.get("J_PL"), "J_PL")
        size = members[household]
        records.append(dict(household=household, size=size, total_area=total,
            area_per_person=total / size, area_order_flag=living > total,
            group=GROUPS[0] if size <= 2 else GROUPS[1] if size <= 4 else GROUPS[2]))
    if not records:
        raise DataError("No linked housing households")
    coverage = dict(housing_households=len(records), roster_households=len(members),
        roster_only_households=len(set(members) - seen),
        area_order_flagged_households=sum(row["area_order_flag"] for row in records))
    return records, coverage


def quartiles(values):
    if len(values) == 1:
        return values[0], values[0]
    points = statistics.quantiles(values, n=4, method="inclusive")
    return points[0], points[2]


def aggregate(records):
    result = []
    for group in GROUPS:
        part = [record for record in records if record["group"] == group]
        areas = [record["total_area"] for record in part]
        person_areas = [record["area_per_person"] for record in part]
        q1, q3 = quartiles(person_areas) if part else (None, None)
        result.append(dict(household_size_group=group, households=len(part),
            persons=sum(record["size"] for record in part),
            median_total_area_m2=statistics.median(areas) if part else None,
            median_area_per_person_m2=statistics.median(person_areas) if part else None,
            q25_area_per_person_m2=q1, q75_area_per_person_m2=q3))
    return result


def calculate(housing, roster):
    records, coverage = prepare(housing, roster)
    return dict(stage="calculation_example", ai_planner_connected=False,
        year=2024, analysis_unit="household", weighted=False, synthetic_data=True,
        question="Как различается площадь жилья в домохозяйствах разного размера?",
        coverage=coverage, primary=aggregate(records),
        sensitivity=aggregate([row for row in records if not row["area_order_flag"]]),
        method=dict(join="D006.NOMER to counts of distinct D008.(NOMER, NOMP), within 2024",
            household_size="Number of D008 rows per NOMER, checked against KOL_CHL",
            total_area="D006.OB_PL, square metres",
            area_per_person="Compute OB_PL / household_size for each household, then take group median",
            group_edges="1–2, 3–4, 5 or more persons",
            quartiles="Linear interpolation, statistics.quantiles(method='inclusive')",
            primary_policy="Keep all linked households with validated positive areas and roster counts; retain area-order flags",
            sensitivity_policy="Separately exclude J_PL > OB_PL; do not change original records",
            estimate_weight="Each household has equal weight; this is not a person-weighted estimate"),
        limitations=[
            "Синтетический конкурсный массив: результаты не являются оценками населения Казахстана.",
            "Невзвешенные описательные показатели; причинные выводы и перенос на население не обоснованы.",
            "Площадь на человека математически зависит от размера домохозяйства; сравнение не доказывает причинную связь.",
            "Медиана индивидуально рассчитанных отношений не равна отношению медиан.",
            "J_PL > OB_PL — диагностическое противоречие, не подтверждение того, какое значение ошибочно.",
            "Домохозяйства только из D008 не входят в анализ жилья; это ограничение охвата, не установленная ошибка.",
            "ИИ-планировщик не подключён; это воспроизводимый пример расчётного ядра.",
        ])


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path,
        default=Path(__file__).resolve().parents[3] / "synthetic_microdata_2021-2024_20260921")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    paths = {"D006": args.data_dir / "d006/2024/rio_2024.csv",
             "D008": args.data_dir / "d008/2024/kontr_k.csv"}
    result = calculate(load_rows(paths["D006"], ["NOMER", "OB_PL", "J_PL"]),
        load_rows(paths["D008"], ["NOMER", "NOMP", "KOL_CHL"]))
    result["inputs"] = {form: dict(path=str(path.resolve()),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest()) for form, path in paths.items()}
    result["reproduction"] = dict(script="reproduce.py", dependencies="Python standard library only",
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "table.csv", result["primary"])
    write_csv(args.output_dir / "sensitivity.csv", result["sensitivity"])
    (args.output_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(dict(stage=result["stage"], coverage=result["coverage"],
        output_dir=str(args.output_dir.resolve())), ensure_ascii=False))


if __name__ == "__main__":
    main()
