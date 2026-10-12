"""Persistent local workspaces, background analysis, and review history."""
from copy import deepcopy
import csv
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import re
from threading import Lock, Thread
import uuid

STATUSES = {'pending', 'needs_check', 'accepted', 'confirmed'}
STATUS_NAMES = {'pending': 'Не рассмотрено', 'needs_check': 'Нужна сверка',
                'accepted': 'Допустимо', 'confirmed': 'Ошибка подтверждена'}
ID_PATTERN = re.compile(r'^[a-f0-9]{32}$')


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def read_json(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else deepcopy(default)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def compare_packages(previous, current):
    """Compare signals and values for identical keys within the same period only."""
    result = dict(available=False, baseline_id=None, changed_count=0, resolved_count=0,
                  new_count=0, added_count=0, added_with_signals_count=0,
                  unchanged_count=0, missing_count=0, items=[])
    if not previous:
        return result
    if previous['year'] != current['year']:
        result['message'] = 'Разные периоды: сопоставление домохозяйств между годами недопустимо.'
        return result
    result.update(available=True, baseline_id=previous['run_id'])
    before = previous.get('all_records_by_id', {})
    after = current.get('all_records_by_id', {})
    old_queue = {r['id']: r for r in previous['records']}
    new_queue = {r['id']: r for r in current['records']}
    soft = {'IFOREST_CANDIDATE', 'ROBUST_OUTLIER'}
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if old and not new:
            change, description = 'missing', 'Запись отсутствует в новой версии; это не подтверждение исправления.'
        elif not old:
            change, description = 'added', 'Новая запись в пакете.'
            if set(new.get('signal_ids', [])) - soft:
                result['added_with_signals_count'] += 1
                description += ' В ней обнаружены сигналы проверяемых условий.'
        else:
            # Dropping out of a model's top-K is not a resolved data defect.
            old_signals = set(old.get('signal_ids', [])) - soft
            new_signals = set(new.get('signal_ids', [])) - soft
            values_changed = (old.get('values') != new.get('values') or
                              old.get('declared_household_sizes') != new.get('declared_household_sizes'))
            if old_signals and not new_signals:
                change, description = 'resolved', 'Ранее отмеченные нарушения правил больше не обнаруживаются.'
            elif new_signals - old_signals:
                change, description = 'new', 'Появилось новое нарушение проверяемого условия.'
            elif values_changed or old_signals != new_signals:
                change, description = 'changed', 'Значения или состав сигналов изменились; требуется новый разбор.'
            else:
                change, description = 'unchanged', 'Проверяемые значения и нарушения правил не изменились.'
        result[change + '_count'] += 1
        # Keep the report readable while counts cover every record in both packages.
        if change != 'unchanged' or key in old_queue or key in new_queue:
            result['items'].append(dict(id=key, change=change, description=description,
                                        before=old, after=new))
    return result


def normalize_comparison(comparison):
    """Update the display contract of saved comparisons without rewriting the saved run."""
    result = deepcopy(comparison or {'available': False})
    if 'added_count' in result:
        return result
    result.update(added_count=0, added_with_signals_count=0)
    if not result.get('available'):
        return result
    soft = {'IFOREST_CANDIDATE', 'ROBUST_OUTLIER'}
    for item in result.get('items', []):
        if item.get('change') == 'new' and item.get('before') is None and item.get('after') is not None:
            item['change'] = 'added'
            item['description'] = 'Новая запись в пакете.'
            result['added_count'] += 1
            if set(item['after'].get('signal_ids', [])) - soft:
                result['added_with_signals_count'] += 1
                item['description'] += ' В ней обнаружены сигналы проверяемых условий.'
    result['new_count'] = sum(item.get('change') == 'new' for item in result.get('items', []))
    # Unchanged rows may be omitted from items, so preserve their original full count.
    return result


class Workspace:
    def __init__(self, root, data_dir=None):
        self.root = Path(root)
        self.store = self.root / 'private_outputs' / 'workspace'
        self.data_dir = Path(data_dir) if data_dir else self.root.parent / 'synthetic_microdata_2021-2024_20260921'
        self.lock, self.analysis_lock = Lock(), Lock()
        self.jobs = {}

    def index(self):
        return read_json(self.store / 'index.json', {'current_id': None, 'runs': []})

    def package(self, run_id=None):
        run_id = run_id or self.index()['current_id']
        if not run_id:
            return None
        if not isinstance(run_id, str) or not ID_PATTERN.fullmatch(run_id):
            raise ValueError('Некорректный идентификатор версии.')
        package = read_json(self.store / 'runs' / run_id / 'result.json')
        if package is None:
            raise ValueError('Версия не найдена.')
        return package

    def reviews(self, run_id):
        return read_json(self.store / 'runs' / run_id / 'reviews.json', {'current': {}, 'history': []})

    def state(self, run_id=None):
        with self.lock:
            package = self.package(run_id)
            index = self.index()
            if package is None:
                return dict(summary={}, records=[], checks=[], sources=[], runs=index['runs'],
                            comparison={'available': False}, mode='empty', assistant_mode='local_evidence')
            result = {k: v for k, v in package.items() if k not in {'all_records_by_id'}}
            result['comparison'] = normalize_comparison(package.get('comparison'))
            reviews = self.reviews(package['run_id'])
            for record in result['records']:
                review = reviews['current'].get(record['id'], {})
                record['review'] = review
                record['status'] = review.get('status', 'pending')
            result.update(runs=index['runs'], mode='local', assistant_mode='local_evidence',
                          review_history=reviews['history'])
            result['summary']['reviewed_count'] = sum(r['status'] != 'pending' for r in result['records'])
            # Reference only: never report the old benchmark as measured on an imported package.
            old = read_json(self.root / 'reports' / 'quality_summary.json', {})
            result['reference_benchmark'] = old.get('benchmark', {})
            result['workspace_benchmark'] = read_json(self.root / 'reports' / 'workspace_benchmark.json', {})
            return result

    def demo_csv(self, form):
        names = {'d006': 'd006/2024/rio_2024.csv', 'd008': 'd008/2024/kontr_k.csv'}
        if form not in names:
            raise ValueError('Выберите D006 или D008.')
        path = self.data_dir / names[form]
        if not path.is_file():
            raise ValueError('Демонстрационный архив не найден. Загрузите D006 и D008 вручную.')
        return path.read_text(encoding='utf-8-sig')

    def start_demo(self, body):
        return self.start_analysis(dict(housing_csv=self.demo_csv('d006'), roster_csv=self.demo_csv('d008'),
                                       year=2024, label=body.get('label') or 'БНС · синтетические данные 2024',
                                       base_run_id=body.get('base_run_id')), demo=True)

    def start_analysis(self, body, demo=False, demo_note=None):
        housing, roster = body.get('housing_csv'), body.get('roster_csv')
        if not isinstance(housing, str) or not isinstance(roster, str) or not housing.strip() or not roster.strip():
            raise ValueError('Нужны два непустых CSV: D006 (жильё) и D008 (состав домохозяйств).')
        if len(housing.encode('utf-8')) + len(roster.encode('utf-8')) > 16 * 1024 * 1024:
            raise ValueError('Общий размер файлов превышает 16 МБ для локального MVP.')
        try:
            year = int(body.get('year', 2024))
        except (ValueError, TypeError):
            raise ValueError('Укажите год обследования.')
        if year != 2024:
            raise ValueError('В этом MVP проверены формы и правила только за 2024 год.')
        label = str(body.get('label') or 'Загруженный пакет').strip()[:120]
        with self.lock:
            baseline_id = body.get('base_run_id')
            baseline = self.package(baseline_id) if baseline_id else None
        if not self.analysis_lock.acquire(blocking=False):
            raise ValueError('Один пакет уже проверяется. Дождитесь завершения текущей проверки.')
        job_id = uuid.uuid4().hex
        with self.lock:
            self.jobs[job_id] = dict(status='running', stage='validate',
                                     message='Проверка структуры, ключей и значений…')

        def work():
            try:
                from .workspace import analyze_package
                with self.lock:
                    self.jobs[job_id].update(stage='analyze', message='Применение правил, обучение модели и подготовка оснований…')
                package = analyze_package(housing, roster, year=year, label=label)
                run_id = uuid.uuid4().hex
                package.update(run_id=run_id, label=label, year=year, created_at=utc_now(),
                               is_demo=demo, demo_note=demo_note, base_run_id=baseline_id)
                package['comparison'] = compare_packages(baseline, package)
                with self.lock:
                    folder = self.store / 'runs' / run_id
                    folder.mkdir(parents=True, exist_ok=True)
                    (folder / 'd006.csv').write_text(housing, encoding='utf-8')
                    (folder / 'd008.csv').write_text(roster, encoding='utf-8')
                    write_json(folder / 'result.json', package)
                    index = self.index()
                    summary = {k: package['summary'].get(k, 0) for k in
                               ['housing_rows', 'rule_flags', 'validation_flags', 'queue_size', 'ml_queue']}
                    index['runs'].insert(0, dict(id=run_id, run_id=run_id, label=label, year=year,
                                                created_at=package['created_at'], summary=summary, is_demo=demo))
                    index['current_id'] = run_id
                    write_json(self.store / 'index.json', index)
                    self.jobs[job_id].update(status='done', stage='complete', message='Проверка завершена', run_id=run_id)
            except Exception as exc:
                with self.lock:
                    self.jobs[job_id].update(status='error', stage='error', error=str(exc), message=str(exc))
            finally:
                self.analysis_lock.release()

        Thread(target=work, daemon=True).start()
        return {'job_id': job_id}

    def job(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise ValueError('Проверка не найдена. Если сервер перезапускался, откройте последнюю версию.')
            return deepcopy(self.jobs[job_id])

    def review(self, body):
        status, comment = body.get('status'), body.get('comment', '')
        if status not in STATUSES:
            raise ValueError('Неизвестный статус решения.')
        if not isinstance(comment, str) or len(comment) > 2000:
            raise ValueError('Комментарий должен содержать не более 2000 символов.')
        if status == 'confirmed' and not comment.strip():
            raise ValueError('Для подтверждённой ошибки укажите основание в комментарии.')
        with self.lock:
            package = self.package(body.get('run_id'))
            if not package or body.get('id') not in {r['id'] for r in package['records']}:
                raise ValueError('Запись не найдена в выбранной версии.')
            reviews = self.reviews(package['run_id'])
            review = dict(id=body['id'], status=status, comment=comment.strip(), updated_at=utc_now(),
                          run_id=package['run_id'])
            reviews['current'][body['id']] = review
            reviews['history'].append(review)
            write_json(self.store / 'runs' / package['run_id'] / 'reviews.json', reviews)
            return dict(ok=True, review=review)

    def assistant(self, body):
        from .assistant import assist_case
        package = self.state(body.get('run_id'))
        record = next((r for r in package['records'] if r['id'] == body.get('id')), None)
        if record is None:
            raise ValueError('Откройте запись перед обращением к помощнику.')
        question = body.get('question', '')
        if not isinstance(question, str) or len(question) > 1000:
            raise ValueError('Вопрос должен содержать не более 1000 символов.')
        return assist_case(record, action=body.get('action', 'explain'), question=question)

    def demo_recheck(self, body):
        with self.lock:
            package = self.package(body.get('run_id'))
            if not package or not package.get('is_demo'):
                raise ValueError('Учебная копия доступна только для демонстрационных данных.')
            record = next((r for r in package['records'] if r['id'] == body.get('id')), None)
            if not record or not (record['values'].get('J_PL', 0) > record['values'].get('OB_PL', 0)):
                raise ValueError('Выберите случай, где жилая площадь больше общей.')
            folder = self.store / 'runs' / package['run_id']
            housing = (folder / 'd006.csv').read_text(encoding='utf-8')
            roster = (folder / 'd008.csv').read_text(encoding='utf-8')
        # This is an explicitly labelled counterfactual exercise, never a proposed real correction.
        reader = csv.DictReader(io.StringIO(housing))
        fieldnames = reader.fieldnames
        rows = list(reader)
        for row in rows:
            if row['NOMER'] == record['id']:
                row['J_PL'] = str(round(float(row['OB_PL']) * .6, 2))
        output = io.StringIO(newline='')
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader(); writer.writerows(rows)
        note = ('Учебная копия: для записи ' + record['id'] +
                ' условно задана жилая площадь 60% от общей. Значение придумано для демонстрации повторной проверки; '
                'это не установленное исправление первоисточника. Исходный пакет сохранён.')
        return self.start_analysis(dict(housing_csv=output.getvalue(), roster_csv=roster, year=2024,
                                       label='Учебная копия · после условной сверки', base_run_id=package['run_id']),
                                   demo=True, demo_note=note)

    def export(self, run_id=None, format='json'):
        state = self.state(run_id)
        if not state.get('run_id'):
            raise ValueError('Сначала выполните проверку пакета.')
        if format == 'json':
            return json.dumps(state, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8'), 'application/json', 'residual-review.json'
        if format not in {'csv', 'comparison_csv'}:
            raise ValueError('Доступны форматы JSON, CSV журнала и CSV сравнения.')
        def safe(value):
            value = str(value if value is not None else '')
            return "'" + value if value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else value
        output = io.StringIO(newline='')
        writer = csv.writer(output, delimiter=';')
        if format == 'comparison_csv':
            comparison = state.get('comparison') or {}
            if not comparison.get('available'):
                raise ValueError('Для выгрузки сравнения сначала проверьте пакет с выбранной исходной версией.')
            fields = [('OB_PL', 'Общая площадь'), ('J_PL', 'Жилая площадь'),
                      ('KOL_K', 'Комнаты'), ('household_size', 'Размер семьи')]
            labels = {'added': 'Новая запись', 'new': 'Новый сигнал', 'resolved': 'Сигнал устранён',
                      'changed': 'Изменено', 'unchanged': 'Без изменений', 'missing': 'Нет в новом пакете'}
            writer.writerow(['Исходная версия', 'Новая версия', 'Пакет', 'NOMER', 'Изменение',
                             'Описание', 'Сигналы до', 'Сигналы после'] +
                            [f'{label} {when}' for _, label in fields for when in ('до', 'после')] +
                            ['KOL_CHL до', 'KOL_CHL после'])
            for item in comparison.get('items', []):
                before, after = item.get('before') or {}, item.get('after') or {}
                values_before, values_after = before.get('values', {}), after.get('values', {})
                # Older saved results classified an added row as "new" even without a signal.
                change = 'added' if item.get('change') == 'new' and item.get('before') is None else item.get('change')
                row = [comparison.get('baseline_id'), state['run_id'], state['label'], item.get('id'),
                       labels.get(change, change), item.get('description'),
                       ' | '.join(before.get('signal_ids', [])), ' | '.join(after.get('signal_ids', []))]
                row += [values.get(field) for field, _ in fields for values in (values_before, values_after)]
                row += [' | '.join('не указано' if value is None else str(value)
                                  for value in record.get('declared_household_sizes', []))
                        for record in (before, after)]
                writer.writerow([safe(value) for value in row])
            return output.getvalue().encode('utf-8-sig'), 'text/csv', 'residual-comparison.csv'
        writer.writerow(['Версия', 'Пакет', 'NOMER', 'Тип сигнала', 'Общая площадь', 'Жилая площадь',
                         'Комнаты', 'Размер семьи', 'Основания', 'Решение', 'Комментарий', 'Время решения'])
        for record in state['records']:
            values, review = record.get('values', {}), record.get('review', {})
            writer.writerow([safe(v) for v in [state['run_id'], state['label'], record['id'], record['kind'],
                              values.get('OB_PL'), values.get('J_PL'), values.get('KOL_K'), values.get('household_size'),
                              ' | '.join(record.get('reasons', [])), STATUS_NAMES[record['status']],
                              review.get('comment'), review.get('updated_at')]])
        return output.getvalue().encode('utf-8-sig'), 'text/csv', 'residual-review.csv'
