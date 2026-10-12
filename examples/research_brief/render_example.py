"""Render the fixed D006/D008 housing calculation as a standalone statistical brief.

Use --html-only to retain the existing SVG charts while updating the report text.
Rebuilding the SVG charts additionally requires matplotlib.
"""
import argparse
from html import escape
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
data = json.loads((HERE / 'result.json').read_text(encoding='utf-8'))
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--html-only', action='store_true')
args = parser.parse_args()

def number(value, digits=2):
    rounded = Decimal(str(value)).quantize(Decimal('1').scaleb(-digits), rounding=ROUND_HALF_UP)
    text = f'{rounded:,.{digits}f}'.rstrip('0').rstrip('.') if digits else f'{rounded:,}'
    return text.replace(',', ' ').replace('.', ',')

def chart(field, filename, title, maximum):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    values = [row[field] for row in data['primary']]
    labels = [row['household_size_group'] + ' чел.' for row in data['primary']]
    with plt.rc_context({'font.family': 'DejaVu Sans', 'font.size': 14,
                         'axes.spines.top': False, 'axes.spines.right': False,
                         'axes.spines.left': False, 'axes.spines.bottom': False}):
        fig, ax = plt.subplots(figsize=(4.7, 3.15))
        fig.patch.set_facecolor('#f6f7f2')
        ax.set_facecolor('#f6f7f2')
        ax.barh(labels, values, color='#73865a', height=.48)
        ax.invert_yaxis()
        ax.set_xlim(0, maximum)
        ax.set_xticks([0, maximum/2, maximum])
        ax.set_xticklabels([number(value) for value in [0, maximum/2, maximum]])
        ax.set_xlabel('м²', fontsize=13)
        ax.tick_params(axis='both', length=0, labelsize=13)
        ax.set_axisbelow(True)
        ax.xaxis.grid(True, color='#dce0d6', linewidth=.7)
        for i, value in enumerate(values):
            ax.text(value + maximum*.025, i, number(value), va='center', fontsize=14)
        fig.subplots_adjust(left=.24, right=.94, top=.98, bottom=.22)
        fig.savefig(HERE / filename, format='svg', metadata={'Title': title,
                    'Description': 'Невзвешенные медианы в синтетическом конкурсном массиве 2024 года.'})
        plt.close(fig)

if not args.html_only:
    chart('median_total_area_m2', 'total-area.svg', 'Медиана общей площади жилья', 90)
    chart('median_area_per_person_m2', 'area-per-person.svg', 'Медиана площади на человека', 45)
rows = ''.join(f"<tr><th scope='row'>{escape(row['household_size_group'])} чел.</th>"
               f"<td>{number(row['households'], 0)}</td>"
               f"<td>{number(row['median_total_area_m2'])}</td>"
               f"<td>{number(row['median_area_per_person_m2'])}</td></tr>" for row in data['primary'])
comparison_rows = ''.join(f"<tr><th scope='row'>{escape(old['household_size_group'])}</th>"
               f"<td>{number(old['median_area_per_person_m2'])}</td>"
               f"<td>{number(new['median_area_per_person_m2'])}</td>"
               f"<td>{number(old['households']-new['households'],0)}</td></tr>"
               for old, new in zip(data['primary'],data['sensitivity']))
limitations = ''.join('<li>'+escape(item)+'</li>' for item in data['limitations'])
template = '''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Residual — жилищные условия домохозяйств, 2024</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#222521;color:#252a22;font:16px/1.65 system-ui,-apple-system,"Segoe UI",sans-serif}a{color:inherit}main{max-width:1060px;margin:28px auto;padding:38px 52px;background:#eeefe9;border-radius:28px}header{display:flex;align-items:center;justify-content:space-between;gap:20px;border-bottom:1px solid #c5ccbe;padding-bottom:22px}header strong{font-size:26px;letter-spacing:-1px}.tag{font-size:12px;color:#525d49;border:1px solid #adb89e;border-radius:30px;padding:7px 13px}nav{display:flex;flex-wrap:wrap;gap:22px;margin:26px 0;font-size:14px}nav a{text-decoration:none;border-bottom:1px solid #879876}h1{font-size:clamp(30px,4vw,46px);line-height:1.12;letter-spacing:-1.5px;font-weight:550;max-width:750px;margin:34px 0 20px}h2{font-size:27px;font-weight:550;line-height:1.3;letter-spacing:-.7px;margin:0 0 18px}h3{font-size:18px;font-weight:550;margin:0 0 9px}.intro{font-size:19px;max-width:800px}.status{background:#dee6d3;border-radius:16px;padding:18px 22px;margin:26px 0;font-size:14px}.status strong{display:block;margin-bottom:4px}section{margin-top:42px;padding-top:30px;border-top:1px solid #c5ccbe}.question{font-size:21px;line-height:1.5;border-left:3px solid #73865a;padding-left:20px;margin:24px 0}.caption{font-size:13px;color:#58624f}.answer{font-size:18px;line-height:1.65}.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:15px;margin:20px 0}th,td{padding:13px 10px;border-bottom:1px solid #cbd2c4;text-align:right;font-variant-numeric:tabular-nums}th:first-child{text-align:left}thead th{font-size:13px;color:#55604b;font-weight:500}tbody th{font-weight:500}.charts{display:grid;grid-template-columns:1fr 1fr;gap:22px;margin:30px 0}.chart{background:#f6f7f2;border-radius:18px;padding:20px 12px 0;min-width:0}.chart h3{padding:0 14px;min-height:50px}.chart img{width:100%;height:auto;display:block}.downloads{display:flex;flex-wrap:wrap;gap:12px;margin:24px 0}.downloads a{display:inline-block;text-decoration:none;border:1px solid #a1ad94;border-radius:22px;padding:9px 16px;font-size:14px}.downloads a:first-child{background:#293421;color:#f0f3eb;border-color:#293421}summary{cursor:pointer;font-weight:500}details{margin:20px 0;padding:18px 0;border-top:1px solid #d0d7c9}li{margin:10px 0}ol{padding-left:24px}.step{font-weight:550}.contract{display:grid;grid-template-columns:1fr 1fr;gap:22px}.contract>div{background:#f6f7f2;padding:22px;border-radius:18px}.contract p{margin:8px 0;font-size:15px}.small{font-size:14px}.source{font-size:13px;overflow-wrap:anywhere}footer{margin-top:35px;padding-top:22px;border-top:1px solid #c5ccbe;color:#58624f;font-size:13px}@media(max-width:650px){main{padding:24px 20px;margin:10px;border-radius:20px}header{align-items:flex-start}.tag{max-width:165px}.charts,.contract{grid-template-columns:1fr}h1{letter-spacing:-1px}.intro,.answer{font-size:17px}.question{font-size:18px}th,td{padding:11px 5px;font-size:13px}thead th{font-size:11px}.chart h3{min-height:0}nav{gap:13px;font-size:13px}section{margin-top:30px;padding-top:24px}}@media print{body{background:white}main{margin:0;padding:10px;max-width:none}nav,.downloads{display:none}section,.chart{break-inside:avoid}details{display:block}}
</style></head><body><main>
<header><strong>residual</strong><span class="tag">Синтетические данные · 2024</span></header>
<nav aria-label="Разделы"><a href="#result">Результаты</a><a href="#coverage">Охват и качество</a><a href="#method">Метод и ограничения</a><a href="#reproduce">Воспроизведение</a></nav>
<h1>Жилищные условия<br>домохозяйств разного размера.</h1>
<p class="intro">Описательная справка по формам D006 и D008 за 2024 год: общая площадь жилья и площадь на человека в группах из 1–2, 3–4 и 5+ человек.</p>
<div class="status"><strong>Отдельный воспроизводимый пример</strong>Фиксированный расчёт по синтетическому массиву. Основной вариант сохраняет записи с диагностическим противоречием площади; дополнительный вариант показывает результат после их исключения. Показатели невзвешенные и не являются официальными оценками населения.</div>
<section id="result"><h2>Результаты</h2>
<p class="question">Как различаются медиана общей площади жилья и медиана площади на человека между группами домохозяйств?</p>
<p class="answer">__ANSWER__</p>
<p>Площадь на человека рассчитывается делением общей площади на размер домохозяйства. Это математическое соотношение влияет на сравнение; различия не устанавливают причину или жилищную нуждаемость.</p>
<div class="table-wrap"><table><caption class="caption">Единица анализа — домохозяйство; равный вес каждой записи</caption><thead><tr><th scope="col">Размер</th><th scope="col">Домохозяйств</th><th scope="col">Медиана площади, м²</th><th scope="col">Медиана м² на человека</th></tr></thead><tbody>__ROWS__</tbody></table></div>
<div class="charts"><div class="chart"><h3>Общая площадь жилья</h3><img src="total-area.svg" alt="__AREA_ALT__"></div><div class="chart"><h3>Площадь на человека</h3><img src="area-per-person.svg" alt="__PERSON_ALT__"></div></div>
<div class="downloads"><a href="table.csv" download>Таблица CSV</a><a href="result.json" download>Паспорт расчёта JSON</a></div>
</section>
<section id="coverage"><h2>Охват и качество данных</h2>
<p>В основной расчёт вошли __HOUSING__ домохозяйства с данными о жилье. В карточках состава есть ещё __ROSTER_ONLY__ домохозяйств без соответствующей строки жилья; они не включены в сравнение.</p>
<p>В __FLAGGED__ записях жилая площадь больше общей. Основной вариант сохраняет эти записи: противоречие само по себе не определяет, какое поле ошибочно. Дополнительный расчёт исключает их целиком без изменения исходных значений.</p>
<h3>Чувствительность к исключению спорных записей</h3>
<p>__SENSITIVITY__</p><div class="table-wrap"><table><caption class="caption">Медиана площади на человека до и после исключения</caption><thead><tr><th scope="col">Группа</th><th scope="col">Основной вариант, м²/чел.</th><th scope="col">Без спорных, м²/чел.</th><th scope="col">Исключено</th></tr></thead><tbody>__COMPARISON__</tbody></table></div>
<a href="sensitivity.csv" download>Скачать дополнительный расчёт CSV</a>
</section>
<section id="method"><h2>Метод и ограничения</h2>
<p class="source">Жильё: <code>d006/2024/rio_2024.csv</code>, общая площадь <code>OB_PL</code>, жилая площадь <code>J_PL</code>. Состав: <code>d008/2024/kontr_k.csv</code>, ключи <code>NOMER + NOMP</code>, заявленный размер <code>KOL_CHL</code>. Определения полей приведены в структурах D006 и D008 исходного архива.</p>
<ol><li>Проверяются уникальность ключа домохозяйства в D006 и ключа человека в D008, положительные конечные площади и согласованность <code>KOL_CHL</code> с числом членов.</li><li>Разные члены домохозяйства подсчитываются по D008. Затем состав агрегируется до одной строки на домохозяйство и соединяется с D006 по <code>NOMER</code> внутри 2024 года.</li><li>Для каждого домохозяйства вычисляется <code>OB_PL / число членов</code>. Домохозяйства разделяются на группы 1–2, 3–4 и 5+ человек.</li><li>В каждой группе рассчитываются медианы общей площади и индивидуальных значений площади на человека. Медиана отношений не заменяется отношением медиан.</li></ol>
<ul>__LIMITATIONS__</ul>
</section>
<section id="reproduce"><h2>Самостоятельное воспроизведение</h2>
<p>Скачайте скрипт и укажите каталог распакованного исходного архива. Нужен Python 3.10+ со стандартной библиотекой. Модель и сетевые запросы для этого расчёта не требуются.</p>
<pre class="source"><code>python reproduce.py --data-dir PATH_TO_ARCHIVE --output-dir OUTPUT_DIR</code></pre>
<p>Скрипт создаёт <code>table.csv</code>, <code>sensitivity.csv</code> и <code>result.json</code>. В JSON сохранены определения показателей, ограничения и контрольные суммы исходных файлов. Для проверки чисел сравните разделы <code>coverage</code>, <code>primary</code> и <code>sensitivity</code>.</p>
<div class="downloads"><a href="reproduce.py" download>Скачать Python-скрипт</a><a href="result.json" download>Скачать паспорт расчёта</a></div>
</section><footer>Residual · синтетические данные 2024 года · описательная справка. Результаты не являются официальной оценкой населения Казахстана.</footer>
</main></body></html>'''
first, last = data['primary'][0], data['primary'][-1]
answer = (f"В группе {last['household_size_group']} человек медиана общей площади составляет "
          f"{number(last['median_total_area_m2'])} м², в группе {first['household_size_group']} — "
          f"{number(first['median_total_area_m2'])} м². Медиана площади на человека — "
          f"{number(last['median_area_per_person_m2'])} и {number(first['median_area_per_person_m2'])} м² соответственно.")
area_alt = '; '.join(f"{row['household_size_group']} человек: {number(row['median_total_area_m2'])} м²" for row in data['primary'])
person_alt = '; '.join(f"{row['household_size_group']} человек: {number(row['median_area_per_person_m2'])} м² на человека" for row in data['primary'])
sensitivity = 'Медианы общей площади по группам: ' + '; '.join(
    f"{old['household_size_group']} человек — {number(old['median_total_area_m2'])} м² в основном варианте, "
    f"{number(new['median_total_area_m2'])} м² после исключения"
    for old, new in zip(data['primary'], data['sensitivity'])) + '.'
replacements = {'__ROWS__': rows, '__COMPARISON__': comparison_rows, '__LIMITATIONS__': limitations,
    '__ANSWER__': escape(answer), '__AREA_ALT__': escape(area_alt), '__PERSON_ALT__': escape(person_alt),
    '__HOUSING__': number(data['coverage']['housing_households'], 0),
    '__ROSTER_ONLY__': number(data['coverage']['roster_only_households'], 0),
    '__FLAGGED__': number(data['coverage']['area_order_flagged_households'], 0),
    '__SENSITIVITY__': escape(sensitivity)}
for marker, value in replacements.items():
    template = template.replace(marker, value)
(HERE / 'index.html').write_text(template, encoding='utf-8')
print('Rendered: index.html' if args.html_only else 'Rendered: index.html, total-area.svg, area-per-person.svg')
