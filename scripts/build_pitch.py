"""Сборка презентации docs/pitch/RWB-Mesh-PVZ-pitch.pptx (10 слайдов, 16:9) и PDF.

Запуск: .venv/bin/python scripts/build_pitch.py
Числа — из economics/params.yaml и docs/ECON_REPORT.md; цифры стенда (слайд 7) — со
свежего прогона на чистой БД, см. docs/screenshots/01-dashboard.png.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "pitch" / "RWB-Mesh-PVZ-pitch.pptx"
SHOTS = ROOT / "docs" / "screenshots"

FONT = "DejaVu Sans"
BG, CARD, CARD2, LINE = "0D1220", "151B2C", "1C2438", "28324A"
TXT, SUB, MUTE = "EEF2FA", "9AA7C0", "6E7B96"
BLUE, GREEN, AMBER, RED = "5B8DEF", "3CCF98", "F2B53A", "F2706D"

W, H = 13.333, 7.5
ML, MR = 0.65, 0.65
CW = W - ML - MR
TOTAL = 10

# Фактические цифры стенда (чистая БД + scripts/demo.py + 4 заказа)
STAND = {"points": 9, "orders": 5, "mesh_pct": 80, "logistics": "270 ₽", "tests": 148, "paths": 40}


def rgb(h: str) -> RGBColor:
    return RGBColor.from_string(h)


prs = Presentation()
prs.slide_width, prs.slide_height = Inches(W), Inches(H)
BLANK = prs.slide_layouts[6]


def rect(sl, x, y, w, h, fill, line=None, rounded=True, adj=0.07, lw=1.0):
    shp = sl.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE,
                              Inches(x), Inches(y), Inches(w), Inches(h))
    if rounded:
        shp.adjustments[0] = adj
    shp.fill.solid()
    shp.fill.fore_color.rgb = rgb(fill)
    if line:
        shp.line.color.rgb = rgb(line)
        shp.line.width = Pt(lw)
    else:
        shp.line.fill.background()
    shp.shadow.inherit = False
    return shp


def tb(sl, x, y, w, h, paras, size=14, color=TXT, bold=False, align="l", anchor="t", gap=0, pad=0.15):
    """paras: str | list[str | list[(text, {size,color,bold})]]"""
    box = sl.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = Inches(pad)
    tf.vertical_anchor = {"t": MSO_ANCHOR.TOP, "m": MSO_ANCHOR.MIDDLE, "b": MSO_ANCHOR.BOTTOM}[anchor]
    if isinstance(paras, str):
        paras = [paras]
    for i, para in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = {"l": PP_ALIGN.LEFT, "c": PP_ALIGN.CENTER, "r": PP_ALIGN.RIGHT}[align]
        if gap and i:
            p.space_before = Pt(gap)
        runs = [(para, {})] if isinstance(para, str) else para
        for text, st in runs:
            r = p.add_run()
            r.text = text
            r.font.name = FONT
            r.font.size = Pt(st.get("size", size))
            r.font.bold = st.get("bold", bold)
            r.font.color.rgb = rgb(st.get("color", color))
    return box


def card(sl, x, y, w, h, fill=CARD, line=LINE, accent=None):
    shp = rect(sl, x, y, w, h, fill, line)
    if accent:
        rect(sl, x + 0.0, y + 0.18, 0.07, h - 0.36, accent, rounded=False)
    return shp


def kpi(sl, x, y, w, h, value, label, color, fill=CARD, vsize=44, lsize=13):
    card(sl, x, y, w, h, fill)
    tb(sl, x, y + 0.12, w, h * 0.55, value, size=vsize, color=color, bold=True, align="c", anchor="m")
    tb(sl, x, y + h * 0.58, w, h * 0.4, label, size=lsize, color=SUB, align="c", anchor="t")


def pill(sl, x, y, w, h, text, fill, color=BG, size=13):
    rect(sl, x, y, w, h, fill, adj=0.5)
    tb(sl, x, y, w, h, text, size=size, color=color, bold=True, align="c", anchor="m", pad=0.05)


def base(n, title=None, sub=None):
    sl = prs.slides.add_slide(BLANK)
    sl.background.fill.solid()
    sl.background.fill.fore_color.rgb = rgb(BG)
    rect(sl, 0, 0, W, 0.06, BLUE, rounded=False)
    if n > 1:
        tb(sl, ML - 0.15, 7.02, 7, 0.35, "RWB Mesh · ПВЗ как узлы сети", size=10, color=MUTE)
        tb(sl, W - MR - 2.0 + 0.15, 7.02, 2.0, 0.35, f"{n} / {TOTAL}", size=10, color=MUTE, align="r")
    if title:
        tb(sl, ML - 0.15, 0.42, CW + 0.3, 1.05, title, size=26, bold=True, anchor="t")
        if sub:
            tb(sl, ML - 0.15, 1.5, CW + 0.3, 0.4, sub, size=14, color=SUB)
    return sl


def notes(sl, text):
    sl.notes_slide.notes_text_frame.text = text


def picture(sl, path, x, y, w):
    h = w * 900 / 1440
    rect(sl, x - 0.06, y - 0.06, w + 0.12, h + 0.12, LINE, rounded=False)
    sl.shapes.add_picture(str(path), Inches(x), Inches(y), Inches(w), Inches(h))
    return h


# ---------------------------------------------------------------- 1. Титул
s = base(1)
pill(s, ML, 0.95, 3.9, 0.5, "ХАКАТОН RWB · ТОП ИИ", BLUE, color="FFFFFF", size=13)
tb(s, ML - 0.15, 1.75, CW + 0.3, 1.9, "Посылка не должна ехать через Казань", size=46, bold=True, anchor="t")
tb(s, ML - 0.15, 3.75, CW + 0.3, 0.6, "Mesh-доставка через ПВЗ: 3 часа вместо 30", size=22, color=GREEN, bold=True)
card(s, ML, 5.0, CW, 1.7, CARD, LINE, accent=BLUE)
tb(s, ML + 0.25, 5.1, CW - 0.5, 0.45, "КОМАНДА", size=12, color=SUB, bold=True)
tb(s, ML + 0.25, 5.45, CW - 0.5, 0.5,
   "Меркушев Михаил · Казаков Евгений · Родионова Дарья · Степанова Татьяна", size=16, bold=True)
tb(s, ML + 0.25, 5.98, CW - 0.5, 0.6,
   "ПГТУ, Йошкар-Ола · Научный руководитель: Курасов П. А.", size=14, color=SUB)
notes(s, "Команда: Меркушев, Казаков, Родионова, Степанова. Научный руководитель — Курасов П. А.")

# ---------------------------------------------------------------- 2. Проблема
s = base(2, "Заказ внутри одного города едет через хаб: 30 часов и 135,50 ₽",
         "Сегодня звезда: каждая посылка проходит через центр, даже если оба пункта рядом")
cw, gp = 3.5, 0.75
steps = [("ПВЗ → Хаб", "4 ч", AMBER), ("Магистраль", "22 ч", RED), ("Последняя миля", "4 ч", AMBER)]
for i, (name, hrs, col) in enumerate(steps):
    x = ML + i * (cw + gp)
    card(s, x, 1.95, cw, 1.9, CARD, LINE, accent=col)
    tb(s, x + 0.1, 2.05, cw - 0.2, 0.5, name, size=15, color=SUB, bold=True, align="c")
    tb(s, x + 0.1, 2.55, cw - 0.2, 1.1, hrs, size=44, color=col, bold=True, align="c", anchor="m")
    if i < 2:
        tb(s, x + cw, 2.5, gp, 0.8, "→", size=32, color=MUTE, bold=True, align="c", anchor="m", pad=0)
kpi(s, ML, 4.25, 3.9, 2.35, "30 ч", "до получения в своём городе", RED, vsize=48)
kpi(s, ML + 3.9 + 0.165, 4.25, 3.9, 2.35, "135,50 ₽", "логистика одного заказа", RED, vsize=44)
card(s, ML + 2 * (3.9 + 0.165), 4.25, 3.9, 2.35, CARD2, LINE)
tb(s, ML + 2 * (3.9 + 0.165) + 0.1, 4.35, 3.7, 2.15,
   ["Оба пункта — в Йошкар-Оле.", "Посылка уезжает в Казань и возвращается."], size=15, color=TXT, anchor="m", gap=8)

# ---------------------------------------------------------------- 3. Решение
s = base(3, "Убираем хаб из города: пункты выдачи — узлы одной сети",
         "Хаб остаётся только там, где он нужен: между городами")
card(s, ML, 1.95, 6.1, 1.9, CARD, LINE, accent=RED)
tb(s, ML + 0.2, 2.0, 5.8, 0.45, "Звезда через хаб", size=17, bold=True, color=RED)
tb(s, ML + 0.2, 2.45, 5.8, 1.35, ["Все потоки идут через центр.", "Лишняя перевалка и магистраль даже внутри города."], size=14, color=SUB, gap=4)
card(s, ML, 4.05, 6.1, 1.9, CARD, LINE, accent=GREEN)
tb(s, ML + 0.2, 4.1, 5.8, 0.45, "Mesh-сеть ПВЗ", size=17, bold=True, color=GREEN)
tb(s, ML + 0.2, 4.55, 5.8, 1.35, ["Пункты соединены напрямую.", "Остаток уже лежит рядом с покупателем."], size=14, color=SUB, gap=4)
rx = ML + 6.1 + 0.25
rw = CW - 6.1 - 0.25
kpi(s, rx, 1.95, rw, 1.9, "90 ₽", "вместо 135,50 ₽ за доставку", GREEN, vsize=44)
kpi(s, rx, 4.05, rw, 1.9, "3 ч", "вместо 30 ч до получения", GREEN, vsize=44)
pill(s, ML, 6.2, CW, 0.55, "−45,50 ₽ и −27 ч на каждом городском заказе", GREEN, size=15)

# ---------------------------------------------------------------- 4. Экономика
s = base(4, "Выигрывают все четыре стороны — и цепочка в целом",
         "Городской заказ, товар 2 500 ₽: схема через хаб против mesh")
rows = [
    ("Сторона", "Через хаб", "Mesh", "Δ"),
    ("ПВЗ", "27,50 ₽", "42,50 ₽", "+15,00 ₽"),
    ("Маркетплейс", "358,50 ₽", "359,00 ₽", "+0,50 ₽"),
    ("Продавец", "367,50 ₽", "367,50 ₽", "0,00 ₽"),
    ("Покупатель", "2 659,00 ₽", "2 629,00 ₽", "−30,00 ₽ / −1,13%"),
    ("Цепочка (прибыль)", "753,50 ₽", "769,00 ₽", "+15,50 ₽"),
    ("Срок доставки", "30 ч", "3 ч", "−27 ч / −90%"),
]
colw = [3.9, 2.6, 2.6, 2.93]
rh = 0.58
gt = s.shapes.add_table(len(rows), 4, Inches(ML), Inches(1.95), Inches(CW), Inches(rh * len(rows)))
tbl = gt.table
tblPr = gt._element.graphic.graphicData.tbl.tblPr
for a in ("firstRow", "bandRow"):
    tblPr.set(a, "0")
for j, w_ in enumerate(colw):
    tbl.columns[j].width = Inches(w_)
for i, row in enumerate(rows):
    tbl.rows[i].height = Inches(rh)
    for j, val in enumerate(row):
        c = tbl.cell(i, j)
        c.fill.solid()
        c.fill.fore_color.rgb = rgb(CARD2 if i == 0 else (CARD if i % 2 else BG))
        c.vertical_anchor = MSO_ANCHOR.MIDDLE
        c.margin_left = c.margin_right = Inches(0.15)
        tf = c.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.LEFT if j == 0 else PP_ALIGN.RIGHT
        r = p.add_run()
        r.text = val
        r.font.name = FONT
        r.font.size = Pt(14 if i else 13)
        r.font.bold = i == 0 or j == 3 or i >= 5
        if i == 0:
            col = SUB
        elif j == 3:
            col = GREEN
        else:
            col = TXT
        r.font.color.rgb = rgb(col)
tb(s, ML - 0.15, 6.2, CW + 0.3, 0.7,
   "Параметры модели — economics/params.yaml: эталон 2 500 ₽, 1,5 ячейки, 3 дня хранения.",
   size=12, color=SUB)

# ---------------------------------------------------------------- 5. Устойчивость
s = base(5, "Точка выдачи выходит в ноль с 38 операций в день — запас мощности втрое",
         "Постоянные расходы 1 200 ₽/день; очередь считается как M/M/1")
tw = (CW - 2 * 0.3) / 3
tiles = [
    ("38 оп/день", "безубыточность при 30% mesh\n33 оп/день при 62%", GREEN),
    ("120 оп/день", "мощность точки\nпостоянные расходы 1 200 ₽/день", BLUE),
    ("80–85%", "рабочий коридор очереди\nожидание 0,80–1,13 ч", AMBER),
]
for i, (v, l, c) in enumerate(tiles):
    x = ML + i * (tw + 0.3)
    card(s, x, 1.95, tw, 2.75, CARD, LINE)
    tb(s, x, 2.1, tw, 1.2, v, size=38, color=c, bold=True, align="c", anchor="m")
    tb(s, x + 0.1, 3.4, tw - 0.2, 1.2, l.split("\n"), size=13, color=SUB, align="c", gap=3)
card(s, ML, 4.95, CW, 1.75, CARD2, LINE, accent=AMBER)
tb(s, ML + 0.25, 5.0, CW - 0.5, 0.45, "Длительное хранение", size=16, bold=True, color=AMBER)
tb(s, ML + 0.25, 5.45, CW - 0.5, 1.2,
   ["21-й день — алерт продавцу и предложение переместить остаток за 60 ₽.",
    "52 дня — порог, после которого хранение обнуляет прибыль продавца."], size=14, color=TXT, gap=4)

# ---------------------------------------------------------------- 6. Межгород и масштаб
s = base(6, "Межгород работает с предразмещением, а overflow 85% держит сеть на 2000 заказах",
         "Без остатка в городе получателя — честный откат на хаб")
cw2 = (CW - 0.3) / 2
card(s, ML, 1.95, cw2, 4.75, CARD, LINE, accent=BLUE)
tb(s, ML + 0.25, 2.0, cw2 - 0.4, 0.5, "Межгород", size=18, bold=True, color=BLUE)
inter = [("10,50 ₽", "магистраль на заказ", AMBER), ("+5,00 ₽", "прибыль цепочки", GREEN),
         ("−30 ₽ · 3 ч", "покупателю", GREEN)]
for i, (v, l, c) in enumerate(inter):
    y = 2.6 + i * 0.95
    tb(s, ML + 0.25, y, 2.6, 0.8, v, size=22, bold=True, color=c, anchor="m")
    tb(s, ML + 2.85, y, cw2 - 3.0, 0.8, l, size=14, color=SUB, anchor="m")
tb(s, ML + 0.25, 5.5, cw2 - 0.4, 1.1, "Нет остатка в городе получателя — заказ уходит через хаб, и интерфейс говорит об этом прямо.",
   size=13, color=SUB, anchor="m")
x2 = ML + cw2 + 0.3
card(s, x2, 1.95, cw2, 4.75, CARD, LINE, accent=GREEN)
tb(s, x2 + 0.25, 2.0, cw2 - 0.4, 0.5, "Overflow при 2000 заказов", size=18, bold=True, color=GREEN)
tb(s, x2 + 0.25, 2.55, 2.4, 0.4, "", size=12)
tb(s, x2 + 2.4, 2.55, 1.6, 0.4, "Наивный mesh", size=12, color=SUB, bold=True, align="r", pad=0.05)
tb(s, x2 + 4.0, 2.55, 1.6, 0.4, "Overflow 85%", size=12, color=GREEN, bold=True, align="r", pad=0.05)
cmp_rows = [("Опоздания", "2000", "953"), ("Макс. загрузка", "226%", "136%"),
            ("Потери отмен", "701,6 тыс. ₽", "351,9 тыс. ₽"), ("Маржа МП", "5,75%", "10,00%")]
for i, (l, a, b) in enumerate(cmp_rows):
    y = 3.0 + i * 0.9
    rect(s, x2 + 0.2, y, cw2 - 0.4, 0.78, CARD2, adj=0.12)
    tb(s, x2 + 0.25, y, 2.2, 0.78, l, size=13, color=TXT, anchor="m", pad=0.08)
    tb(s, x2 + 2.2, y, 1.8, 0.78, a, size=13, color=RED, bold=True, align="r", anchor="m", pad=0.05)
    tb(s, x2 + 3.95, y, 1.7, 0.78, b, size=13, color=GREEN, bold=True, align="r", anchor="m", pad=0.05)

# ---------------------------------------------------------------- 7. Дашборд
s = base(7, f"Живой стенд: {STAND['orders']} заказов, {STAND['mesh_pct']}% из них — по mesh",
         "Дашборд на чистой базе после демо-прогона; цифры считает API")
pw = 7.9
ph = picture(s, SHOTS / "01-dashboard.png", ML, 1.95, pw)
rx = ML + pw + 0.35
rw = CW - pw - 0.35
facts = [(f"{STAND['points']} точек", "8 пунктов + хаб Казани", BLUE),
         (f"{STAND['orders']} заказов · {STAND['mesh_pct']}% mesh", "против хаба", GREEN),
         (f"Логистика {STAND['logistics']}", "внешние затраты, не прибыль", AMBER),
         (f"{STAND['tests']} автотестов", f"{STAND['paths']} путей OpenAPI", BLUE)]
fh = (ph - 3 * 0.15) / 4
for i, (v, l, c) in enumerate(facts):
    y = 1.95 + i * (fh + 0.15)
    card(s, rx, y, rw, fh, CARD, LINE, accent=c)
    tb(s, rx + 0.1, y + 0.05, rw - 0.15, fh * 0.55, v, size=15, bold=True, color=TXT, anchor="b", pad=0.1)
    tb(s, rx + 0.1, y + fh * 0.55, rw - 0.15, fh * 0.4, l, size=12, color=SUB, anchor="t", pad=0.1)

# ---------------------------------------------------------------- 8. Покупатель + MVP
s = base(8, "Покупатель видит статус по токену, а сеть переживает отказ узла",
         "Прозрачность без входа в систему и устойчивость MVP")
pw = 6.9
ph = picture(s, SHOTS / "06-buyer.png", ML, 1.95, pw)
pill(s, ML, 1.95 + ph + 0.25, pw, 0.5, "К оплате 19 029 ₽ · доставка 129 ₽ · ссылка с токеном", BLUE, color="FFFFFF", size=12)
rx = ML + pw + 0.35
rw = CW - pw - 0.35
caps = [("Отказ узла", "split-эвакуация остатков по соседям", RED),
        ("Скоринг размещения", "куда завезти остаток заранее", BLUE),
        ("Аудит остатков", "сверка ячеек, резервов и ledger", AMBER),
        ("Демо-кокпит 5+2", "5 обязательных и 2 расширенных сценария", GREEN)]
fh = 1.12
for i, (v, l, c) in enumerate(caps):
    y = 1.95 + i * (fh + 0.15)
    card(s, rx, y, rw, fh, CARD, LINE, accent=c)
    tb(s, rx + 0.1, y + 0.05, rw - 0.15, 0.5, v, size=15, bold=True, color=TXT, anchor="b", pad=0.1)
    tb(s, rx + 0.1, y + 0.55, rw - 0.15, 0.55, l, size=12, color=SUB, anchor="t", pad=0.1)

# ---------------------------------------------------------------- 9. Ограничения и пилот
s = base(9, "Мы честно показываем границы и предлагаем пилот на 30 дней",
         "Прототип на синтетических данных, не продакшн")
lw = 5.3
card(s, ML, 1.95, lw, 4.75, CARD, LINE, accent=AMBER)
tb(s, ML + 0.25, 2.0, lw - 0.4, 0.5, "Честные ограничения", size=18, bold=True, color=AMBER)
tb(s, ML + 0.25, 2.6, lw - 0.4, 4.0,
   ["• Хаб, магистраль и платёжный шлюз — заглушки",
    "• Тарифы синтетические, это параметры модели, не прайс RWB",
    "• Демо-ключи вместо OIDC",
    "• SQLite: один писатель, нет очереди"], size=14, color=TXT, gap=10)
px = ML + lw + 0.3
pw_ = CW - lw - 0.3
plan = [("Пилот 30 дней", "8 точек, 288 тыс. ₽, метрики M1–M5", GREEN),
        ("PostgreSQL + OIDC + аудит", "продакшн-основа и реальные интеграции", BLUE),
        ("Тиражирование на города", "плотность пунктов — главный ресурс", BLUE)]
sh = 1.4
for i, (v, l, c) in enumerate(plan):
    y = 1.95 + i * (sh + 0.275)
    card(s, px, y, pw_, sh, CARD, LINE)
    rect(s, px + 0.25, y + 0.35, 0.7, 0.7, c, adj=0.5)
    tb(s, px + 0.25, y + 0.35, 0.7, 0.7, str(i + 1), size=20, bold=True, color=BG, align="c", anchor="m", pad=0)
    tb(s, px + 1.15, y + 0.12, pw_ - 1.3, 0.55, v, size=16, bold=True, anchor="b", pad=0.05)
    tb(s, px + 1.15, y + 0.7, pw_ - 1.3, 0.6, l, size=13, color=SUB, pad=0.05)
    if i < 2:
        tb(s, px + 0.25, y + sh - 0.02, 0.7, 0.3, "↓", size=14, color=MUTE, align="c", pad=0)

# ---------------------------------------------------------------- 10. Финал
s = base(10)
tb(s, ML - 0.15, 0.9, CW + 0.3, 2.3,
   "Городская доставка быстрее в 10 раз — без новых складов, на точках, которые уже стоят",
   size=32, bold=True, align="c", anchor="m")
bw = (CW - 2 * 0.3) / 3
for i, (t, c) in enumerate([("−27 ч покупателю", GREEN), ("+15 ₽ ПВЗ", BLUE), ("148 тестов / CI green", AMBER)]):
    x = ML + i * (bw + 0.3)
    card(s, x, 3.65, bw, 1.15, CARD, LINE, accent=c)
    tb(s, x + 0.1, 3.65, bw - 0.1, 1.15, t, size=18, bold=True, color=c, align="c", anchor="m")
tb(s, ML - 0.15, 5.2, CW + 0.3, 0.5,
   "Меркушев Михаил · Казаков Евгений · Родионова Дарья · Степанова Татьяна", size=15, color=SUB, align="c")
tb(s, ML - 0.15, 5.9, CW + 0.3, 0.7, "Спасибо! Готовы к вопросам", size=26, bold=True, color=BLUE, align="c")
rect(s, 0, H - 0.06, W, 0.06, BLUE, rounded=False)

assert len(prs.slides) == TOTAL
OUT.parent.mkdir(parents=True, exist_ok=True)
prs.save(str(OUT))
print("saved", OUT)

res = subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(OUT.parent), str(OUT)],
                     capture_output=True, text=True)
print(res.stdout.strip() or res.stderr.strip())
sys.exit(res.returncode)
