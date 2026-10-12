'use strict';
const root = document.querySelector('#root');
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const number = value => value === null || value === undefined || value === '' || !Number.isFinite(Number(value)) ? '—' : Number(value).toLocaleString('ru-RU', {maximumFractionDigits:2});
const date = value => {const d = new Date(value); return !value || Number.isNaN(d.getTime()) ? '' : d.toLocaleString('ru-RU', {day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'});};
const paths = {arrow:'M4 12h16 M14 6l6 6-6 6',plus:'M12 5v14 M5 12h14',check:'M5 12l4 4L19 6',upload:'M12 16V3 M7 8l5-5 5 5 M4 17v4h16v-4',download:'M12 3v13 M7 11l5 5 5-5 M4 17v4h16v-4',data:'M4 4h16v16H4z M4 9h16 M9 4v16',history:'M3 11a9 9 0 1 1 2 7 M3 4v7h7 M12 7v5l3 2',settings:'M4 7h16 M4 17h16 M8 4v6 M16 14v6',close:'M6 6l12 12 M18 6L6 18',info:'M12 10v7 M12 7h.01 M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',spark:'M12 3l2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z',file:'M14 3H5v18h14V8z M14 3v6h5 M8 13h8 M8 17h5',chevron:'M9 5l7 7-7 7'};
const icon = name => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[name] || paths.info}"/></svg>`;
const defaultProvider = {provider:'ollama',base_url:'http://127.0.0.1:11434',model:'qwen3:1.7b',allow_external:false};
let state = null;
const ui = {datasetId:'',question:'',plan:null,report:null,chartMetric:'',modal:'',busy:'',error:'',notice:'',probe:null,configDirty:false,config:{...defaultProvider},upload:{label:'Обследование домохозяйств',housing:null,roster:null}};
const metricFallback = {total_area:{label:'Общая площадь',unit:'м²'},area_per_person:{label:'Площадь на человека',unit:'м²/чел.'},rooms:{label:'Число комнат',unit:'комнат'},persons_per_room:{label:'Человек на комнату',unit:'чел./комнату'},living_share:{label:'Доля жилой площади',unit:''}};
const qualityLabels = {housing_rows:'Записей D006',roster_rows:'Строк D008',roster_households:'Домохозяйств D008',eligible_households:'Пригодны для расчёта',excluded_households:'Исключены из расчёта',invalid_numeric_excluded:'Непригодные числовые значения',roster_mismatch_excluded:'Несоответствие состава домохозяйства',area_order:'Жилая площадь больше общей',unused_roster_households:'D008 вне охвата D006'};
const dataset = () => state?.datasets?.find(item => item.id === ui.datasetId);
const metric = id => ui.report?.formulas?.[id] || state?.catalog?.metrics?.[id] || metricFallback[id] || {label:id,unit:''};
const metricName = id => metric(id).label || id;
const metricUnit = id => metric(id).unit || '';
const aggregationName = id => ({median:'Медиана',mean:'Среднее арифметическое'}[id] || id);
const groupingName = id => ({household_size:'По размеру домохозяйства',all:'Весь выбранный пакет'}[id] || id);
const list = values => (Array.isArray(values) ? values : values ? [values] : []);

async function api(path, body) {
 const response = await fetch('/api/research/' + path, {method:body === undefined ? 'GET' : 'POST',headers:body === undefined ? {} : {'Content-Type':'application/json'},body:body === undefined ? undefined : JSON.stringify(body)});
 let result;
 try {result = await response.json();} catch {throw new Error('Сервер вернул непонятный ответ. Проверьте, что локальное приложение запущено.');}
 if (!response.ok || result.error) throw new Error(result.error || result.message || `Не удалось выполнить действие (${response.status}).`);
 return result;
}
async function refresh() {
 state = await api('state');
 if (!state.datasets?.some(item => item.id === ui.datasetId)) ui.datasetId = state.datasets?.[0]?.id || '';
 ui.config = {...defaultProvider,...state.provider};
}
function announce(message) {document.querySelector('#announcer').textContent = message;}
async function action(name, work) {
 if (ui.busy) return;
 ui.busy = name; ui.error = ''; ui.notice = ''; render(); announce(name);
 try {await work();} catch (error) {ui.error = error.message || 'Не удалось выполнить действие.';}
 finally {ui.busy = '';render();announce(ui.error || ui.notice || 'Готово.');}
}
function disabled(condition = false) {return ui.busy || condition ? 'disabled' : '';}
function sectionHead(index, title, detail, badge = '') {return `<div class="section-head"><span class="step-number">${index}</span><div><h2>${title}</h2>${detail ? `<p>${detail}</p>` : ''}</div>${badge}</div>`;}
function modelStatus() {
 if (ui.probe?.ok) return {text:'Подключение проверено',class:'ready'};
 if (ui.probe) return {text:'Модель недоступна',class:'unavailable'};
 return {text:'Подключение не проверено',class:'unknown'};
}
function header() {
 const status = modelStatus();
 return `<header class="header"><a class="brand" href="/" aria-label="Residual, исследование данных"><img src="/assets/residual-mark.png" width="44" height="44" alt=""><span><strong>residual</strong><small>Ясность в данных</small></span></a><nav aria-label="Рабочие пространства"><span class="nav-current" aria-current="page">Исследование</span><a href="/quality/">Контроль качества ${icon('arrow')}</a></nav><button class="model-trigger" data-action="settings" ${disabled()}><span class="status-dot ${status.class}"></span><span>${esc(state?.provider?.model || defaultProvider.model)}<small>${status.text}</small></span>${icon('settings')}</button></header>`;
}
function sidebar() {
 const reports = list(state.reports);
 const plans = list(state.plans).filter(plan => plan.status === 'ready' && !reports.some(report => report.plan_id === plan.id)).slice(0,5);
 return `<aside class="sidebar"><div class="workspace-label">РАБОЧЕЕ ПРОСТРАНСТВО <span>01</span></div><div class="intro"><h1>От вопроса<br>к <em>расчёту.</em></h1><p>ИИ предлагает план.<br>Вы проверяете. Данные отвечают.</p></div><button class="btn new-question" data-action="new" ${disabled()}>${icon('plus')}Новое исследование</button><div class="history-heading"><h2>${icon('history')}История расчётов</h2><span>${number(reports.length)}</span></div><div class="history-list">${reports.length ? reports.map(report => `<button class="history-item ${ui.report?.id === report.id ? 'active' : ''}" data-report="${esc(report.id)}" ${disabled()}><span>${esc(report.question || 'Сохранённый расчёт')}</span><small>${esc(date(report.created_at))}</small></button>`).join('') : '<p class="muted small empty-history">Здесь появятся выполненные расчёты. Их можно открыть снова и выгрузить.</p>'}</div>${plans.length ? `<details class="pending-plans"><summary>Ожидают подтверждения <span>${number(plans.length)}</span></summary>${plans.map(plan => `<button class="history-item" data-plan="${esc(plan.id)}" ${disabled()}><span>${esc(plan.question)}</span><small>План подготовлен · расчёт не выполнен</small></button>`).join('')}</details>` : ''}<div class="scope-note"><span class="scope-mark">D006 / D008</span><p>Жилищные условия и состав домохозяйства · 2024</p><span>Локальный MVP</span></div></aside>`;
}
function datasetPanel() {
 const selected = dataset();
 return `<section class="panel dataset-panel" aria-labelledby="dataset-title">${sectionHead('01','Данные исследования','Две формы одного периода, связанные по NOMER.')}${selected ? `<div class="dataset-selected"><label for="dataset-select" class="field-label" id="dataset-title">Выбранный пакет</label><select id="dataset-select" ${disabled()}>${state.datasets.map(item => `<option value="${esc(item.id)}" ${item.id === ui.datasetId ? 'selected' : ''}>${esc(item.label)}</option>`).join('')}</select><div class="dataset-metadata"><span>${icon('data')}${number(selected.quality?.housing_rows)} домохозяйств D006</span><span>${esc(selected.year)}</span><span>${selected.is_demo ? 'Вымышленные данные' : selected.synthetic_data === true ? 'Синтетические данные' : 'Загруженный пакет'}</span></div>${selected.is_demo ? '<p class="dataset-caution">Учебные вымышленные данные. Результаты показывают работу инструмента и не описывают население.</p>' : ''}<div class="dataset-actions"><button class="text-button" data-action="upload" ${disabled()}>${icon('upload')}Загрузить другой пакет</button><button class="text-button" data-action="demo" ${disabled()}>Открыть учебные данные</button></div></div>` : `<div class="start-data"><div class="data-symbol" aria-hidden="true">${icon('data')}<span>D006 + D008</span></div><div><h3 id="dataset-title">Начните с данных</h3><p>Загрузите CSV или пройдите сценарий на примере.</p><div class="button-row"><button class="btn primary" data-action="demo" ${disabled()}>Открыть учебный пример ${icon('arrow')}</button><button class="btn" data-action="upload" ${disabled()}>${icon('upload')}Загрузить CSV</button></div><small>Учебные вымышленные данные · 160 домохозяйств</small></div></div>`}</section>`;
}
function questionPanel() {
 const examples = list(state.catalog?.examples).length ? state.catalog.examples : ['Сравни медианную площадь на человека по размеру домохозяйства.','Каково среднее число комнат в домохозяйстве?','Сравни число человек на комнату по размеру домохозяйства.'];
 return `<section class="panel question-panel ${dataset() ? '' : 'awaiting'}">${sectionHead('02','Вопрос к данным','Сначала план, затем расчёт после вашего подтверждения.',`<span class="label-pill">${icon('spark')}ИИ-планировщик</span>`)}<form id="question-form"><label for="research-question" class="field-label">Что вы хотите узнать?</label><textarea id="research-question" rows="3" maxlength="2000" placeholder="Например: как меняется площадь на человека с размером домохозяйства?" ${disabled(!dataset())}>${esc(ui.question)}</textarea><div class="examples" aria-label="Примеры поддерживаемых вопросов">${examples.map((example,index) => `<button type="button" data-example="${index}" ${disabled(!dataset())}>${esc(typeof example === 'string' ? example : example.question || example.text)}</button>`).join('')}</div><div class="question-footer"><p>${dataset() ? 'Доступны среднее и медиана, весь пакет или группы по размеру домохозяйства.' : 'Выберите данные на первом шаге.'}</p><button class="btn primary" type="submit" id="prepare-plan" ${disabled(!dataset() || !ui.question.trim())}>Подготовить план ${icon('arrow')}</button></div></form>${ui.plan ? planPanel() : ''}</section>`;
}
function planPanel() {
 const p = ui.plan, ready = p.status === 'ready';
 return `<div class="plan-card ${ready ? '' : 'plan-paused'}" id="research-plan" tabindex="-1"><div class="plan-title"><span class="label-pill">${icon(ready ? 'check' : 'info')}${ready ? 'План готов к проверке' : p.status === 'clarify' ? 'Нужно уточнение' : 'За пределами доступных расчётов'}</span><small>${esc(p.model || state.provider?.model || '')}</small></div><p class="plan-reason">${esc(p.reason || (ready ? 'Проверьте выбранные показатели и способ расчёта.' : 'Уточните вопрос в рамках доступных показателей.'))}</p>${ready ? `<dl class="plan-fields"><div><dt>Показатели</dt><dd>${list(p.metrics).map(id => esc(metricName(id))).join(' · ')}</dd></div><div><dt>Расчёт</dt><dd>${esc(aggregationName(p.aggregation))}</dd></div><div><dt>Группировка</dt><dd>${esc(groupingName(p.grouping))}</dd></div><div><dt>Период</dt><dd>${esc(p.year)}</dd></div></dl><div class="plan-approval"><p>Будет рассчитан только этот план на выбранном пакете. Исходные значения сохраняются.</p><button class="btn primary" data-action="execute" ${disabled()}>${icon('check')}Подтвердить и рассчитать</button></div>` : '<p class="muted small">Расчёт не выполнялся. Измените вопрос выше или выберите пример.</p>'}</div>`;
}
function chartPanel(report) {
 const metrics = list(report.plan?.metrics).length ? report.plan.metrics : Object.keys(report.table?.[0]?.values || {});
 if (!metrics.includes(ui.chartMetric)) ui.chartMetric = metrics[0] || '';
 const rows = list(report.chart).filter(row => row.metric === ui.chartMetric && row.value !== null && Number.isFinite(Number(row.value)));
 if (!rows.length) return '<div class="empty-result">Нет значений для диаграммы.</div>';
 const maximum = Math.max(...rows.map(row => Math.abs(Number(row.value))),1);
 const height = rows.length * 48 + 26;
 return `<div class="chart-heading"><div><h3>${esc(metricName(ui.chartMetric))}</h3><p>${esc(aggregationName(report.plan?.aggregation))}${metricUnit(ui.chartMetric) ? ` · ${esc(metricUnit(ui.chartMetric))}` : ''}</p></div>${metrics.length > 1 ? `<label class="chart-select-label">Показатель<select id="chart-metric">${metrics.map(id => `<option value="${esc(id)}" ${id === ui.chartMetric ? 'selected' : ''}>${esc(metricName(id))}</option>`).join('')}</select></label>` : ''}</div><svg class="report-chart" viewBox="0 0 700 ${height}" role="img" aria-label="${esc(metricName(ui.chartMetric))} по группам. Точные значения приведены в таблице ниже."><title>${esc(metricName(ui.chartMetric))}</title>${rows.map((row,index) => `<g transform="translate(0 ${index*48+10})"><text class="chart-group" x="0" y="23">${esc(row.group)}</text><rect class="chart-track" x="152" y="5" width="420" height="28" rx="6"/><rect class="chart-bar" x="152" y="5" width="${Math.abs(Number(row.value))/maximum*420}" height="28" rx="6"/><text class="chart-value" x="588" y="24">${number(row.value)}</text></g>`).join('')}</svg>`;
}
function dataTable(rows,metrics,caption) {
 return `<div class="table-scroll" tabindex="0" role="region" aria-label="${esc(caption)}"><table><caption>${esc(caption)}</caption><thead><tr><th scope="col">Группа</th><th scope="col">Домохозяйств</th>${metrics.map(id => `<th scope="col">${esc(metricName(id))}${metricUnit(id) ? `<small>${esc(metricUnit(id))}</small>` : ''}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr><th scope="row">${esc(row.group)}</th><td>${number(row.households)}</td>${metrics.map(id => `<td>${number(row.values?.[id])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}
function reportPanel() {
 const r = ui.report;
 if (!r) return '';
 const metrics = list(r.plan?.metrics).length ? r.plan.metrics : Object.keys(r.table?.[0]?.values || {});
 const quality = r.quality || {}, sensitivity = r.sensitivity || {};
 return `<section class="panel report-panel" id="research-report" tabindex="-1">${sectionHead('03','Результат исследования','Числа рассчитаны по данным. Модель использовалась только для составления плана.',`<span class="label-pill">${icon('check')}Расчёт сохранён</span>`)}<div class="report-intro"><h3>${esc(r.question || r.plan?.question)}</h3><p>${esc(r.dataset?.label || '')} · ${esc(r.plan?.year || r.dataset?.year || '')} · ${esc(aggregationName(r.plan?.aggregation))}</p></div>${r.dataset?.is_demo ? '<div class="inline-notice">Это учебный пример на вымышленных данных. Он не является статистической оценкой для Казахстана.</div>' : ''}<div class="report-summary">${list(r.summary).map(text => `<p>${esc(text)}</p>`).join('')}</div><div class="chart-block">${chartPanel(r)}</div>${dataTable(list(r.table),metrics,'Основной расчёт')}<div class="report-support"><details open><summary>Качество и охват данных</summary><dl class="quality-grid">${Object.entries(quality).filter(([key,value]) => key in qualityLabels && typeof value === 'number').map(([key,value]) => `<div><dt>${esc(qualityLabels[key])}</dt><dd>${number(value)}</dd></div>`).join('')}</dl><p class="muted small">Домохозяйства D008, отсутствующие в D006, не входят в расчёт жилищных показателей. Расхождение охвата само по себе не подтверждает ошибку.</p></details>${sensitivity.table ? `<details><summary>Чувствительность к спорным площадям <span>${number(sensitivity.excluded_area_order)} исключено</span></summary><p>${esc(sensitivity.description || '')}</p>${dataTable(list(sensitivity.table),metrics,'Расчёт без записей, где жилая площадь больше общей')}</details>` : ''}<details><summary>Формулы и происхождение данных</summary><dl class="formula-list">${metrics.map(id => `<div><dt>${esc(metricName(id))}</dt><dd><code>${esc(r.formulas?.[id]?.formula || metric(id).formula || '')}</code></dd></div>`).join('')}</dl>${list(r.sources).map(source => `<div class="source-item"><strong>${esc(source.form || source.title || 'Источник')}</strong><span>${esc(source.file || '')}</span>${source.sha256 ? `<code>SHA-256 ${esc(source.sha256)}</code>` : ''}</div>`).join('')}<p class="muted small">Пакет: ${esc(r.dataset?.id || r.plan?.dataset_id || '')}<br>План: ${esc(r.plan?.id || '')}<br>Модель планирования: ${esc(r.plan?.model || '')}</p></details><details open><summary>Как интерпретировать результат</summary><ul class="limitations">${list(r.limitations).map(text => `<li>${esc(text)}</li>`).join('')}</ul></details></div><div class="export-bar"><span>${icon('download')}Сохранить расчёт</span><div>${[['csv','Таблица CSV'],['html','Отчёт HTML'],['json','Данные JSON'],['python','Код Python']].map(([format,label]) => `<a class="btn small-btn" href="/api/research/export?id=${encodeURIComponent(r.id)}&format=${format}" download>${label}</a>`).join('')}</div></div></section>`;
}
function settingsModal() {
 const p = ui.config, status = modelStatus();
 return `<div class="overlay"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="settings-title"><div class="modal-head"><div><span class="eyebrow">ПОДКЛЮЧЕНИЕ</span><h2 id="settings-title">Модель для планирования</h2><p>Модель переводит ваш вопрос в проверяемый план расчёта.</p></div><button class="icon-button" data-action="close" aria-label="Закрыть настройки" ${disabled()}>${icon('close')}</button></div><div class="modal-body"><div class="connection-status ${status.class}" role="status"><span class="status-dot ${status.class}"></span><div><strong>${status.text}</strong><p>${esc(ui.probe?.message || 'Нажмите «Проверить подключение», чтобы узнать фактическое состояние модели.')}</p></div></div><form id="provider-form"><label class="field-label" for="provider-kind">Способ подключения</label><select id="provider-kind" ${disabled()}><option value="ollama" ${p.provider === 'ollama' ? 'selected' : ''}>Ollama · на этом компьютере</option><option value="openai_compatible" ${p.provider === 'openai_compatible' ? 'selected' : ''}>Совместимый API · свой сервер</option></select>${p.provider === 'ollama' ? '<div class="setup-note"><strong>Первый запуск локальной модели</strong><p>Один раз откройте <code>setup_local_ai.cmd</code> в папке проекта двойным щелчком. Дождитесь установки и загрузки модели, затем проверьте подключение здесь.</p></div>' : ''}<label class="field-label" for="provider-url">Адрес сервера</label><input id="provider-url" type="url" value="${esc(p.base_url)}" required ${disabled()}><label class="field-label" for="provider-model">Имя модели</label><input id="provider-model" value="${esc(p.model)}" maxlength="200" required ${disabled()}>${p.provider === 'openai_compatible' ? `<label class="field-label" for="provider-key">Ключ API</label><input id="provider-key" type="password" autocomplete="new-password" placeholder="${p.api_key_set ? 'Ключ уже задан в памяти сервера' : 'Если сервер требует авторизацию'}" maxlength="4096" ${disabled()}><p class="field-hint">Ключ не сохраняется в браузере. Поле очищается при отправке настроек.</p><label class="checkbox-label"><input id="allow-external" type="checkbox" ${p.allow_external ? 'checked' : ''} ${disabled()}>Разрешаю передавать свой вопрос и описание операций внешнему серверу</label>` : ''}<p class="privacy-note">${esc(state.provider?.data_policy || 'Модель получает вопрос и описание доступных операций. Строки таблиц и результаты расчёта не отправляются. Сам вопрос может содержать чувствительные сведения.')}</p><div class="button-row settings-actions"><button class="btn primary" type="submit" ${disabled()}>Сохранить настройки</button><button class="btn" type="button" data-action="probe" ${disabled(ui.configDirty)}>Проверить подключение</button></div></form>${ui.probe?.models?.length ? `<details class="models-list"><summary>Модели на сервере · ${number(ui.probe.models.length)}</summary><p>${ui.probe.models.map(name => esc(name)).join('<br>')}</p></details>` : ''}${ui.error ? `<div class="error-banner" role="alert">${icon('info')}<span>${esc(ui.error)}</span></div>` : ''}${ui.busy ? `<p class="busy-inline" role="status"><span class="spinner"></span>${esc(ui.busy)}</p>` : ''}</div></section></div>`;
}
function uploadModal() {
 const form = ui.upload;
 return `<div class="overlay"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="upload-title"><div class="modal-head"><div><span class="eyebrow">НОВЫЙ ПАКЕТ</span><h2 id="upload-title">Данные исследования</h2><p>D006 и D008 за 2024 год. Исходные файлы сохраняются отдельным пакетом.</p></div><button class="icon-button" data-action="close" aria-label="Закрыть загрузку" ${disabled()}>${icon('close')}</button></div><form id="upload-form" class="modal-body"><label class="field-label" for="dataset-label">Название пакета</label><input id="dataset-label" value="${esc(form.label)}" maxlength="120" required ${disabled()}>${[['housing','D006 · Жилищные условия','NOMER, OB_PL, J_PL, KOL_K'],['roster','D008 · Состав домохозяйства','NOMER, NOMP, KOL_CHL']].map(([key,title,fields]) => `<label class="file-zone ${form[key] ? 'selected' : ''}">${icon(form[key] ? 'check' : 'upload')}<span><strong>${title}</strong><small>${form[key] ? `${esc(form[key].name)} · ${number(form[key].size/1024)} КБ` : fields}</small></span><input type="file" id="file-${key}" accept=".csv,text/csv" aria-label="${title}" ${disabled()}></label>`).join('')}<p class="field-hint">CSV · период 2024 · до 16 МБ суммарно. Привязка форм — по NOMER внутри одного периода.</p><button class="btn primary full-width" type="submit" ${disabled(!form.housing || !form.roster)}>Загрузить и проверить структуру ${icon('arrow')}</button>${ui.error ? `<div class="error-banner" role="alert">${icon('info')}<span>${esc(ui.error)}</span></div>` : ''}${ui.busy ? `<p class="busy-inline" role="status"><span class="spinner"></span>${esc(ui.busy)}</p>` : ''}</form></section></div>`;
}
function render() {
 if (!state) {root.innerHTML = `<div class="boot"><img src="/assets/residual-mark.png" alt="Residual" width="64" height="64"><p>${esc(ui.error || 'Открываем рабочее пространство…')}</p>${ui.error ? '<button class="btn" data-action="retry">Повторить подключение</button>' : '<span class="spinner"></span>'}</div>`;return;}
 root.innerHTML = `<div class="app">${header()}<div class="workspace">${sidebar()}<main class="content" id="main-content"><div class="content-heading"><span class="eyebrow">ИССЛЕДОВАНИЕ ДАННЫХ</span><span class="local-label">${icon('data')}Расчёты на этом компьютере</span></div>${ui.error && !ui.modal ? `<div class="error-banner" role="alert">${icon('info')}<span>${esc(ui.error)}</span></div>` : ''}${ui.notice ? `<div class="notice-banner" role="status">${icon('check')}<span>${esc(ui.notice)}</span></div>` : ''}${ui.busy && !ui.modal ? `<div class="busy-banner" role="status"><span class="spinner"></span><div><strong>${esc(ui.busy)}</strong><p>${ui.busy.includes('план') ? 'Первый ответ локальной модели может занять несколько минут. Расчёт ещё не выполняется.' : 'Дождитесь завершения операции.'}</p></div></div>` : ''}${datasetPanel()}${questionPanel()}${reportPanel()}<footer class="footer"><span>residual © 2026</span><span>Вопрос → план → подтверждение → воспроизводимый расчёт</span></footer></main></div></div>${ui.modal === 'settings' ? settingsModal() : ui.modal === 'upload' ? uploadModal() : ''}`;
}
function resetResearch(clearQuestion = false) {ui.plan = null; ui.report = null; ui.chartMetric = '';if (clearQuestion) ui.question = '';ui.error = '';ui.notice = '';}
async function demo() {await action('Открываем учебные данные…',async () => {const result = await api('demo',{});const selected = result.dataset || result;await refresh();ui.datasetId = selected.id;resetResearch(true);ui.modal = '';ui.notice = 'Учебный пакет загружен. Выберите пример вопроса или напишите свой.';});}
async function prepare(event) {
 event.preventDefault();if (!dataset() || !ui.question.trim()) return;
 await action('Модель составляет план…',async () => {ui.report = null;ui.plan = null;const result = await api('plan',{dataset_id:ui.datasetId,question:ui.question.trim()});ui.plan = result.plan || result;ui.probe = {ok:true,message:'Модель ответила на запрос и подготовила план.',selected_model:ui.plan.model};await refresh();});
 document.querySelector('#research-plan')?.focus({preventScroll:true});
}
async function execute() {
 if (ui.plan?.status !== 'ready') return;
 await action('Выполняем подтверждённый расчёт…',async () => {const result = await api('execute',{plan_id:ui.plan.id});ui.report = result.report || result;ui.chartMetric = '';await refresh();ui.notice = 'Расчёт сохранён. Проверьте таблицу, качество данных и ограничения.';});
 if (ui.report) document.querySelector('#research-report')?.scrollIntoView({behavior:'smooth',block:'start'});
}
async function configure(event) {
 event.preventDefault();
 const secret = document.querySelector('#provider-key');
 const body = {provider:ui.config.provider,base_url:document.querySelector('#provider-url').value.trim(),model:document.querySelector('#provider-model').value.trim(),allow_external:ui.config.provider === 'openai_compatible' && Boolean(document.querySelector('#allow-external')?.checked)};
 if (secret?.value) body.api_key = secret.value;
 if (secret) secret.value = '';
 await action('Сохраняем настройки модели…',async () => {await api('configure',body);ui.probe = null;ui.configDirty = false;await refresh();ui.notice = 'Настройки сохранены. Проверьте подключение к выбранной модели.';});
}
async function upload(event) {
 event.preventDefault();const form = ui.upload;
 if (!form.housing || !form.roster) return;
 if (form.housing.size+form.roster.size > 16*1024*1024) {ui.error = 'Размер двух CSV не должен превышать 16 МБ.';render();return;}
 await action('Загружаем и проверяем структуру…',async () => {const [housing_csv,roster_csv] = await Promise.all([form.housing.text(),form.roster.text()]);const result = await api('upload',{label:form.label.trim(),housing_csv,roster_csv,year:2024});await refresh();ui.datasetId = (result.dataset || result).id;resetResearch(true);ui.modal = '';ui.notice = 'Пакет загружен. Теперь можно сформулировать вопрос.';});
}
root.addEventListener('click',async event => {
 const button = event.target.closest('button');if (!button || button.disabled) return;
 if (button.dataset.example !== undefined) {const examples = list(state.catalog?.examples).length ? state.catalog.examples : ['Сравни медианную площадь на человека по размеру домохозяйства.','Каково среднее число комнат в домохозяйстве?','Сравни число человек на комнату по размеру домохозяйства.'];const example = examples[Number(button.dataset.example)];ui.question = typeof example === 'string' ? example : example.question || example.text;resetResearch();render();document.querySelector('#research-question')?.focus();return;}
 if (button.dataset.report) {await action('Открываем сохранённый расчёт…',async () => {const result = await api('report?id='+encodeURIComponent(button.dataset.report));ui.report = result.report || result;ui.plan = ui.report.plan;ui.datasetId = ui.plan.dataset_id || ui.report.dataset?.id;ui.question = ui.report.question || ui.plan.question;ui.chartMetric = '';});return;}
 if (button.dataset.plan) {ui.plan = state.plans.find(plan => plan.id === button.dataset.plan);ui.datasetId = ui.plan.dataset_id;ui.question = ui.plan.question;ui.report = null;ui.error = '';render();return;}
 switch (button.dataset.action) {
  case 'settings':ui.modal = 'settings';ui.error = '';ui.config = {...defaultProvider,...state.provider};ui.configDirty = false;render();document.querySelector('#provider-kind')?.focus();break;
  case 'close':ui.modal = '';ui.error = '';render();break;
  case 'upload':ui.modal = 'upload';ui.error = '';render();document.querySelector('#dataset-label')?.focus();break;
  case 'demo':await demo();break;
  case 'execute':await execute();break;
  case 'new':resetResearch(true);render();document.querySelector('#research-question')?.focus();break;
  case 'probe':await action('Проверяем подключение к модели…',async () => {try {ui.probe = await api('probe',{});} catch(error) {ui.probe = {ok:false,message:error.message};throw error;}});break;
  case 'retry':await action('Подключаемся…',async () => {await refresh();});break;
 }
});
root.addEventListener('input',event => {
 const {id,value} = event.target;
 if (id === 'research-question') {ui.question = value;const button = document.querySelector('#prepare-plan');if (button) button.disabled = Boolean(ui.busy || !dataset() || !value.trim());if (ui.plan) {ui.plan = null;ui.report = null;document.querySelector('#research-plan')?.remove();document.querySelector('#research-report')?.remove();}}
 if (id === 'dataset-label') ui.upload.label = value;
 if (id === 'provider-url') ui.config.base_url = value;
 if (id === 'provider-model') ui.config.model = value;
 if (['provider-url','provider-model','provider-key'].includes(id)) {ui.configDirty = true;const probe = document.querySelector('[data-action="probe"]');if(probe) probe.disabled = true;}
});
root.addEventListener('change',event => {
 const {id,value} = event.target;
 if (id === 'dataset-select') {ui.datasetId = value;resetResearch();render();}
 if (id === 'chart-metric') {ui.chartMetric = value;render();}
 if (id === 'provider-kind') {ui.configDirty = true;ui.config.provider = value;ui.config.allow_external = false;if (value === 'ollama') {ui.config.base_url = defaultProvider.base_url;ui.config.model = defaultProvider.model;} else {ui.config.base_url = '';ui.config.model = '';}render();}
 if (id === 'allow-external') {ui.config.allow_external = event.target.checked;ui.configDirty = true;const probe = document.querySelector('[data-action="probe"]');if(probe) probe.disabled = true;}
 if (id === 'file-housing' || id === 'file-roster') {ui.upload[id === 'file-housing' ? 'housing' : 'roster'] = event.target.files[0] || null;ui.error = '';render();}
});
root.addEventListener('submit',event => {if (event.target.id === 'question-form') prepare(event);if (event.target.id === 'provider-form') configure(event);if (event.target.id === 'upload-form') upload(event);});
document.addEventListener('keydown',event => {
 if (event.key === 'Escape' && ui.modal && !ui.busy) {ui.modal = '';ui.error = '';render();}
 if (event.key === 'Tab' && ui.modal) {const focusable = [...document.querySelectorAll('[role="dialog"] button:not([disabled]),[role="dialog"] input:not([disabled]),[role="dialog"] select:not([disabled]),[role="dialog"] summary')];if (!focusable.length) {event.preventDefault();return;}const first = focusable[0],last = focusable[focusable.length-1];if (event.shiftKey && document.activeElement === first) {event.preventDefault();last.focus();} else if (!event.shiftKey && document.activeElement === last) {event.preventDefault();first.focus();}}
});
refresh().then(render).catch(error => {ui.error = error.message;render();});
