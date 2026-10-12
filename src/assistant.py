"""Offline case explanations grounded in supplied values and a curated source registry.

This module is deterministic evidence assembly, not an LLM or a retrieval model.
Isolation Forest lives in the quality engine. No data leaves the local process.
"""
from copy import deepcopy
import math
import re


SOURCE_VERSION = '2026-10-12.1'
SOURCES = {
    'D006-AREA': {
        'id': 'D006-AREA', 'title': 'D006: инструкция, площадь жилья',
        'type': 'survey_document',
        'locator': 'documentation/d006/2024/D006_deid/Анкета Д006 рус.doc · инструкция, пункт 7 (вопрос 3)',
        'excerpt': 'В общую (полезную) площадь включается как жилая площадь, так и площадь кухни, коридора, ванной, туалета, кладовок и других вспомогательных комнат.',
        'note': 'Документ из конкурсного архива за 2024 год. Актуальность для другого обследования или периода не установлена.',
    },
    'D006-ROOMS': {
        'id': 'D006-ROOMS', 'title': 'D006: инструкция, жилые комнаты',
        'type': 'survey_document',
        'locator': 'documentation/d006/2024/D006_deid/Анкета Д006 рус.doc · инструкция, пункт 8 (вопрос 4)',
        'excerpt': 'В вопросе 4 проставляется количество всех жилых комнат в жилом помещении, кроме кухни, ванны, туалета, прихожей, кладовых.',
    },
    'D006-FIELDS': {
        'id': 'D006-FIELDS', 'title': 'D006: соответствие полей вопросам',
        'type': 'survey_document',
        'locator': 'documentation/d006/2024/D006_deid/структураД006.doc · вопросы 3–4',
        'excerpt': 'В вопросе 3: Значность-3 знака, поля Ob_pl, J_pl – ввод общей и жилой площади. В вопросе 4: поле Kol_k — количество жилых комнат.',
        'note': 'Краткая выдержка; имена полей в CSV приведены к верхнему регистру.',
    },
    'D008-FIELDS': {
        'id': 'D008-FIELDS', 'title': 'D008: код домохозяйства и состав',
        'type': 'survey_document',
        'locator': 'documentation/d008/2024/D008_2024/Структура D008.doc · NOMER, NOMP, KOL_CHL',
        'excerpt': 'Nomer — код домохозяйства; Kol_chl — количество членов домохозяйства; Nomp — порядковый номер.',
    },
    'TUTORIAL-KEYS': {
        'id': 'TUTORIAL-KEYS', 'title': 'Учебник организаторов: связь таблиц',
        'type': 'organiser_guidance',
        'locator': 'datathon_tutorial (1).html · Synthetic microdata: a working tutorial, 05.10.2026 · NOMER',
        'excerpt': 'NOMER соединяет D008, D006, D002 и D004 в пределах одного года. Межгодовой панели домохозяйств в архиве нет.',
        'note': 'Перевод смысла соответствующих фрагментов учебника.',
    },
    'RESIDUAL-RULES': {
        'id': 'RESIDUAL-RULES', 'title': 'Residual: правила проверки MVP',
        'type': 'project_rule',
        'locator': 'Residual · набор правил D006 + D008 · версия 0.3',
        'excerpt': 'J_PL > OB_PL — противоречие определениям площадей. Пропуски, неположительные значения, ошибки ключей и расхождения состава требуют проверки применимости и первичного источника. Сигнал не задаёт правильного значения.',
        'note': 'Реализация команды на основе структуры форм; это не утверждённый ведомственный регламент контроля.',
    },
    'RESIDUAL-METHOD': {
        'id': 'RESIDUAL-METHOD', 'title': 'Residual: интерпретация необычных значений',
        'type': 'methodology',
        'locator': 'Residual · локальный анализ D006 + D008 · версия 0.3',
        'excerpt': 'Робастное отклонение и Isolation Forest выделяют необычность относительно текущего массива. Процентиль — позиция оценки в массиве, а не вероятность ошибки. Квартили описывают сравниваемые данные, а не нормативные границы.',
        'note': 'Показанные отличия признаков являются описательным контекстом, а не причинным объяснением решения Isolation Forest.',
    },
}
for _source in SOURCES.values():
    _source['version'] = SOURCE_VERSION

NAMES = {
    'OB_PL': 'Общая площадь', 'J_PL': 'Жилая площадь', 'KOL_K': 'Жилые комнаты',
    'household_size': 'Число членов домохозяйства', 'declared_size': 'Заявленное число членов',
    'area_per_person': 'Площадь на человека', 'area_per_room': 'Площадь на комнату',
    'living_share': 'Доля жилой площади',
}
UNITS = {'OB_PL': ' м²', 'J_PL': ' м²', 'area_per_person': ' м²', 'area_per_room': ' м²'}


def _text(value, limit=500):
    return re.sub(r'[\x00-\x1f\x7f]', ' ', str(value))[:limit]


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (ValueError, TypeError):
        return None


def _fmt(value):
    parsed = _number(value)
    return 'не указано' if parsed is None else f'{parsed:,.3f}'.rstrip('0').rstrip('.').replace(',', ' ').replace('.', ',')


def _action(action, question):
    aliases = {'sources': 'source', 'evidence': 'source', 'draft': 'request', 'clarify': 'request',
               'explanation': 'explain'}
    action = aliases.get(action, action)
    if not question:
        return action if action in {'explain', 'source', 'request'} else 'unsupported'
    q = question.casefold()
    if any(word in q for word in ('поставь', 'измени', 'исправь', 'замени', 'игнорируй', 'ignore', 'забудь', 'delete')):
        return 'correction'
    if any(word in q for word in ('источник', 'инструкц', 'основан', 'документ', 'правило')):
        return 'source'
    if any(word in q for word in ('запрос', 'сверк', 'уточн', 'что делать', 'следующ')):
        return 'request'
    if any(word in q for word in ('почему', 'объясн', 'сигнал', 'необыч', 'площад', 'комнат', 'состав', 'ошибк', 'процентил')):
        return 'explain'
    return 'unsupported'


def _record_source(record):
    locator = _text(record.get('source') or 'Загруженный пакет D006 + D008', 500)
    rows = record.get('source_rows')
    locations = []
    if isinstance(rows, dict):
        for key, form in (('housing', 'D006'), ('d006', 'D006'), ('roster', 'D008'), ('d008', 'D008')):
            if key not in rows:
                continue
            entry = rows[key]
            filename = 'загруженный CSV'
            if isinstance(entry, dict):
                filename = _text(entry.get('file') or filename)
                numbers = entry.get('rows', entry.get('row', []))
            else:
                numbers = entry
            if not isinstance(numbers, (list, tuple)):
                numbers = [numbers]
            numbers = [_text(number) for number in numbers if number is not None]
            detail = ('строка ' if len(numbers) == 1 else 'строки ') + ', '.join(numbers) if numbers else 'строки не указаны'
            locations.append(f'{form} — {filename}: {detail}')
    if locations:
        locator = '; '.join(locations)
    values = record.get('values') or {}
    if not isinstance(values, dict):
        values = {}
    excerpt = '; '.join(f'{name}: {_fmt(values.get(field))}{UNITS.get(field, "")}'
                        for field, name in NAMES.items() if field in values)
    return {'id': 'CASE-INPUT', 'title': f'Исходные значения: {_text(record.get("id", "без идентификатора"), 100)}',
            'type': 'input_data', 'locator': locator, 'excerpt': excerpt or 'Значения полей не переданы.',
            'version': _text(record.get('batch_id') or record.get('batch') or 'текущий пакет', 100)}


def assist_case(record: dict, action: str = 'explain', question: str = '') -> dict:
    """Explain only this case; prepare a draft but never change data or send messages.

    Sources label provenance explicitly. Free text selects a narrow supported intent;
    it is never executed or passed to a language model.
    """
    if not isinstance(record, dict):
        raise ValueError('record must be a dictionary')
    action = _action(str(action), str(question).strip())
    values = record.get('values') or {}
    if not isinstance(values, dict):
        values = {}
    parsed = {field: _number(values.get(field)) for field in ('OB_PL', 'J_PL', 'KOL_K', 'household_size')}
    total, living = parsed['OB_PL'], parsed['J_PL']
    area_conflict = total is not None and living is not None and living > total
    missing = [field for field, value in parsed.items() if value is None]
    nonpositive = [field for field, value in parsed.items() if value is not None and value <= 0]
    kind = str(record.get('kind', ''))
    statistical = kind in ('ml', 'statistical', 'robust')
    signals = ' '.join(str(value).lower() for value in record.get('signal_ids', []))
    link_issue = any(term in signals for term in ('roster', 'duplicate', 'join', 'unmatched', 'key', 'size_mismatch'))
    source_ids = ['D006-FIELDS', 'RESIDUAL-RULES']
    statements = []
    if area_conflict:
        statements.append(f'Жилая площадь {_fmt(living)} м² превышает общую {_fmt(total)} м² на {_fmt(living-total)} м². '
                          'Общая площадь включает жилую по определению в инструкции D006 [D006-AREA]. '
                          'Сочетание требует сверки; по этим значениям нельзя установить, какое поле верно.')
        source_ids.insert(0, 'D006-AREA')
    if missing:
        statements.append('Не хватает пригодных числовых значений: ' + ', '.join(NAMES[f] for f in missing) + '. '
                          'Нужно выяснить применимость вопроса, наличие ответа и результат соединения таблиц. '
                          'Пропуск сам по себе не доказывает ошибку респондента [RESIDUAL-RULES].')
    if nonpositive:
        statements.append('Нулевое или отрицательное значение: ' + ', '.join(NAMES[f] for f in nonpositive) + '. '
                          'Проверьте кодирование, применимость и первичный источник; автоматическая замена не обоснована [RESIDUAL-RULES].')
    if 'fractional_rooms' in signals or (parsed['KOL_K'] is not None and parsed['KOL_K'] % 1 != 0):
        statements.append(f'Указано дробное число комнат: {_fmt(parsed["KOL_K"])}. '
                          'Сверьте запись и способ кодирования числа жилых комнат [D006-ROOMS]. '
                          'Округление без первичного источника не обосновано.')
        source_ids.append('D006-ROOMS')
    if 'roster_size' in signals:
        declared = record.get('declared_household_sizes') or []
        declared_text = ', '.join(_fmt(value) for value in declared) if declared else 'не указано'
        statements.append(f'По строкам D008 рассчитано членов домохозяйства: {_fmt(parsed["household_size"])}; '
                          f'значения KOL_CHL в карточке: {declared_text}. '
                          'Проверьте полноту состава, пропуски и согласованность заявленного количества. '
                          'Этот диагностический сигнал не определяет, какую строку нужно добавить или удалить [RESIDUAL-RULES].')
    if link_issue:
        statements.append('Сначала проверьте ключи и состав домохозяйства в D008: NOMER обозначает домохозяйство, '
                          'NOMP — порядковый номер, KOL_CHL — число членов [D008-FIELDS]. '
                          'Соединение конкурсных D-форм допустимо в пределах одного года [TUTORIAL-KEYS].')
        source_ids += ['D008-FIELDS', 'TUTORIAL-KEYS']
    if statistical:
        label = 'моделью Isolation Forest' if kind == 'ml' else 'статистическим ранжированием'
        statements.append(f'Запись отобрана {label} для просмотра. Необычность относительно текущего массива '
                          'может быть допустимым случаем и не подтверждает ошибку [RESIDUAL-METHOD].')
        percentile = _number(record.get('percentile', record.get('ml_percentile' if kind == 'ml' else 'robust_percentile')))
        if percentile is not None:
            statements.append(f'Процентиль оценки: {_fmt(percentile)}. Это относительная позиция в массиве, '
                              'а не вероятность ошибки [RESIDUAL-METHOD].')
        for item in (record.get('evidence') or [])[:3]:
            if not isinstance(item, dict):
                continue
            field = item.get('field', '')
            if field not in NAMES or _number(item.get('value')) is None:
                continue
            if all(_number(item.get(key)) is not None for key in ('q25', 'q75', 'median')):
                statements.append(f'{NAMES[field]}: {_fmt(item["value"])}{UNITS.get(field, "")}; '
                                  f'медиана сравнения {_fmt(item["median"])}, центральные 50% значений '
                                  f'{_fmt(item["q25"])}–{_fmt(item["q75"])}. Это описание массива, '
                                  'а не допустимый норматив или доказательство причины сигнала модели [RESIDUAL-METHOD].')
        source_ids.append('RESIDUAL-METHOD')
    if not statements:
        statements.append('По переданным значениям противоречие площадей, пропуск или неположительное значение не установлены. '
                          'Это не полная гарантия качества: помощник проверяет только доступный контекст случая.')
    if parsed['KOL_K'] is not None:
        source_ids.append('D006-ROOMS')
    if parsed['household_size'] is not None:
        source_ids.append('D008-FIELDS')
    steps = [
        'Сверьте выделенные поля и код домохозяйства с первичным источником и применимостью вопроса.',
        'При необходимости подготовьте уточнение ответственному специалисту по установленному в организации процессу.',
        'Зафиксируйте решение и основание. При исправлении данных загрузите уточнённый пакет для повторной проверки.',
    ]
    if statistical and not area_conflict and not missing and not nonpositive:
        steps.insert(1, 'Если редкое значение подтверждено первичным источником, сохраните решение «Допустимо» и пояснение.')
    response = {
        'title': 'Разбор случая', 'answer': '\n\n'.join(statements), 'steps': steps,
        'sources': [_record_source(record)] + [deepcopy(SOURCES[s]) for s in dict.fromkeys(source_ids)],
        'mode': 'local_evidence', 'action': action, 'version': SOURCE_VERSION,
        'disclosure': 'Локальное объяснение рассчитанных фактов и заранее проверенных выдержек. Без генеративной модели и внешней передачи данных.',
    }
    if action == 'source':
        response['title'] = 'Основания проверки'
        response['answer'] = ('Определения полей взяты из документов конкурсного архива 2024 года. '
                              'Отдельно отмечены правила команды и описание метода; они не являются ведомственным регламентом.\n\n'
                              + response['answer'])
    elif action == 'request':
        response['title'] = 'Черновик запроса на сверку'
        lines = [f'Просьба сверить запись {_text(record.get("id", "без идентификатора"), 100)} по первичному источнику.']
        if area_conflict:
            lines.append(f'В D006 указаны общая площадь {_fmt(total)} м² и жилая площадь {_fmt(living)} м². '
                         'Жилая площадь превышает общую. Просьба уточнить оба значения и единицу измерения.')
        if 'roster_size' in signals:
            lines.append(f'В D008 по строкам домохозяйства рассчитано членов: {_fmt(parsed["household_size"])}; '
                         f'значения KOL_CHL в карточке: {declared_text}. '
                         'Просьба сверить полноту состава и заявленное число членов, подтвердить значения '
                         'или предоставить уточнение с основанием. По этому сигналу нельзя определить, '
                         'какую строку нужно добавить или удалить.')
        if statistical:
            lines.append('Запись выделена как статистически необычная. Это не означает, что ответ ошибочен. '
                         'Просьба подтвердить значения или предоставить уточнение.')
        elif not area_conflict and 'roster_size' not in signals:
            lines.append('Просьба уточнить применимость вопросов, наличие значений и корректность связи D006 с D008.')
        lines.append('; '.join(f'{NAMES[f]}: {_fmt(values.get(f))}{UNITS.get(f, "")}' for f in parsed) + '.')
        lines.append('Источник записи: ' + _record_source(record)['locator'] + '.')
        lines.append('По имеющимся данным правильное значение не установлено. Просьба указать основание подтверждения или уточнения.')
        response['draft'] = '\n\n'.join(lines)
        response['answer'] = 'Черновик подготовлен по данным выбранного случая. Проверьте его перед использованием. Ничего не отправлено.'
    elif action == 'correction':
        response.update(title='Нужно подтверждение первичного значения',
                        answer='По сигналу нельзя определить правильное значение. Помощник не изменяет запись и не придумывает исправление. '
                               'Сначала сверьте первичный источник, затем зафиксируйте решение специалиста.', steps=steps[:1])
    elif action == 'unsupported':
        response.update(title='Вопрос вне контекста случая',
                        answer='Помощник работает только с выбранной записью: объясняет сигнал, показывает основания и готовит запрос на сверку. '
                               'Для этого вопроса проверенного ответа в доступном контексте нет.', steps=[])
    return response
