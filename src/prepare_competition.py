"""Prepare one documented household table from the contest D006/D008 archive.

The default output is private. Source household/person identifiers never enter
the prepared table or audit. No model, weights, imputation or network is used.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import statistics


YEAR = 2024
HOUSING_PATH = "d006/2024/rio_2024.csv"
ROSTER_PATH = "d008/2024/kontr_k.csv"
DOC_PATHS = [
    "data-guide.html",
    "documentation/d006/2024/D006_deid/структураД006.doc",
    "documentation/d006/2024/D006_deid/Анкета Д006 рус.doc",
    "documentation/d008/2024/D008_2024/Структура D008.doc",
]
GROUP = "Размер домохозяйства"
SIZE = "Членов домохозяйства"
TOTAL = "Общая площадь"
LIVING = "Жилая площадь"
PER_PERSON = "Площадь на человека"
COLUMNS = [GROUP, SIZE, TOTAL, LIVING, PER_PERSON]
GROUP_ORDER = ["1–2 человека", "3–4 человека", "5+ человек"]
EXCLUSION_ORDER = [
    "missing_roster", "roster_size_unconfirmed", "invalid_area",
    "living_area_exceeds_total", "nonfinite_derived",
]


class PreparationError(ValueError):
    """An invalid structure or ambiguous join must stop preparation."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(text: str, required: list[str], form: str) -> list[dict]:
    lines = text.lstrip("\ufeff").splitlines()
    if not lines:
        raise PreparationError(f"{form}: пустой файл.")
    sep = max((",", ";", "\t"), key=lines[0].count)
    try:
        reader = csv.reader(io.StringIO(text.lstrip("\ufeff")), delimiter=sep, strict=True)
        header = [cell.strip().upper() for cell in next(reader)]
        if len(set(header)) != len(header) or not set(required) <= set(header):
            raise PreparationError(f"{form}: нужны уникальные столбцы {', '.join(required)}.")
        rows = []
        for values in reader:
            if not values or not any(value.strip() for value in values):
                continue
            if len(values) != len(header):
                raise PreparationError(f"{form}: неверная длина строки {reader.line_num}.")
            rows.append(dict(zip(header, (cell.strip() for cell in values))))
        if not rows:
            raise PreparationError(f"{form}: нет строк данных.")
        return rows
    except csv.Error as exc:
        raise PreparationError(f"{form}: повреждён CSV.") from exc


def _positive_integer(value: str) -> int | None:
    try:
        result = float(value.replace(",", "."))
    except (ValueError, TypeError):
        return None
    if not math.isfinite(result) or result <= 0 or not result.is_integer():
        return None
    return int(result)


def prepare(housing_text: str, roster_text: str) -> tuple[list[dict], dict]:
    """Inner join at household grain after explicit validation, never many-to-many."""
    housing = _read(housing_text, ["NOMER", "OB_PL", "J_PL"], "D006")
    roster = _read(roster_text, ["NOMER", "NOMP", "KOL_CHL", "GOD"], "D008")
    housing_ids: set[str] = set()
    for row in housing:
        key = row["NOMER"]
        if not key or key in housing_ids:
            raise PreparationError("D006: пустой или повторный NOMER; объединение остановлено.")
        housing_ids.add(key)

    people: dict[str, list[dict]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for row in roster:
        key = (row["NOMER"], row["NOMP"])
        if not all(key) or key in seen:
            raise PreparationError("D008: пустой или повторный NOMER/NOMP; объединение остановлено.")
        if row["GOD"] != str(YEAR):
            raise PreparationError("D008: все GOD должны быть 2024; смешение периодов запрещено.")
        seen.add(key)
        people[key[0]].append(row)

    output = []
    exclusions = dict.fromkeys(EXCLUSION_ORDER, 0)
    for row in housing:
        members = people.get(row["NOMER"], [])
        if not members:
            exclusions["missing_roster"] += 1
            continue
        size = len(members)
        if any(_positive_integer(member["KOL_CHL"]) != size for member in members):
            exclusions["roster_size_unconfirmed"] += 1
            continue
        total, living = (_positive_integer(row[field]) for field in ("OB_PL", "J_PL"))
        if total is None or living is None:
            exclusions["invalid_area"] += 1
            continue
        if living > total:
            exclusions["living_area_exceeds_total"] += 1
            continue
        per_person = total / size
        if not math.isfinite(per_person):
            exclusions["nonfinite_derived"] += 1
            continue
        group = GROUP_ORDER[0] if size <= 2 else GROUP_ORDER[1] if size <= 4 else GROUP_ORDER[2]
        output.append({GROUP: group, SIZE: size, TOTAL: total,
                       LIVING: living, PER_PERSON: per_person})

    if not output:
        raise PreparationError("После явных проверок не осталось пригодных домохозяйств.")
    if len(output) > 50_000:
        raise PreparationError("Подготовлено больше 50 000 домохозяйств: превышен предел импорта MVP.")
    counts = Counter(row[GROUP] for row in output)
    audit = {
        "housing_rows": len(housing), "housing_unique_households": len(housing_ids),
        "roster_rows": len(roster), "roster_unique_households": len(people),
        "matched_housing_households": len(housing_ids & people.keys()),
        "unused_roster_households": len(people.keys() - housing_ids),
        "duplicate_housing_keys": 0, "duplicate_person_keys": 0,
        "eligible_households": len(output), "excluded_households": len(housing) - len(output),
        "exclusion_priority": EXCLUSION_ORDER, "exclusions_mutually_exclusive": exclusions,
        "group_counts": {group: counts[group] for group in GROUP_ORDER},
    }
    assert audit["eligible_households"] + sum(exclusions.values()) == len(housing)
    return output, audit


def prepare_archive(data_root: Path, output_dir: Path) -> dict:
    """Write private CSV and an identifier-free aggregate audit for the supplied archive."""
    sources = {}
    contents = {}
    for relative in [HOUSING_PATH, ROSTER_PATH, *DOC_PATHS]:
        source = data_root / relative
        if not source.is_file():
            raise PreparationError(f"В архиве отсутствует {relative}.")
        data = source.read_bytes()
        sources[relative] = {"sha256": sha256(data), "bytes": len(data)}
        if relative in (HOUSING_PATH, ROSTER_PATH):
            contents[relative] = data.decode("utf-8-sig")
    rows, audit = prepare(contents[HOUSING_PATH], contents[ROSTER_PATH])
    csv_buffer = io.StringIO(newline="")
    writer = csv.DictWriter(csv_buffer, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    csv_bytes = csv_buffer.getvalue().encode("utf-8-sig")
    aggregate = []
    for group in GROUP_ORDER:
        members = [row for row in rows if row[GROUP] == group]
        if members:
            aggregate.append({"group": group, "households": len(members),
                "median_area_per_person_m2": round(statistics.median(row[PER_PERSON] for row in members), 6),
                "median_total_area_m2": round(statistics.median(row[TOTAL] for row in members), 6)})
    result = {
        "case": "Residual: жилищная справка по синтетическому конкурсному набору",
        "year": YEAR, "synthetic_data": True, "population_inference": False,
        "source_files": sources, "preparation_code_sha256": sha256(Path(__file__).read_bytes()),
        "prepared_file": {"name": "competition_2024.csv", "sha256": sha256(csv_bytes),
                          "rows": len(rows), "columns": COLUMNS},
        "audit": audit,
        "definitions": {
            GROUP: "Группы проекта: 1–2, 3–4 и 5+ записанных членов домохозяйства.",
            SIZE: "Число уникальных NOMER+NOMP в D008, равное KOL_CHL во всех строках домохозяйства.",
            TOTAL: "D006.OB_PL, общая площадь жилья, м².",
            LIVING: "D006.J_PL, жилая площадь жилья, м².",
            PER_PERSON: "D006.OB_PL / число уникальных NOMER+NOMP в D008, м²/чел.",
        },
        "rules": [
            "Ключи читаются как текст: ведущие нули сохраняются; дубликаты прекращают подготовку.",
            "D008 сначала сворачивается до одного размера состава на NOMER, затем соединяется с D006.",
            "Отсутствующий или несогласованный состав исключается без восстановления значений.",
            "Площади должны быть конечными положительными целыми числами; это узкий сценарий без импутации.",
            "J_PL > OB_PL исключается как смысловое противоречие. Это правило проекта по определению площадей.",
            "Причины исключений взаимно исключаются по указанному приоритету; строки исходников не меняются.",
            "Число комнат не используется и не влияет на включение строки в этот анализ площадей.",
        ],
        "reference_aggregates": aggregate,
        "limitations": [
            "Синтетические данные организаторов. Числа описывают только пригодные записи этого набора.",
            "Веса обследования, доверительные интервалы, контроль раскрытия и причинные выводы не рассчитываются.",
            "Медиана отношений площадей рассчитана по домохозяйствам, а не по отдельным людям.",
            "Состав — записанные лица из карточки 2024 года. Изменения состава внутри года не моделируются.",
            "Площадь на человека механически зависит от знаменателя; сравнение групп не доказывает причинность.",
            "Участки/владение дополнительным жильём не учитываются. Группировка создана проектом.",
            "Подготовленный CSV и исходные микроданные остаются локально; агрегат можно проверять отдельно.",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "competition_2024.csv").write_bytes(csv_bytes)
    (output_dir / "preparation_audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True,
                        help="Корень распакованного конкурсного архива.")
    parser.add_argument("--output-dir", type=Path,
                        default=Path(__file__).resolve().parents[1] / "private_outputs" / "competition_case",
                        help="Локальная папка результата. По умолчанию private_outputs/competition_case.")
    args = parser.parse_args()
    try:
        result = prepare_archive(args.data_root, args.output_dir)
    except (PreparationError, OSError, UnicodeError) as exc:
        parser.exit(1, f"Подготовка остановлена: {exc}\n")
    print(json.dumps({"prepared_households": result["audit"]["eligible_households"],
                      "excluded_households": result["audit"]["excluded_households"],
                      "synthetic_data": True, "files": ["competition_2024.csv", "preparation_audit.json"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
