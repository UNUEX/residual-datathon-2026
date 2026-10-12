"""Local table catalogue, approved AI plans, deterministic reports and exports."""
from __future__ import annotations

import base64
import binascii
import csv
from datetime import datetime, timezone
import hashlib
import html
import io
import json
from pathlib import Path
import re
import threading
from uuid import uuid4

from . import table_calculation as calculation
from .table_import import inspect_file, read_table, MAX_FILE_BYTES


class TableError(ValueError):
    pass


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True).encode("utf-8")


def now():
    return datetime.now(timezone.utc).isoformat()


def csv_safe_text(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def text_field(value, label, maximum, required=False):
    if not isinstance(value, str) or len(value.strip()) > maximum or (required and not value.strip()):
        raise TableError(f"{label}: {'обязательное поле, ' if required else ''}не более {maximum} символов.")
    return value.strip()


PLAN_KEYS = ("status", "reason", "metrics", "aggregation", "group_by")


class TabularService:
    def __init__(self, root, provider_getter):
        self.root = Path(root)
        self.provider_getter = provider_getter
        self.lock = threading.RLock()
        for name in ("imports", "datasets", "plans", "reports"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        # Capture the exact trusted calculator source used by this process.
        self.calculation_source = Path(calculation.__file__).read_bytes()
        self.code_hash = digest(self.calculation_source)
        example_root = Path(__file__).resolve().parents[1] / "examples" / "import"
        self.example_hashes = {digest(path.read_bytes()) for name in ("schools.csv", "schools.xlsx", "budget.html")
                               if (path := example_root / name).is_file()}

    def _dataset_display(self, dataset):
        # Recognize the bundled examples by content, even if renamed by the user.
        return {**dataset, "synthetic_data": dataset.get("declared_synthetic") is True
                or dataset.get("source", {}).get("sha256") in self.example_hashes}

    def _synthetic_notice(self, dataset):
        if dataset.get("source", {}).get("sha256") in self.example_hashes:
            return "Учебные вымышленные данные из примеров приложения. Результаты не являются официальной статистикой."
        return "Пользователь указал синтетическое или учебное происхождение данных. Результаты описывают этот набор и не являются оценками населения."

    def _identifier(self, value):
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
            raise TableError("Некорректный идентификатор. Выберите сохранённую таблицу или справку.")
        return value

    def _write(self, path, value):
        with self.lock:
            temp = path.with_suffix("." + uuid4().hex + ".tmp")
            temp.write_bytes(json_bytes(value))
            temp.replace(path)

    def _read(self, kind, identifier):
        path = self.root / kind / (self._identifier(identifier) + ".json")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise TableError("Сохранённая запись не найдена или повреждена. Загрузите таблицу заново.") from None

    def _index(self, kind):
        result = []
        for path in (self.root / kind).glob("*.json"):
            try:
                result.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(result, key=lambda x: x.get("created_at", ""), reverse=True)

    def state(self):
        provider = self.provider_getter().public_config()
        provider["data_policy"] = "Модели передаются вопрос и подтверждённые названия столбцов, единицы и описание набора. Значения строк и результаты расчёта не передаются. Сам вопрос и названия могут содержать чувствительные сведения."
        return {"datasets": [self._dataset_display(item) for item in self._index("datasets")],
                "reports": [{key: report[key] for key in ("id", "dataset_id", "question", "created_at")} for report in self._index("reports")],
                "provider": provider,
                "capabilities": {"formats": ["csv", "tsv", "xlsx", "xls", "html", "htm"],
                    "max_file_bytes": MAX_FILE_BYTES, "max_rows": 50000, "max_columns": 100,
                    "max_active_columns": 15, "max_groups": 30,
                    "aggregations": ["mean", "median", "sum"], "python_upload": False}}

    def inspect(self, payload):
        filename = text_field(payload.get("filename"), "Имя файла", 255, True)
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
        encoded = payload.get("content_base64")
        if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_FILE_BYTES + 2) // 3):
            raise TableError("Выберите один файл размером до 10 МБ.")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise TableError("Файл передан некорректно. Повторите загрузку.") from None
        inspection = inspect_file(filename, raw)
        identifier = uuid4().hex
        item = {**inspection, "id": identifier, "filename": filename, "synthetic_data": digest(raw) in self.example_hashes,
                "sha256": digest(raw), "created_at": now()}
        (self.root / "imports" / (identifier + ".bin")).write_bytes(raw)
        self._write(self.root / "imports" / (identifier + ".json"), item)
        return item

    def _selected(self, payload):
        item = self._read("imports", payload.get("import_id"))
        raw = (self.root / "imports" / (item["id"] + ".bin")).read_bytes()
        if digest(raw) != item["sha256"]:
            raise TableError("Файл изменился после загрузки. Загрузите его заново.")
        header = payload.get("header_row", 1)
        if isinstance(header, bool) or not isinstance(header, int):
            raise TableError("Номер строки заголовков должен быть целым числом, начиная с 1.")
        table_id = payload.get("table_id")
        selected = read_table(item["filename"], raw, table_id, header)
        return item, selected

    def preview(self, payload):
        item, selected = self._selected(payload)
        return {"import_id": item["id"], "table_id": payload["table_id"],
                "table_label": selected["table_label"], "header_row": selected["header_row"],
                "row_count": len(selected["rows"]),
                "columns": calculation.profile_columns(selected["columns"], selected["rows"]),
                "preview": selected["rows"][:8], "warnings": selected["warnings"]}

    @staticmethod
    def _fingerprint(dataset):
        keys = ("id", "label", "observation_unit", "period", "columns", "row_count",
                "source", "source_csv_sha256")
        if "declared_synthetic" in dataset:
            keys += ("declared_synthetic",)
        return digest(json_bytes({key: dataset[key] for key in keys}))

    def register(self, payload):
        item, selected = self._selected(payload)
        profiles = calculation.profile_columns(selected["columns"], selected["rows"])
        columns = calculation.validate_columns(profiles, payload.get("columns"))
        declared_synthetic = payload.get("declared_synthetic", False)
        if not isinstance(declared_synthetic, bool):
            raise TableError("Укажите признак синтетических данных как да или нет.")
        for index, column in enumerate(columns):
            values = [row[index] for row in selected["rows"]]
            column["missing_count"] = sum(calculation.is_missing(value, column["missing_values"]) for value in values)
            column["unique_count"] = len(set(values))
            column["invalid_count"] = 0
            if column["role"] == "number":
                column["invalid_count"] = sum(not calculation.is_missing(value, column["missing_values"]) and
                    calculation.parse_number(value, column["decimal_separator"]) is None for value in values)
                if column["missing_count"] + column["invalid_count"] == len(values):
                    raise TableError(f"«{column['label']}»: нет пригодных чисел с выбранным десятичным разделителем. Проверьте роль и разделитель.")
        identifier = uuid4().hex
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow([column["id"] for column in columns])
        writer.writerows(selected["rows"])
        raw_csv = output.getvalue().encode("utf-8")
        dataset = {"id": identifier, "created_at": now(),
            "label": text_field(payload.get("label"), "Название набора", 120, True),
            "observation_unit": text_field(payload.get("observation_unit"), "Что означает одна строка", 120, True),
            "period": text_field(payload.get("period", ""), "Период", 80),
            "columns": columns, "row_count": len(selected["rows"]), "declared_synthetic": declared_synthetic,
            "warnings": selected["warnings"], "source_csv_sha256": digest(raw_csv),
            "source": {"import_id": item["id"], "filename": item["filename"], "format": item["format"],
                "table": selected["table_label"], "table_id": payload["table_id"],
                "header_row": selected["header_row"], "sha256": item["sha256"]}}
        dataset = self._dataset_display(dataset)
        dataset["fingerprint"] = self._fingerprint(dataset)
        (self.root / "datasets" / (identifier + ".csv")).write_bytes(raw_csv)
        self._write(self.root / "datasets" / (identifier + ".json"), dataset)
        return dataset

    def _checked_source(self, dataset):
        if self._fingerprint(dataset) != dataset["fingerprint"]:
            raise TableError("Описание набора изменилось. Создайте новую версию таблицы и плана.")
        path = self.root / "datasets" / (self._identifier(dataset["id"]) + ".csv")
        raw = path.read_bytes()
        if digest(raw) != dataset["source_csv_sha256"]:
            raise TableError("Исходный CSV изменился после загрузки. Создайте новую версию таблицы.")
        original = self.root / "imports" / (self._identifier(dataset["source"]["import_id"]) + ".bin")
        if digest(original.read_bytes()) != dataset["source"]["sha256"]:
            raise TableError("Исходный файл изменился после загрузки. Создайте новую версию таблицы.")
        return raw

    def plan(self, payload):
        dataset = self._read("datasets", payload.get("dataset_id"))
        self._checked_source(dataset)
        question = text_field(payload.get("question"), "Вопрос", 2000, True)
        if len(question) < 5:
            raise TableError("Введите более конкретный вопрос: минимум 5 символов.")
        provider = self.provider_getter()
        base_prompt = calculation.build_prompt(dataset)
        prompt = base_prompt
        errors = []
        for attempt in range(2):
            value = provider.propose(question, prompt, calculation.create_schema(dataset["columns"]))
            try:
                candidate = calculation.question_guard(question, calculation.validate_plan(value, dataset), dataset)
                break
            except calculation.CalculationError as exc:
                if attempt:
                    raise
                errors.append(str(exc))
                prompt = base_prompt + "\nПроверка предыдущего плана: " + str(exc) + "\nИсправь план для исходного вопроса. Расчёт ещё не выполнялся."
        mapping = {column["id"]: column for column in dataset["columns"]}
        plan = {**candidate, "id": uuid4().hex, "dataset_id": dataset["id"], "question": question,
            "dataset_fingerprint": dataset["fingerprint"], "calculation_code_sha256": self.code_hash,
            "created_at": now(), "model": provider.config["model"], "provider": provider.config["provider"],
            "model_endpoint": provider.config["base_url"], "model_attempts": attempt + 1,
            "validation_feedback": errors, "data_sent_to_model": ["question", "confirmed_metadata", "operation_catalog"],
            "metric_details": [{key: mapping[metric][key] for key in ("id", "label", "unit")} for metric in candidate["metrics"]],
            "group_label": mapping[candidate["group_by"]]["label"] if candidate["group_by"] else "Вся таблица",
            "observation_unit": dataset["observation_unit"], "period": dataset["period"],
            "requires_confirmation": candidate["status"] == "ready"}
        self._write(self.root / "plans" / (plan["id"] + ".json"), plan)
        return plan

    def execute(self, payload):
        if set(payload) != {"plan_id"}:
            raise TableError("Расчёт принимает только идентификатор сохранённого плана.")
        plan = self._read("plans", payload.get("plan_id"))
        dataset = self._dataset_display(self._read("datasets", plan["dataset_id"]))
        candidate = calculation.validate_plan({key: plan[key] for key in PLAN_KEYS}, dataset)
        if candidate["status"] != "ready":
            raise TableError("Уточните вопрос: этот план нельзя выполнить.")
        if plan["dataset_fingerprint"] != dataset["fingerprint"] or plan["calculation_code_sha256"] != self.code_hash:
            raise TableError("Версия данных или расчёта изменилась. Составьте новый план.")
        raw = self._checked_source(dataset)
        reader = csv.reader(io.StringIO(raw.decode("utf-8")))
        next(reader)
        result = calculation.calculate(list(reader), dataset["columns"], candidate)
        report = {**result, "id": uuid4().hex, "created_at": now(), "dataset_id": dataset["id"],
            "question": plan["question"], "plan": plan, "dataset": dataset,
            "sources": [{key: dataset["source"][key] for key in ("filename", "table", "sha256", "header_row")}],
            "calculation_code_sha256": self.code_hash, "calculation_version": "tables-1.0",
            "model_used_for": "plan_only"}
        report["limitations"].insert(0, self._synthetic_notice(dataset) if dataset["synthetic_data"] else "Происхождение данных и смысл столбцов подтверждает пользователь. Результат описывает загруженные строки и сам по себе не является официальной оценкой населения.")
        self._write(self.root / "reports" / (report["id"] + ".json"), report)
        # Bundle the source of the calculator, so future application updates do not change reproduction.
        (self.root / "reports" / (report["id"] + ".py")).write_bytes(self.calculation_source)
        return report

    def report(self, identifier):
        result = self._read("reports", identifier)
        result["dataset"] = self._dataset_display(result["dataset"])
        if result["dataset"]["synthetic_data"] and not any("Учебные вымышленные данные" in line or "синтетич" in line.lower() for line in result["limitations"]):
            result["limitations"].insert(0, self._synthetic_notice(result["dataset"]))
        return result

    def export(self, identifier, format="json"):
        report = self.report(identifier)
        if format == "json":
            return json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"), "application/json; charset=utf-8", "residual-report.json"
        if format == "source":
            return self._checked_source(report["dataset"]), "text/csv; charset=utf-8", "source.csv"
        if format == "python":
            source = (self.root / "reports" / (report["id"] + ".py")).read_bytes()
            if digest(source) != report["calculation_code_sha256"]:
                raise TableError("Сохранённый код расчёта повреждён.")
            # Trusted, versioned application code. Imported files and the model never provide executable code.
            config = {"columns": report["dataset"]["columns"],
                "plan": {key: report["plan"][key] for key in PLAN_KEYS}, "sha256": report["dataset"]["source_csv_sha256"]}
            script = ('# Повторение: python reproduce_table.py source.csv > result.json\n'
                      '# Python 3.10+, только стандартная библиотека. CSV скачайте рядом с кодом.\n'
                      '# Метки столбцов и план — данные; загруженный код не выполняется.\n' + source.decode("utf-8") + '\n\n'
                      'if __name__ == "__main__":\n'
                      '    import csv, hashlib, io, json, sys\n'
                      '    from pathlib import Path\n'
                      '    if len(sys.argv) != 2:\n        raise SystemExit("Usage: python reproduce_table.py source.csv")\n'
                      '    settings = json.loads(' + repr(json.dumps(config, ensure_ascii=True)) + ')\n'
                      '    raw = Path(sys.argv[1]).read_bytes()\n'
                      '    if hashlib.sha256(raw).hexdigest() != settings["sha256"]:\n        raise SystemExit("Source SHA-256 mismatch: use the CSV exported with this report.")\n'
                      '    reader = csv.reader(io.StringIO(raw.decode("utf-8")))\n'
                      '    next(reader)\n'
                      '    print(json.dumps(calculate(list(reader), settings["columns"], settings["plan"]), ensure_ascii=True, indent=2, allow_nan=False))\n')
            return script.encode("utf-8"), "text/x-python; charset=utf-8", "reproduce_table.py"
        if format == "csv":
            output = io.StringIO(newline="")
            writer = csv.writer(output)
            metrics = report["plan"]["metrics"]
            writer.writerow(["group", "rows"] + [csv_safe_text(report["formulas"][m]["label"]) for m in metrics]
                + [m + "_valid" for m in metrics] + [m + "_missing" for m in metrics])
            for row in report["table"]:
                group = csv_safe_text("Вся таблица" if row["group"] is None else row["group"])
                writer.writerow([group, row["rows"]] + [row["values"][m] for m in metrics]
                    + [row["valid_counts"][m] for m in metrics] + [row["missing_counts"][m] for m in metrics])
            return output.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8", "residual-table.csv"
        if format == "html":
            return self._html(report).encode("utf-8"), "text/html; charset=utf-8", "residual-brief.html"
        raise TableError("Доступны выгрузки HTML, CSV, JSON и Python, а также исходный CSV.")

    @staticmethod
    def _html_chart(report, metric):
        import textwrap

        escape = lambda value: html.escape(str(value), quote=True)
        number = lambda value: "0" if value == 0 else str(value).replace(".", ",")
        rows, definition = report["table"], report["formulas"][metric]
        values = [row["values"][metric] for row in rows if row["values"][metric] is not None]
        # Normalise before subtraction: a span from -1e308 to 1e308 must stay finite.
        extent = max((abs(value) for value in values), default=0)
        if extent:
            low = min([0] + [value / extent for value in values])
            high = max([0] + [value / extent for value in values])
        else:
            low, high = -1, 1
        left, width, row_height = 250, 470, 72
        position = lambda value: left + ((value / extent if extent else 0) - low) / (high - low) * width
        zero, height = position(0), 55 + len(rows) * row_height
        title = definition["label"] + (" · " + definition["unit"] if definition["unit"] else "")
        parts = [f'<figure><figcaption>{escape(title)}</figcaption><div class="chart-scroll">'
                 f'<svg xmlns="http://www.w3.org/2000/svg" class="report-chart" viewBox="0 0 960 {height}" '
                 f'role="img" aria-labelledby="chart-title-{escape(metric)}">'
                 f'<title id="chart-title-{escape(metric)}">{escape(title)} — значения по группам</title>'
                 '<desc>Отрицательные значения слева от нуля, положительные справа. '
                 'Ноль обозначен точкой. Отсутствующее значение показано без столбца. '
                 'Полные названия и численность приведены в таблице.</desc>'
                 f'<line class="zero-axis" x1="{zero:.6f}" x2="{zero:.6f}" y1="30" y2="{height - 10}"/>'
                 f'<text class="axis-label" x="{zero:.6f}" y="20" text-anchor="middle">0</text>']
        for index, row in enumerate(rows):
            group = "Вся таблица" if row["group"] is None else str(row["group"])
            value = row["values"][metric]
            displayed = "нет данных" if value is None else number(value)
            y = 36 + index * row_height
            lines = textwrap.wrap(group, width=31, max_lines=3, placeholder="…") or [""]
            parts.append(f'<g class="chart-row" data-row="{index}"><title>{escape(group)}: '
                         f'{escape(displayed)}{escape(" " + definition["unit"] if value is not None and definition["unit"] else "")}</title>'
                         f'<text class="group-label" x="8" y="{y + 15}">')
            parts.extend(f'<tspan x="8" dy="{0 if line_index == 0 else 17}">{escape(line)}</tspan>'
                         for line_index, line in enumerate(lines))
            parts.append('</text>')
            if value is not None:
                end = position(value)
                if value == 0:
                    parts.append(f'<circle class="zero-value" cx="{zero:.6f}" cy="{y + 18}" r="4"/>')
                else:
                    parts.append(f'<rect class="value-bar {"negative" if value < 0 else "positive"}" '
                                 f'x="{min(zero, end):.6f}" y="{y + 5}" width="{abs(end - zero):.6f}" height="26" rx="3"/>')
            parts.append(f'<text class="chart-value" x="742" y="{y + 23}">{escape(displayed)}</text></g>')
        parts.append('</svg></div></figure>')
        return "".join(parts)

    @staticmethod
    def _html(report):
        escape = lambda value: html.escape(str(value), quote=True)
        number = lambda value: "—" if value is None else "0" if value == 0 else str(value).replace(".", ",")
        metrics, dataset = report["plan"]["metrics"], report["dataset"]
        synthetic = dataset.get("synthetic_data") is True or dataset.get("synthetic_dataset") is True
        operation = report["plan"]["aggregation"]
        method = {
            "mean": "Среднее: сумма пригодных значений, делённая на их количество.",
            "median": "Медиана: середина упорядоченного ряда пригодных значений; при чётном количестве — среднее двух центральных значений.",
            "sum": "Сумма: все пригодные значения складываются. Если пригодных значений нет, результат отсутствует и не заменяется нулём.",
        }[operation]
        head = '<tr><th scope="col">Группа</th><th scope="col">Строк</th>' + "".join(
            '<th scope="col">' + escape(report["formulas"][m]["label"])
            + (" · " + escape(report["formulas"][m]["unit"]) if report["formulas"][m]["unit"] else "")
            + "</th>" for m in metrics) + "</tr>"
        body = "".join('<tr><th scope="row">' + escape("Вся таблица" if row["group"] is None else row["group"])
            + '</th><td class="numeric">' + escape(row["rows"]) + "</td>" + "".join(
                '<td class="numeric">' + escape(number(row["values"][m])) + "<small>Использовано: "
                + escape(row["valid_counts"][m]) + "; пропущено: " + escape(row["missing_counts"][m])
                + "</small></td>" for m in metrics) + "</tr>" for row in report["table"])
        return ('<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Residual — '
            + ("учебная " if synthetic else "") + 'аналитическая справка</title>'
            '<style>body{max-width:1100px;margin:40px auto;padding:24px;font:16px/1.6 system-ui;color:#20291f;background:#f8faf5}'
            'h1{font-size:30px;line-height:1.25;overflow-wrap:anywhere}h2{margin-top:36px}p,li,td,th,figcaption{overflow-wrap:anywhere}'
            'table{border-collapse:collapse;width:100%;background:white}td,th{padding:12px;border:1px solid #c9d1c5;text-align:left}'
            'thead{background:#e9eee4}tbody th{font-weight:500}.numeric{font-variant-numeric:tabular-nums}small{display:block;color:#526249}'
            'code{overflow-wrap:anywhere}li{margin:10px 0}.sample{padding:14px 18px;border:1px solid #9ead88;background:#e9eee0}'
            '.chart-scroll,.table-scroll{overflow-x:auto}figure{margin:24px 0;padding:18px;background:white;border:1px solid #c9d1c5;border-radius:12px}'
            'figcaption{font-weight:700;margin-bottom:12px}.report-chart{display:block;width:100%;min-width:720px;height:auto}'
            '.zero-axis{stroke:#6b7863;stroke-width:1}.axis-label{fill:#526249;font-size:13px}.group-label{fill:#20291f;font-size:14px}'
            '.chart-value{fill:#20291f;font-size:15px;font-variant-numeric:tabular-nums}.positive,.zero-value{fill:#617b50}.negative{fill:#a9654a}'
            '@media(max-width:600px){body{margin:12px auto;padding:16px}h1{font-size:24px}figure{padding:12px}}'
            '@media print{body{margin:0;background:white}figure{border-radius:0}.report-chart{min-width:0}}</style></head><body>'
            '<p>RESIDUAL · Воспроизводимая аналитическая справка</p>'
            + ('<p class="sample"><strong>Учебные или синтетические данные</strong><br>Результаты не являются официальными оценками населения.</p>' if synthetic else "")
            + "<h1>" + escape(report["question"]) + "</h1><p>" + escape(dataset["label"]) + " · " + escape(report["created_at"]) + "</p>"
            + "".join("<p>" + escape(line) + "</p>" for line in report["summary"])
            + "<h2>Сравнение значений</h2>" + "".join(TabularService._html_chart(report, m) for m in metrics)
            + '<h2>Числа и охват</h2><div class="table-scroll"><table><thead>' + head + "</thead><tbody>" + body + "</tbody></table></div>"
            + "<h2>Методика и качество</h2><p>" + escape(method) + "</p><p>" + escape(report["methodology"]) + "</p>"
            + "<p>Всего строк: " + escape(report["quality"]["total_rows"]) + "; без категории группировки: " + escape(report["quality"]["grouping_missing_rows"]) + ".</p>"
            + "".join("<p>" + escape(report["formulas"][m]["label"]) + ": нечисловых значений — " + escape(report["quality"]["invalid_numeric"].get(m, 0)) + ".</p>" for m in metrics)
            + "<h2>Ограничения</h2><ul>" + "".join("<li>" + escape(line.replace("valid_counts", "число использованных строк")) + "</li>" for line in report["limitations"]) + "</ul>"
            + "<h2>Источники и повторение расчёта</h2>" + "".join("<p>" + escape(source["filename"]) + " · " + escape(source["table"]) + "<br><code>SHA-256 " + escape(source["sha256"]) + "</code></p>" for source in report["sources"])
            + "<p>Единица наблюдения: " + escape(dataset["observation_unit"]) + "; период: " + escape(dataset["period"] or "не указан") + "</p><p>Планировщик: " + escape(report["plan"]["model"]) + ". ИИ использован только для плана.</p>"
            + '<p>Для повторения чисел используйте выгрузки «Python» и «CSV для Python» этой же справки. Скрипт проверяет контрольную сумму источника; модель для повторного расчёта не нужна.</p>'
            + "<p>Код расчёта: <code>" + escape(report["calculation_code_sha256"]) + "</code></p></body></html>")
