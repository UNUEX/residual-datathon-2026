'use strict';

const root = document.querySelector('#root');
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const list = value => Array.isArray(value) ? value : value ? [value] : [];
const number = value => value === null || value === undefined || value === '' || !Number.isFinite(Number(value)) ? '—' : Number(value).toLocaleString('ru-RU',{maximumFractionDigits:2});
const date = value => {const parsed = new Date(value);return !value || Number.isNaN(parsed.getTime()) ? '' : parsed.toLocaleString('ru-RU',{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'});};
const paths = {home:'M3 11l9-8 9 8 M5 10v11h5v-7h4v7h5V10',arrow:'M4 12h16 M14 6l6 6-6 6',plus:'M12 5v14 M5 12h14',check:'M5 12l4 4L19 6',upload:'M12 16V3 M7 8l5-5 5 5 M4 17v4h16v-4',download:'M12 3v13 M7 11l5 5 5-5 M4 17v4h16v-4',data:'M4 4h16v16H4z M4 9h16 M9 4v16',history:'M3 11a9 9 0 1 1 2 7 M3 4v7h7 M12 7v5l3 2',settings:'M4 7h16 M4 17h16 M8 4v6 M16 14v6',close:'M6 6l12 12 M18 6L6 18',info:'M12 10v7 M12 7h.01 M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',spark:'M12 3l2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z',file:'M14 3H5v18h14V8z M14 3v6h5 M8 13h8 M8 17h5'};
const icon = name => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[name] || paths.info}"/></svg>`;
const defaults = {provider:'ollama',base_url:'http://127.0.0.1:11434',model:'qwen3:1.7b',allow_external:false};
const roles = {number:'Числовой показатель',category:'Группа / категория',identifier:'Идентификатор',ignore:'Не использовать'};
const aggregation = value => ({mean:'Среднее арифметическое',median:'Медиана',sum:'Сумма'}[value] || value);
const formulaDescription = value => ({mean:'Сумма пригодных значений, делённая на их количество.',median:'Среднее из двух центральных значений упорядоченного ряда, если значений чётное число; центральное значение — если нечётное.',sum:'Сумма всех пригодных значений показателя.'}[value] || 'Расчёт по подтверждённому плану.') + ' Если пригодных значений нет, результат не определён.';
const humanText = text => esc(String(text).replaceAll('valid_counts','число использованных строк'));
let state = null;
const views = {home:'Главная',data:'Данные',analysis:'Исследование',reports:'Справки',model:'Модель'};
const initialView = () => Object.hasOwn(views,location.hash.slice(1)) ? location.hash.slice(1) : 'home';
const ui = {view:initialView(),datasetId:'',resumeDatasetId:'',question:'',plan:null,report:null,savedReport:null,showReportList:false,chartMetric:'',busy:'',error:'',notice:'',probe:null,config:{...defaults},configDirty:false,importing:false,inspection:null,tableId:'',headerRow:1,preview:null,passport:null,confirmed:false};
const dataset = () => list(state?.datasets).find(item => item.id === ui.datasetId);
const disabled = condition => ui.busy || condition ? 'disabled' : '';
const currentReport = () => ui.view === 'reports' ? ui.savedReport || ui.report : ui.report;
const metricInfo = id => currentReport()?.formulas?.[id] || list(currentReport()?.plan?.metric_details || ui.plan?.metric_details).find(item => item.id === id) || list(currentReport()?.dataset?.columns || dataset()?.columns).find(item => item.id === id) || {id,label:id,unit:''};

async function api(path,body,section='tables') {
 const response = await fetch(`/api/${section}/${path}`,{method:body === undefined ? 'GET' : 'POST',headers:body === undefined ? {} : {'Content-Type':'application/json'},body:body === undefined ? undefined : JSON.stringify(body)});
 let result;
 try {result = await response.json();} catch {throw new Error('Не удалось прочитать ответ приложения. Проверьте, что локальный сервер запущен.');}
 if (!response.ok || result.error) throw new Error(result.error || result.message || `Не удалось выполнить действие (${response.status}).`);
 return result;
}
async function refresh() {
 state = await api('state');
 if (ui.datasetId && !list(state.datasets).some(item => item.id === ui.datasetId)) ui.datasetId = '';
 if (ui.resumeDatasetId && !list(state.datasets).some(item => item.id === ui.resumeDatasetId)) ui.resumeDatasetId = '';
 if (!ui.configDirty) ui.config = {...defaults,...state.provider};
}
function announce(text) {document.querySelector('#announcer').textContent = text;}
async function action(text,work) {
 if (ui.busy) return;
 ui.busy = text;ui.error = '';ui.notice = '';render();announce(text);
 try {await work();} catch(error) {ui.error = error.message || 'Не удалось выполнить действие.';}
 finally {ui.busy = '';render();announce(ui.error || ui.notice || 'Готово.');}
}
function resetResult(clearQuestion=false) {ui.plan = null;ui.report = null;ui.chartMetric = '';ui.error = '';ui.notice = '';if (clearQuestion) ui.question = '';}
function modelStatus() {return ui.probe?.ok ? {text:'Подключение проверено',kind:'ready'} : ui.probe ? {text:'Нет подтверждённого подключения',kind:'unavailable'} : {text:'Подключение не проверено',kind:''};}
function heading(step,title,text) {return `<div class="section-head"><span class="step-number">${step}</span><div><h2>${title}</h2><p>${text}</p></div></div>`;}
function warnings(values) {const items = list(values).filter(Boolean);return items.length ? `<ul class="warnings-list">${items.map(value => `<li>${esc(value)}</li>`).join('')}</ul>` : '';}

function goTo(view,updateHistory=true) {
 if (!Object.hasOwn(views,view)) return;
 ui.view = view;ui.error = '';ui.notice = '';
 if (updateHistory && location.hash !== '#'+view) history.pushState(null,'','#'+view);
 render();document.querySelector('#main-content')?.focus({preventScroll:true});window.scrollTo({top:0});
}
function setView(view) {
 ui.view = view;
 if (location.hash !== '#'+view) history.pushState(null,'','#'+view);
}
function header() {
 const status = modelStatus();
 return `<header class="header"><a class="brand" href="#home" aria-label="Residual — главная"><img src="/assets/residual-mark.png" width="40" height="40" alt=""><span><strong>residual</strong><small>Ясность в данных</small></span></a><span class="header-location">Рабочее пространство статистика</span><button class="model-trigger" data-view="model" ${disabled()}><span class="status-dot ${status.kind}"></span><span>${esc(state.provider?.model || defaults.model)}<small>${status.text}</small></span>${icon('settings')}</button></header>`;
}
function navigation() {
 const icons = {home:'home',data:'data',analysis:'spark',reports:'file',model:'settings'};
 return `<aside class="studio-dock"><a class="dock-brand" href="#home" aria-label="Residual — главная"><img src="/assets/residual-mark.png" alt="" width="38" height="38"></a><nav aria-label="Основная навигация">${Object.entries(views).map(([view,label]) => `<button class="dock-tab ${ui.view === view ? 'active' : ''}" data-view="${view}" ${ui.view === view ? 'aria-current="page"' : ''} ${disabled()}>${icon(icons[view])}<span>${label}</span></button>`).join('')}</nav><div class="dock-bottom">R / 01</div></aside>`;
}
function homePage() {
 const tables = list(state.datasets).length,reports = list(state.reports).length;
 return `<section class="home-hero" aria-labelledby="home-title"><div class="home-copy"><span class="eyebrow">ВАШ ЛОКАЛЬНЫЙ ИИ-АССИСТЕНТ</span><h1 id="home-title">Ваши данные.<br><em>Ясные выводы.</em></h1><p>От таблицы и вопроса — к справке с расчётами, графиком и методикой.</p><div class="hero-actions"><button class="btn primary" data-action="begin" ${disabled()}>Начать исследование ${icon('arrow')}</button><button class="text-button" data-action="example-excel" ${disabled()}>Попробовать пример ${icon('arrow')}</button></div><span class="hero-footnote">Учебный пример — на вымышленных данных</span></div><div class="home-art" aria-hidden="true"><span class="orbit-label">RESIDUAL / DATA TO INSIGHT</span><div class="orbit-ring ring-one"></div><div class="orbit-ring ring-two"></div><div class="hero-mark"><img src="/assets/residual-mark.png" alt="" width="250" height="250"></div><span class="orbit-point"></span></div></section><section class="home-shortcuts" aria-label="Ваши материалы"><button class="shortcut-card" data-view="data" ${disabled()}><span class="shortcut-icon">${icon('data')}</span><div><small>ВАШИ ИСТОЧНИКИ</small><strong>Мои таблицы</strong><span>${tables ? `Сохранено: ${number(tables)}` : 'Загрузите первый файл'}</span></div><span class="shortcut-arrow">${icon('arrow')}</span></button><button class="shortcut-card" data-view="reports" ${disabled()}><span class="shortcut-icon">${icon('file')}</span><div><small>РЕЗУЛЬТАТЫ РАБОТЫ</small><strong>Готовые справки</strong><span>${reports ? `Сохранено: ${number(reports)}` : 'Здесь появятся ваши расчёты'}</span></div><span class="shortcut-arrow">${icon('arrow')}</span></button></section>`;
}
function viewHeading(kicker,title,text,extra='') {
 return `<div class="view-heading"><div><span class="eyebrow">${kicker}</span><h1>${title}</h1><p>${text}</p></div>${extra}</div>`;
}
function emptyView(symbol,title,text,button,view) {
 return `<section class="panel empty-view">${icon(symbol)}<h2>${title}</h2><p>${text}</p><button class="btn primary" data-view="${view}" ${disabled()}>${button} ${icon('arrow')}</button></section>`;
}
function connectionPanel() {
 if (ui.probe?.ok) return '';
 return `<div class="connection-hint">${icon('info')}<span>${ui.probe ? 'Модель недоступна.' : 'Подключение модели ещё не проверено.'}</span><button class="text-button" data-view="model" ${disabled()}>Открыть настройки</button></div>`;
}
function savedDatasetChoice() {
 const saved = list(state.datasets);
 if (!saved.length) return '';
 return `<div class="saved-dataset-choice"><label class="field-label" for="saved-dataset-select">Сохранённые таблицы<select id="saved-dataset-select" ${disabled()}><option value="">Выберите набор</option>${saved.map(item => `<option value="${esc(item.id)}" ${item.id === ui.resumeDatasetId ? 'selected' : ''}>${esc(item.label)}${item.synthetic_data ? ' · учебные данные' : ''} · ${number(item.row_count)} строк</option>`).join('')}</select></label><button class="btn" data-action="resume-dataset" ${disabled(!ui.resumeDatasetId)}>Перейти к вопросу ${icon('arrow')}</button></div>`;
}
function syntheticBadge(value) {return value ? '<span class="sample-data-badge">Учебные или синтетические данные</span>' : '';}
function startPanel() {
 return `${savedDatasetChoice()}<div class="import-start"><label class="upload-tile">${icon('upload')}<strong>Загрузить таблицу</strong><span>CSV · TSV · Excel · HTML</span><span>До 10 МБ</span><input id="table-file" type="file" accept=".csv,.tsv,.xlsx,.xls,.html,.htm" aria-label="Загрузить таблицу" ${disabled()}></label><div class="sample-tile"><span class="sample-label">УЧЕБНЫЙ ПРИМЕР</span><h3>Сначала попробовать</h3><p>Таблица школ с вымышленными данными.</p><button class="btn" data-action="example-excel" ${disabled()}>Открыть пример Excel ${icon('arrow')}</button><div class="sample-links"><a href="/examples/import/schools.xlsx" download>Скачать Excel</a><a href="/examples/import/budget.html" download>Скачать HTML</a></div></div></div><details class="scope-help"><summary>Требования к таблице и поддерживаемые расчёты</summary><p>Одна строка — один объект наблюдения; столбцы — его признаки и показатели. В Excel можно выбрать лист, в HTML — таблицу. Строку с названиями столбцов вы укажете перед загрузкой.</p><p>Доступны сумма, среднее и медиана, при необходимости — по одной категории. Данные анализируются без фильтров и весов.</p><p>Из Python или R экспортируйте таблицу в CSV или Excel. Код при импорте не выполняется. Для повторной проверки готовой справки можно скачать Python и исходные данные.</p></details>`;
}
function dataPage() {
 return `${viewHeading('01 / ИСТОЧНИКИ','Данные','Ваши файлы и описание показателей.')}<div class="data-page">${datasetPanel()}${dataset() && !ui.importing ? `<div class="dataset-continue"><button class="btn primary" data-view="analysis" ${disabled()}>Перейти к вопросу ${icon('arrow')}</button></div>` : ''}<a class="archive-link" href="/housing/">Конкурсные формы D006 + D008 · домохозяйства 2024 ${icon('arrow')}</a></div>`;
}
function analysisPage() {
 const selected = dataset();
 const title = viewHeading('02 / ВОПРОС И ПЛАН','Исследование','Вы задаёте вопрос. ИИ предлагает план. Вы подтверждаете расчёт.');
 if (ui.importing) return title + emptyView('data','Завершите подключение таблицы',ui.inspection ? 'Выбор листа и описание столбцов сохранены.' : 'Выберите новый файл или вернитесь к подключённому набору.','Продолжить загрузку','data');
 if (!selected) return title + emptyView('data','Сначала выберите данные','Загрузите файл или откройте сохранённую таблицу.','Выбрать таблицу','data');
 return `${title}<div class="active-dataset"><div><small>ТАБЛИЦА ДЛЯ РАСЧЁТА</small><strong>${esc(selected.label)}</strong><span>${number(selected.row_count)} строк${selected.period ? ` · ${esc(selected.period)}` : ''}${selected.synthetic_data ? ' · учебные данные' : ''}</span></div><button class="text-button" data-view="data" ${disabled()}>Изменить ${icon('arrow')}</button></div>${connectionPanel()}${questionPanel()}`;
}
function reportsPage() {
 const reports = list(state.reports);
 const report = currentReport();
 if (report && !ui.showReportList) return `${viewHeading('03 / СОХРАНЁННЫЙ РЕЗУЛЬТАТ','Справка','Результат, который можно проверить и передать коллеге.',`<button class="btn" data-action="all-reports" ${disabled()}>${icon('history')}Все справки</button>`)}${reportPanel(report)}<details class="panel completed-plan"><summary>Посмотреть подтверждённый план</summary>${planCard(report.plan,report)}</details>`;
 const title = viewHeading('03 / РЕЗУЛЬТАТЫ','Справки','Сохранённые расчёты и файлы для выгрузки.');
 if (!reports.length) return title + emptyView('file','Первая справка ещё впереди','Она появится здесь после подтверждения и выполнения плана.','Начать исследование','analysis');
 return `${title}<div class="report-list">${reports.map(report => `<button class="report-list-item" data-report="${esc(report.id)}" ${disabled()}><span class="report-list-icon">${icon('file')}</span><span class="report-list-copy"><strong>${esc(report.question || 'Сохранённый расчёт')}</strong><small>${esc(date(report.created_at))}</small></span>${icon('arrow')}</button>`).join('')}</div>`;
}
function pageContent() {
 return {home:homePage,data:dataPage,analysis:analysisPage,reports:reportsPage,model:settingsPanel}[ui.view]();
}
function rawPreview(table) {
 const rows = list(table?.preview);
 if (!rows.length) return '<p class="preview-note">Предпросмотр пуст. Выберите другую таблицу.</p>';
 return `<div class="table-scroll table-preview"><table class="preview-table"><caption>Начало таблицы. Подсвечена строка, выбранная для названий столбцов.</caption><tbody>${rows.map((row,index) => `<tr class="${index + 1 === Number(ui.headerRow) ? 'header-candidate' : ''}"><td class="row-index">${index + 1}</td>${list(row).map(cell => `<td>${esc(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}
function importProgress() {const step = ui.preview ? 3 : ui.inspection ? 2 : 1;return `<div class="import-progress" aria-label="Этап подключения">${['Файл','Таблица','Описание'].map((text,index) => `${index ? '<i aria-hidden="true"></i>' : ''}<span class="${step === index + 1 ? 'current' : ''}" ${step === index + 1 ? 'aria-current="step"' : ''}><b>${index + 1}</b>${text}</span>`).join('')}</div>`;}
function tableSelection() {
 const selected = list(ui.inspection.tables).find(item => item.id === ui.tableId);
 return `<form id="table-preview-form"><div class="import-meta"><strong>${icon('file')} ${esc(ui.inspection.filename)}</strong><small>${esc(String(ui.inspection.format || '').toUpperCase())} · таблиц: ${number(ui.inspection.tables?.length)}</small></div><div class="form-row"><label class="field-label" for="import-table">Лист или таблица<select id="import-table" ${disabled()}>${list(ui.inspection.tables).map(table => `<option value="${esc(table.id)}" ${table.id === ui.tableId ? 'selected' : ''}>${esc(table.label)} · около ${number(table.row_count)} строк</option>`).join('')}</select></label><label class="field-label" for="header-row">Строка заголовков<input id="header-row" type="number" min="1" step="1" value="${Number(ui.headerRow) || 1}" required ${disabled()}></label></div>${rawPreview(selected)}${warnings([...list(ui.inspection.warnings),...list(selected?.warnings)])}<div class="import-actions"><button type="button" class="text-button" data-action="reset-import" ${disabled()}>Выбрать другой файл</button><button type="submit" class="btn primary" ${disabled(!selected)}>Далее: описать столбцы ${icon('arrow')}</button></div></form>`;
}
function columnCard(column,index) {
 const profile = list(ui.preview.columns).find(item => item.id === column.id) || {};
 return `<div class="column-card ${column.role === 'ignore' ? 'ignored' : ''}"><div class="column-title"><div><strong><code>${esc(column.id)}</code>${esc(profile.source_name)}</strong><small>Примеры: ${list(profile.sample).length ? list(profile.sample).map(esc).join(' · ') : 'пустые значения'}</small></div><span class="column-stats">Пропусков: ${number(profile.missing_count)}<br>Разных значений: ${number(profile.unique_count)}</span></div><div class="column-fields"><label class="field-label" for="column-label-${index}">Понятное название<input id="column-label-${index}" data-column="${index}" data-field="label" value="${esc(column.label)}" maxlength="120" required ${disabled()}></label><label class="field-label" for="column-role-${index}">Роль столбца<select id="column-role-${index}" data-column="${index}" data-field="role" ${disabled()}>${Object.entries(roles).map(([role,label]) => `<option value="${role}" ${column.role === role ? 'selected' : ''}>${label}</option>`).join('')}</select></label><label class="field-label" for="column-unit-${index}">Единица измерения<input id="column-unit-${index}" data-column="${index}" data-field="unit" value="${esc(column.unit)}" placeholder="тенге, чел., м²" maxlength="60" ${disabled(column.role !== 'number')}></label><label class="field-label" for="column-decimal-${index}">Дробная часть<select id="column-decimal-${index}" data-column="${index}" data-field="decimal_separator" ${disabled(column.role !== 'number')}><option value="." ${column.decimal_separator === '.' ? 'selected' : ''}>Точка · 12.5</option><option value="," ${column.decimal_separator === ',' ? 'selected' : ''}>Запятая · 12,5</option></select></label></div><details class="column-missing"><summary>Коды отсутствующих ответов</summary><label class="field-label" for="column-missing-${index}">Значения через точку с запятой<input id="column-missing-${index}" data-column="${index}" data-field="missing_values" value="${esc(list(column.missing_values).join('; '))}" placeholder="Например: 89; 99 — только если так указано в документации" maxlength="1700" ${disabled(column.role === 'ignore' || column.role === 'identifier')}></label><p class="field-hint">Указывайте коды по документации к этому столбцу. Совпадение текстовое: «89» и «89.0» — разные записи. Регистр и пробелы по краям не учитываются.</p></details></div>`;
}
function passportPanel() {
 const form = ui.passport;
 return `<form id="passport-form"><div class="import-meta"><strong>${esc(ui.inspection.filename)} / ${esc(ui.preview.table_label || '')}</strong><small>${number(ui.preview.row_count)} строк данных · ${number(form.columns.length)} столбцов</small></div><p class="field-hint">Проверьте предложенные роли. Например, код района — идентификатор или категория, даже если он состоит из цифр. Описание определяет, какие расчёты сможет предложить ИИ.</p><div class="passport-context"><label class="field-label wide" for="dataset-label">Название набора<input id="dataset-label" value="${esc(form.label)}" maxlength="120" required ${disabled()}></label><label class="field-label" for="observation-unit">Что означает одна строка?<small>Например: школа, организация, домохозяйство</small><input id="observation-unit" value="${esc(form.observation_unit)}" placeholder="Например, школа" maxlength="120" required ${disabled()}></label><label class="field-label" for="dataset-period">Период данных<small>Укажите, если вся таблица относится к одному периоду</small><input id="dataset-period" value="${esc(form.period)}" placeholder="Например, 2024 или январь 2025" maxlength="80" ${disabled()}></label></div><label class="confirm-label"><input id="dataset-synthetic" type="checkbox" ${form.declared_synthetic ? 'checked' : ''} ${disabled(ui.inspection.synthetic_data)}>Это синтетические или учебные данные</label><div class="passport-heading"><h3>Описание столбцов</h3><span>Роли — предложения программы</span></div><div class="column-list">${form.columns.map(columnCard).join('')}</div>${warnings(ui.preview.warnings)}<details class="column-details"><summary>Посмотреть строки данных</summary><div class="table-scroll table-preview"><table class="preview-table"><thead><tr>${form.columns.map(column => `<th scope="col">${esc(column.label)}</th>`).join('')}</tr></thead><tbody>${list(ui.preview.preview).map(row => `<tr>${list(row).map(cell => `<td>${esc(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table></div></details><label class="confirm-label"><input id="confirm-passport" type="checkbox" required ${ui.confirmed ? 'checked' : ''} ${disabled()}>Я проверил(а) смысл столбцов, единицы, коды пропусков и что означает одна строка.</label><div class="import-actions"><button class="text-button" type="button" data-action="back-table" ${disabled()}>Вернуться к выбору таблицы</button><button class="btn primary" id="register-dataset" type="submit" ${disabled(!ui.confirmed || !form.columns.some(column => column.role === 'number'))}>Сохранить описание и перейти к вопросу ${icon('arrow')}</button></div>${!form.columns.some(column => column.role === 'number') ? '<p class="field-hint">Для расчёта нужен хотя бы один числовой показатель.</p>' : ''}</form>`;
}
function datasetPanel() {
 const selected = dataset();
 if (ui.importing || !selected) return `<section class="panel import-panel" id="import-panel">${heading('01','Подключите свои данные','Выберите таблицу и подтвердите смысл её столбцов.')}${importProgress()}${ui.preview ? passportPanel() : ui.inspection ? tableSelection() : startPanel()}${selected ? `<div class="dataset-actions"><button class="text-button" data-action="cancel-import" ${disabled()}>Вернуться к подключённому набору</button></div>` : ''}</section>`;
 return `<section class="panel dataset-panel">${heading('01','Данные исследования','Описание сохранено. Теперь можно задавать вопросы к этой таблице.')}<div class="dataset-heading-row"><label class="field-label" for="dataset-select">Подключённый набор</label><button class="text-button" data-action="new-import" ${disabled()}>${icon('upload')}Загрузить другой файл</button></div><select id="dataset-select" ${disabled()}>${list(state.datasets).map(item => `<option value="${esc(item.id)}" ${item.id === selected.id ? 'selected' : ''}>${esc(item.label)}</option>`).join('')}</select>${syntheticBadge(selected.synthetic_data)}<div class="dataset-metadata"><span>${icon('data')}${number(selected.row_count)} строк</span><span>Одна строка: ${esc(selected.observation_unit)}</span>${selected.period ? `<span>${esc(selected.period)}</span>` : ''}</div><details class="column-details"><summary>Описание данных и доступные показатели</summary><dl>${list(selected.columns).filter(column => column.role !== 'ignore').map(column => `<div><dt>${esc(column.label)}</dt><dd>${esc(roles[column.role] || column.role)}${column.unit ? ` · ${esc(column.unit)}` : ''}${column.missing_count ? ` · пропусков: ${number(column.missing_count)}` : ''}${column.invalid_count ? ` · нечисловых: ${number(column.invalid_count)}` : ''}${column.missing_values?.length ? ` · коды пропусков: ${column.missing_values.map(esc).join('; ')}` : ''}</dd></div>`).join('')}</dl><p class="field-hint">Для нового описания загрузите файл снова. Сохранённые расчёты останутся связаны с исходным набором.</p></details></section>`;
}
function examples() {
 const selected = dataset(), numeric = list(selected?.columns).filter(column => column.role === 'number'), category = list(selected?.columns).find(column => column.role === 'category');
 if (!numeric.length) return [];
 return [`Найди среднее значение показателя «${numeric[0].label}» по всей таблице.`,category ? `Сравни медиану «${numeric[0].label}» по категориям «${category.label}».` : `Найди медиану показателя «${numeric[0].label}» по всей таблице.`];
}
function planCard(plan=ui.plan,report=ui.report) {
 if (!plan) return '';
 const ready = plan.status === 'ready', completed = Boolean(report && report.plan?.id === plan.id);
 return `<div id="table-plan" class="plan-card ${ready ? '' : 'plan-paused'}" tabindex="-1"><div class="plan-title"><span class="label-pill">${icon(ready ? 'check' : 'info')}${ready ? (completed ? 'Расчёт выполнен и сохранён' : 'План готов к проверке') : plan.status === 'clarify' ? 'Нужно уточнить вопрос' : 'Этот запрос пока не поддерживается'}</span><small>${esc(plan.model || state.provider?.model || '')}</small></div><p class="plan-reason">${esc(plan.reason || (ready ? 'Проверьте параметры перед расчётом.' : 'Переформулируйте вопрос, используя доступные показатели.'))}</p>${ready ? `<dl class="plan-fields"><div><dt>Показатели</dt><dd>${list(plan.metric_details).length ? list(plan.metric_details).map(item => `${esc(item.label)}${item.unit ? ` (${esc(item.unit)})` : ''}`).join(' · ') : list(plan.metrics).map(id => esc(metricInfo(id).label)).join(' · ')}</dd></div><div><dt>Расчёт</dt><dd>${esc(aggregation(plan.aggregation))}</dd></div><div><dt>Группировка</dt><dd>${plan.group_by ? `По столбцу «${esc(plan.group_label || list(dataset()?.columns).find(column => column.id === plan.group_by)?.label || plan.group_by)}»` : 'Вся выбранная таблица'}</dd></div><div><dt>Одна строка</dt><dd>${esc(plan.observation_unit || dataset()?.observation_unit || '')}</dd></div><div><dt>Период</dt><dd>${esc(plan.period || dataset()?.period || 'Не указан в описании')}</dd></div></dl><div class="plan-approval"><p>Все строки набора, без фильтров и весов. Пропуски и нечисловые значения будут показаны вместе с расчётом. Проверьте, что такой охват соответствует вопросу.</p>${completed ? '<p class="saved-plan-note">Расчёт сохранён в истории. Для новой справки измените вопрос и составьте новый план.</p>' : `<button class="btn primary" data-action="execute" ${disabled()}>Подтвердить и рассчитать ${icon('arrow')}</button>`}</div>` : '<p class="field-hint">Данные не изменены, расчёт не выполнялся. Уточните вопрос выше и составьте новый план.</p>'}</div>`;
}
function questionPanel() {
 if (ui.importing || !dataset()) return '';
 const questions = examples();
 return `<section class="panel question-panel">${heading('02','Какой вопрос нужно решить?','ИИ использует подтверждённое описание таблицы, чтобы предложить план.')}<form id="question-form"><label class="field-label" for="table-question">Задача для ассистента</label><textarea id="table-question" rows="3" maxlength="2000" placeholder="Например: сравни среднее число учащихся по типам школ." required ${disabled()}>${esc(ui.question)}</textarea><div class="examples">${questions.map((question,index) => `<button type="button" data-example="${index}" ${disabled()}>${esc(question)}</button>`).join('')}</div><div class="question-footer"><p>Доступны среднее, медиана и сумма. Модели передаются вопрос и описание столбцов; значения строк не передаются.</p><button class="btn primary" id="prepare-plan" type="submit" ${disabled(!ui.question.trim())}>${icon('spark')}Составить план</button></div></form>${planCard()}</section>`;
}
function chart(report,metricId) {
 const rows = list(report.table).slice(0,24), info = metricInfo(metricId);
 if (!rows.length) return '<p class="empty-result">Для построения графика нет групп.</p>';
 const values = rows.map(row => row.values?.[metricId]).filter(value => value !== null && value !== undefined && Number.isFinite(Number(value))).map(Number);
 if (!values.length) return '<p class="empty-result">Для этого показателя нет пригодных числовых значений.</p>';
 let minimum = Math.min(0,...values), maximum = Math.max(0,...values);
 if (minimum === maximum) maximum = minimum + 1;
 const left = 205,width = 420,zero = left + (0-minimum)/(maximum-minimum)*width,height = rows.length*58 + 22;
 const mobileChart = `<div class="mobile-chart" role="img" aria-label="${esc(info.label)} по группам. Значения доступны в таблице ниже.">${rows.map(row => {const raw = row.values?.[metricId],valid = raw !== null && raw !== undefined && Number.isFinite(Number(raw)),value = valid ? Number(raw) : 0,zeroPercent = (0-minimum)/(maximum-minimum)*100,valuePercent = (value-minimum)/(maximum-minimum)*100;return `<div class="mobile-chart-row"><span class="mobile-chart-label">${esc(row.group ?? 'Вся таблица')}</span><span class="mobile-chart-track"><i class="mobile-chart-zero" style="left:${zeroPercent}%"></i>${valid ? `<i class="mobile-chart-bar ${value < 0 ? 'negative' : ''}" style="left:${Math.min(zeroPercent,valuePercent)}%;width:${Math.abs(valuePercent-zeroPercent)}%"></i>` : ''}</span><strong>${esc(number(raw))}</strong></div>`;}).join('')}</div>`;
 return `${mobileChart}<svg class="report-chart" viewBox="0 0 800 ${height}" role="img" aria-label="${esc(info.label)} по группам. Числа также доступны в таблице ниже."><line class="chart-zero" x1="${zero}" x2="${zero}" y1="6" y2="${height-5}"/>${rows.map((row,index) => {const raw = row.values?.[metricId],valid = raw !== null && raw !== undefined && Number.isFinite(Number(raw)),value = valid ? Number(raw) : 0,coordinate = left + (value-minimum)/(maximum-minimum)*width,y = index*58+15,label = String(row.group ?? 'Вся таблица'),short = label.length > 27 ? label.slice(0,25)+'…' : label;return `<g><title>${esc(label)}: ${number(raw)}${info.unit ? ` ${esc(info.unit)}` : ''}</title><text x="0" y="${y+21}" fill="#657b52" font-size="14">${esc(short)}</text>${valid ? `<rect class="${value < 0 ? 'chart-negative' : 'chart-bar'}" x="${Math.min(zero,coordinate)}" y="${y}" width="${Math.abs(coordinate-zero)}" height="30" rx="4"/>` : ''}<text x="650" y="${y+21}" fill="#496437" font-size="16">${esc(number(raw))}</text></g>`;}).join('')}</svg>${report.table.length > rows.length ? `<p class="preview-note">На графике первые ${rows.length} группы. Все ${number(report.table.length)} групп — в таблице и выгрузке.</p>` : ''}`;
}
function reportPanel(report=ui.report) {
 if (!report) return '';
 const metrics = list(report.plan?.metrics), metricId = metrics.includes(ui.chartMetric) ? ui.chartMetric : metrics[0], bad = Object.entries(report.quality?.invalid_numeric || {});
 return `<section class="panel report-panel" id="table-report" tabindex="-1">${heading('03','Проверяемая справка','Рассчитано по вашему набору и сохранено в истории.')}${syntheticBadge(report.dataset?.synthetic_data)}<div class="report-intro"><h3>${esc(report.question)}</h3><p>${esc(report.dataset?.label || dataset()?.label || '')} · ${esc(date(report.created_at))}</p></div><div class="report-summary">${list(report.summary).map(text => `<p>${esc(text)}</p>`).join('')}</div><div class="chart-block"><div class="chart-heading"><div><h3>${esc(aggregation(report.plan?.aggregation))} по выбранным данным</h3><p>${esc(metricInfo(metricId).unit || 'В единицах выбранного показателя')}</p></div>${metrics.length > 1 ? `<label class="chart-select-label" for="chart-metric">Показатель на графике<select id="chart-metric">${metrics.map(id => `<option value="${esc(id)}" ${id === metricId ? 'selected' : ''}>${esc(metricInfo(id).label)}</option>`).join('')}</select></label>` : ''}</div>${metricId ? chart(report,metricId) : ''}</div><div class="table-scroll"><table class="report-table"><caption>Под значением указано число использованных строк. Пропуски и нечисловые значения не заменяются нулями.</caption><thead><tr><th scope="col">${esc(report.plan?.group_label || 'Группа')}</th><th scope="col">Всего строк</th>${metrics.map(id => `<th scope="col">${esc(metricInfo(id).label)}<small>${esc(metricInfo(id).unit || '')}</small></th>`).join('')}</tr></thead><tbody>${list(report.table).map(row => `<tr><th scope="row">${esc(row.group ?? 'Вся таблица')}</th><td>${number(row.rows)}</td>${metrics.map(id => `<td>${number(row.values?.[id])}<small>Использовано: ${number(row.valid_counts?.[id])}${row.missing_counts?.[id] ? ` · пропущено: ${number(row.missing_counts[id])}` : ''}</small></td>`).join('')}</tr>`).join('')}</tbody></table></div><div class="report-support"><details open><summary>Качество данных и методика</summary><dl class="quality-grid"><div><dt>Всего строк в наборе</dt><dd>${number(report.quality?.total_rows)}</dd></div><div><dt>Без значения группировки</dt><dd>${number(report.quality?.grouping_missing_rows)}</dd></div></dl>${bad.length ? `<p>${bad.map(([id,value]) => `${esc(metricInfo(id).label)}: нечисловых значений — ${number(value)}`).join('<br>')}</p>` : ''}<p>${esc(report.methodology || '')}</p><dl class="formula-list">${metrics.map(id => `<div><dt>${esc(metricInfo(id).label)}</dt><dd>${esc(formulaDescription(report.plan?.aggregation))}</dd></div>`).join('')}</dl></details><details><summary>Источники и воспроизводимость</summary>${list(report.sources).map(source => `<div class="source-item"><strong>${esc(source.filename || '')}</strong><span>${esc(source.table || '')}</span>${source.sha256 ? `<code>SHA-256 ${esc(source.sha256)}</code>` : ''}</div>`).join('')}<p>Модель планирования: ${esc(report.plan?.model || '')}<br>Расчёт: ${esc(report.id)}</p><p>Для повторной проверки скачайте код Python и «CSV для Python». Этот CSV сохраняет исходные текстовые значения, включая запятые в дробях; запускайте код именно с ним. Порядок запуска указан в начале файла Python.</p></details><details open><summary>Ограничения вывода</summary><ul class="limitations">${list(report.limitations).map(text => `<li>${humanText(text)}</li>`).join('')}</ul></details></div><div class="export-bar"><span>${icon('download')}Сохранить результат</span><div>${[['html','Справка HTML'],['csv','Таблица CSV'],['python','Код Python'],['source','CSV для Python'],['json','Данные JSON']].map(([format,label]) => `<a class="btn small-btn" href="/api/tables/export?id=${encodeURIComponent(report.id)}&format=${format}" download>${label}</a>`).join('')}</div></div></section>`;
}
function modelSetupGuide(config) {
 if (config.provider !== 'ollama') return '<div class="setup-note"><strong>Подключить свой API</strong><p>Укажите адрес совместимого сервера, имя доступной модели и ключ, если он требуется. Сохраните настройки и нажмите «Проверить подключение».</p></div>';
 return '<div class="setup-note"><strong>Первый запуск локального ИИ</strong><ol><li>Если модели ещё нет, один раз запустите <code>setup_local_ai.cmd</code> из папки проекта. Понадобится интернет и загрузка около 1,5 ГБ.</li><li>Дождитесь завершения установки. Если Ollama уже установлена, запустите её и укажите имя своей локальной модели ниже.</li><li>Сохраните изменённые настройки и нажмите «Проверить подключение».</li></ol><p>После установки модель работает на этом компьютере. Для своего API выберите другой способ подключения выше.</p></div>';
}
function settingsPanel() {
 const config = ui.config,status = modelStatus();
 return `<section class="panel settings-page" aria-labelledby="settings-title"><div class="modal-head"><div><span class="eyebrow">ПОДКЛЮЧЕНИЕ</span><h2 id="settings-title">Модель для планирования</h2><p>Расчёты выполняет приложение. Модель помогает понять вопрос и выбрать операции.</p></div></div><div class="modal-body"><div class="connection-status" role="status"><span class="status-dot ${status.kind}"></span><div><strong>${status.text}</strong><p>${esc(ui.probe?.message || 'Проверьте, доступна ли выбранная модель.')}</p></div></div><form id="provider-form"><label class="field-label" for="provider-kind">Способ подключения</label><select id="provider-kind" ${disabled()}><option value="ollama" ${config.provider === 'ollama' ? 'selected' : ''}>Ollama · на этом компьютере</option><option value="openai_compatible" ${config.provider === 'openai_compatible' ? 'selected' : ''}>Совместимый API · свой сервер</option></select><details class="model-help"><summary>Как подключить модель?</summary>${modelSetupGuide(config)}</details><label class="field-label" for="provider-url">Адрес сервера</label><input id="provider-url" type="url" value="${esc(config.base_url)}" required ${disabled()}><label class="field-label" for="provider-model">Имя модели</label><input id="provider-model" value="${esc(config.model)}" maxlength="200" required ${disabled()}>${config.provider === 'openai_compatible' ? `<label class="field-label" for="provider-key">Ключ API</label><input id="provider-key" type="password" autocomplete="new-password" maxlength="4096" placeholder="${config.api_key_set ? 'Ключ уже задан в памяти сервера' : 'Если сервер требует авторизацию'}" ${disabled()}><p class="field-hint">Ключ не сохраняется в браузере. После отправки поле очищается.</p><label class="checkbox-label"><input id="allow-external" type="checkbox" ${config.allow_external ? 'checked' : ''} ${disabled()}>Разрешаю передавать вопрос и подтверждённое описание столбцов внешнему серверу</label>` : ''}<p class="privacy-note">Значения строк и результаты расчёта не отправляются модели. Внешний сервер получает вопрос и описание показателей; не включайте в них сведения, которые не хотите передавать.</p><div class="button-row settings-actions"><button class="btn primary" type="submit" ${disabled()}>Сохранить настройки</button><button class="btn" type="button" data-action="probe" ${disabled(ui.configDirty)}>Проверить подключение</button></div></form>${ui.probe?.models?.length ? `<details class="models-list"><summary>Модели на сервере</summary><p>${ui.probe.models.map(esc).join('<br>')}</p></details>` : ''}${ui.error ? `<div class="error-banner" role="alert">${esc(ui.error)}</div>` : ''}${ui.busy ? `<p class="busy-inline" role="status"><span class="spinner"></span>${esc(ui.busy)}</p>` : ''}</div></section>`;
}
function render() {
 if (!state) {root.innerHTML = `<div class="boot"><img src="/assets/residual-mark.png" alt="Residual" width="64" height="64"><p>${esc(ui.error || 'Открываем рабочее пространство…')}</p>${ui.error ? '<button class="btn" data-action="retry">Повторить подключение</button>' : '<span class="spinner"></span>'}</div>`;return;}
 root.innerHTML = `${navigation()}<div class="app table-app studio-app view-${ui.view}">${header()}<div class="workspace"><main class="content" id="main-content" tabindex="-1">${ui.error && ui.view !== 'model' ? `<div class="error-banner" role="alert">${icon('info')}<span>${esc(ui.error)}</span></div>` : ''}${ui.notice ? `<div class="notice-banner" role="status">${icon('check')}<span>${esc(ui.notice)}</span></div>` : ''}${ui.busy && ui.view !== 'model' ? `<div class="busy-banner" role="status"><span class="spinner"></span><div><strong>${esc(ui.busy)}</strong><p>${ui.busy.includes('план') ? 'Модель подготавливает план. Расчёт начнётся после вашего подтверждения.' : 'Дождитесь завершения операции.'}</p></div></div>` : ''}${pageContent()}<footer class="footer"><span>residual © 2026</span><span>Расчёты на этом компьютере</span></footer></main></div></div>`;

}

async function inspectFile(file) {
 if (!file) return;
 if (file.size > 10*1024*1024) {ui.error = 'Размер файла не должен превышать 10 МБ. Сохраните нужный лист или таблицу отдельно.';render();return;}
 if (!/\.(csv|tsv|xlsx|xls|html|htm)$/i.test(file.name)) {ui.error = 'Поддерживаются CSV, TSV, Excel (.xlsx, .xls) и HTML с таблицами. Из Python или R сначала экспортируйте данные в CSV.';render();return;}
 await action('Читаем файл и ищем таблицы…',async () => {
  const content = await new Promise((resolve,reject) => {const reader = new FileReader();reader.onload = () => resolve(String(reader.result).split(',')[1]);reader.onerror = () => reject(new Error('Не удалось прочитать файл.'));reader.readAsDataURL(file);});
  const result = await api('inspect',{filename:file.name,content_base64:content});
  if (!result.tables?.length) throw new Error('В файле не найдено таблиц. Сохраните данные с одной строкой заголовков.');
  setView('data');ui.inspection = result;ui.tableId = result.tables[0].id;ui.headerRow = 1;ui.preview = null;ui.passport = null;ui.confirmed = false;ui.importing = true;resetResult();
 });
}
async function preparePassport(event) {
 event.preventDefault();
 if (!ui.inspection) return;
 await action('Проверяем структуру и значения столбцов…',async () => {
  ui.preview = await api('preview',{import_id:ui.inspection.id,table_id:ui.tableId,header_row:Number(ui.headerRow)});
  ui.passport = {label:ui.inspection.filename.replace(/\.[^.]+$/,''),observation_unit:'',period:'',declared_synthetic:Boolean(ui.inspection.synthetic_data),columns:list(ui.preview.columns).map(column => ({id:column.id,label:column.source_name,role:column.inferred_role || 'ignore',unit:'',decimal_separator:column.suggested_decimal_separator === ',' ? ',' : '.',missing_values:[]}))};ui.confirmed = false;
 });
 document.querySelector('#dataset-label')?.focus({preventScroll:true});
}
async function registerDataset(event) {
 event.preventDefault();
 if (!ui.confirmed || !ui.passport) return;
 if (!ui.passport.observation_unit.trim()) {ui.error = 'Укажите, что означает одна строка: например, школа или организация.';render();return;}
 await action('Сохраняем данные и подтверждённое описание…',async () => {
  const result = await api('register',{import_id:ui.inspection.id,table_id:ui.tableId,header_row:Number(ui.headerRow),...ui.passport});
  await refresh();ui.datasetId = (result.dataset || result).id;ui.importing = false;ui.inspection = null;ui.preview = null;ui.passport = null;resetResult(true);setView('analysis');ui.notice = 'Набор подключён. Описание сохранено — можно задать вопрос или выбрать пример.';
 });
 document.querySelector('#table-question')?.focus({preventScroll:true});
}
async function preparePlan(event) {
 event.preventDefault();
 if (!dataset() || !ui.question.trim()) return;
 await action('ИИ составляет план расчёта…',async () => {
  ui.plan = null;ui.report = null;ui.plan = await api('plan',{dataset_id:ui.datasetId,question:ui.question.trim()});if (ui.plan.status === 'ready' && ui.plan.model) ui.probe = {ok:true,message:'Модель подготовила план расчёта.',selected_model:ui.plan.model};
 });
 document.querySelector('#table-plan')?.focus({preventScroll:true});
}
async function execute() {
 if (ui.plan?.status !== 'ready') return;
 await action('Рассчитываем подтверждённые показатели…',async () => {ui.report = await api('execute',{plan_id:ui.plan.id});ui.savedReport = null;ui.showReportList = false;ui.chartMetric = '';await refresh();setView('reports');ui.notice = 'Справка сохранена. Проверьте результат, качество данных и ограничения.';});
 document.querySelector('#table-report')?.scrollIntoView({behavior:'smooth',block:'start'});
}
async function configure(event) {
 event.preventDefault();
 const secret = document.querySelector('#provider-key'),body = {provider:ui.config.provider,base_url:document.querySelector('#provider-url').value.trim(),model:document.querySelector('#provider-model').value.trim(),allow_external:ui.config.provider === 'openai_compatible' && Boolean(document.querySelector('#allow-external')?.checked)};
 if (secret?.value) body.api_key = secret.value;
 if (secret) secret.value = '';
 await action('Сохраняем настройки модели…',async () => {await api('configure',body,'research');ui.configDirty = false;ui.probe = null;await refresh();ui.notice = 'Настройки сохранены. Проверьте подключение к модели.';});
}
function dirtyConfig() {ui.configDirty = true;const button = document.querySelector('[data-action="probe"]');if (button) button.disabled = true;}

root.addEventListener('click',async event => {
 const button = event.target.closest('button');
 if (!button || button.disabled || ui.busy) return;
 if (button.dataset.view) {goTo(button.dataset.view);return;}
 if (button.dataset.example !== undefined) {ui.question = examples()[Number(button.dataset.example)] || '';resetResult();render();document.querySelector('#table-question')?.focus();return;}
 if (button.dataset.report) {await action('Открываем сохранённую справку…',async () => {ui.savedReport = await api('report?id='+encodeURIComponent(button.dataset.report));ui.showReportList = false;ui.chartMetric = '';setView('reports');});return;}
 switch(button.dataset.action) {
  case 'begin':goTo(dataset() && !ui.importing ? 'analysis' : 'data');break;
  case 'all-reports':ui.showReportList = true;render();break;
  case 'resume-dataset':if (list(state.datasets).some(item => item.id === ui.resumeDatasetId)) {ui.datasetId = ui.resumeDatasetId;ui.resumeDatasetId = '';setView('analysis');ui.importing = false;ui.inspection = null;ui.preview = null;ui.passport = null;resetResult(true);render();document.querySelector('#table-question')?.focus({preventScroll:true});}break;
  case 'new':resetResult(true);ui.importing = !dataset();setView(dataset() ? 'analysis' : 'data');render();document.querySelector('#table-question')?.focus();break;
  case 'new-import':ui.importing = true;setView('data');ui.inspection = null;ui.preview = null;ui.passport = null;render();break;
  case 'cancel-import':ui.importing = false;ui.error = '';render();break;
  case 'reset-import':ui.inspection = null;ui.preview = null;ui.passport = null;ui.error = '';render();break;
  case 'back-table':ui.preview = null;ui.passport = null;ui.confirmed = false;ui.error = '';render();break;
  case 'example-excel':{
   let file;setView('data');
   await action('Открываем учебный файл Excel…',async () => {const response = await fetch('/examples/import/schools.xlsx');if (!response.ok) throw new Error('Учебный файл недоступен. Можно загрузить собственную таблицу.');file = new File([await response.blob()],'schools.xlsx');});
   if (file) await inspectFile(file);break;
  }
  case 'execute':await execute();break;
  case 'settings':goTo('model');break;
  case 'close':goTo('home');break;
  case 'probe':await action('Проверяем подключение к модели…',async () => {try {ui.probe = await api('probe',{},'research');} catch(error) {ui.probe = {ok:false,message:error.message};throw error;}});break;
  case 'retry':await action('Подключаемся…',refresh);break;
 }
});
root.addEventListener('input',event => {
 const target = event.target,{id,value} = target;
 if (id === 'table-question') {ui.question = value;const button = document.querySelector('#prepare-plan');if (button) button.disabled = Boolean(ui.busy || !value.trim());if (ui.plan || ui.report) {ui.plan = null;ui.report = null;document.querySelector('#table-plan')?.remove();document.querySelector('#table-report')?.remove();}ui.notice = '';}
 if (id === 'header-row') {ui.headerRow = value;document.querySelectorAll('.preview-table tbody tr').forEach((row,index) => row.classList.toggle('header-candidate',index+1 === Number(value)));}
 if (ui.passport && ['dataset-label','observation-unit','dataset-period'].includes(id)) ui.passport[{'dataset-label':'label','observation-unit':'observation_unit','dataset-period':'period'}[id]] = value;
 if (target.dataset.column !== undefined && ui.passport) {ui.passport.columns[Number(target.dataset.column)][target.dataset.field] = target.dataset.field === 'missing_values' ? value.split(';').map(code => code.trim()).filter(Boolean) : value;ui.confirmed = false;const checkbox = document.querySelector('#confirm-passport');if (checkbox) checkbox.checked = false;const save = document.querySelector('#register-dataset');if (save) save.disabled = true;}
 if (id === 'provider-url') {ui.config.base_url = value;dirtyConfig();}
 if (id === 'provider-model') {ui.config.model = value;dirtyConfig();}
 if (id === 'provider-key') dirtyConfig();
});
root.addEventListener('change',async event => {
 const target = event.target,{id,value} = target;
 if (id === 'table-file') await inspectFile(target.files?.[0]);
 if (id === 'import-table') {ui.tableId = value;ui.headerRow = 1;render();}
 if (id === 'dataset-select') {ui.datasetId = value;resetResult(true);render();}
 if (id === 'saved-dataset-select') {ui.resumeDatasetId = value;const button = document.querySelector('[data-action="resume-dataset"]');if (button) button.disabled = Boolean(ui.busy || !value);}
 if (id === 'chart-metric') {ui.chartMetric = value;render();}
 if (id === 'dataset-synthetic' && ui.passport) {ui.passport.declared_synthetic = target.checked;ui.confirmed = false;render();}
 if (id === 'confirm-passport') {ui.confirmed = target.checked;const button = document.querySelector('#register-dataset');if (button) button.disabled = !ui.confirmed || !ui.passport.columns.some(column => column.role === 'number');}
 if (target.dataset.field === 'role' && ui.passport) {ui.passport.columns[Number(target.dataset.column)].role = value;ui.confirmed = false;render();document.querySelector('#'+id)?.focus({preventScroll:true});}
 if (id === 'provider-kind') {ui.config.provider = value;ui.config.allow_external = false;ui.config.base_url = value === 'ollama' ? defaults.base_url : '';ui.config.model = value === 'ollama' ? defaults.model : '';ui.configDirty = true;render();}
 if (id === 'allow-external') {ui.config.allow_external = target.checked;dirtyConfig();}
});
root.addEventListener('submit',event => {if (event.target.id === 'table-preview-form') preparePassport(event);if (event.target.id === 'passport-form') registerDataset(event);if (event.target.id === 'question-form') preparePlan(event);if (event.target.id === 'provider-form') configure(event);});
window.addEventListener('hashchange',() => goTo(initialView(),false));
refresh().then(render).catch(error => {ui.error = error.message;render();});
