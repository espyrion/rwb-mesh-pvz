"""Сборка презентации docs/pitch/RWB-Mesh-PVZ-pitch.pptx (10 слайдов, 16:9) и PDF.

Запуск: .venv/bin/python scripts/build_pitch.py
Палитра «Editorial Ivory & RWB Plum»: слайды 1 и 10 — тёмные обложки, 2–9 — светлый
редакционный стиль. Числа — из economics/params.yaml и docs/ECON_REPORT.md; цифры стенда
(слайд 7) и скриншоты — docs/screenshots/01-dashboard.png, 06-buyer.png.
"""
from __future__ import annotations

import math
import re
import subprocess
import sys
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "pitch" / "RWB-Mesh-PVZ-pitch.pptx"
SHOTS = ROOT / "docs" / "screenshots"

FONT = "DejaVu Sans"
# тёмные обложки
OBS, PLUM_D, MAGENTA = "140C1C", "6D1D7C", "B83280"
WHITE, WSUB, MINT = "FAF8FC", "C4B8D1", "34D399"
# светлые слайды
BG, CARD, LINE = "F6F4F0", "FFFFFF", "E4DFD7"
INK, MUTE, ZEBRA = "18151F", "605A6E", "FAF8F5"
PLUM, PLUM_L = "7B1E7A", "F4E8F5"
EMER, EMER_L = "0D9468", "E6F7F0"
CORAL, CORAL_L = "C82849", "FCECEF"
AMBER, AMBER_L = "C97A10", "FDF3E3"

W, H = 13.333, 7.5
ML = 0.65
CW = W - 2 * ML
TOTAL = 10
PAD = 0.18
GAP = 0.28

# Фактические цифры стенда (чистая БД + scripts/demo.py + 4 заказа) и число тестов
STAND = {"points": 9, "orders": 5, "mesh_pct": 80, "tests": 149}


def rgb(h: str) -> RGBColor:
    return RGBColor.from_string(h)


def nb(s: str) -> str:
    """Неразрывные пробелы в числах и перед единицами, чтобы «2 659,00 ₽» не рвалось."""
    s = re.sub(r"(?<=\d) (?=\d{3}(?!\d))", "\u00a0", s)
    return re.sub(r" (?=₽|ч(?![а-яё])|дн|тыс\.|оп/день|%|яч\.)", "\u00a0", s)


prs = Presentation()
prs.slide_width, prs.slide_height = Inches(W), Inches(H)
BLANK = prs.slide_layouts[6]


def set_alpha(shp, pct: int) -> None:
    clr = shp.fill._xPr.find(qn("a:solidFill")).find(qn("a:srgbClr"))
    etree.SubElement(clr, qn("a:alpha")).set("val", str(pct * 1000))


def shape(sl, kind, x, y, w, h, fill=None, line=None, lw=0.75, adj=None, alpha=None):
    shp = sl.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    if adj is not None and kind == MSO_SHAPE.ROUNDED_RECTANGLE:
        shp.adjustments[0] = adj
    if fill:
        shp.fill.solid()
        shp.fill.fore_color.rgb = rgb(fill)
        if alpha is not None:
            set_alpha(shp, alpha)
    else:
        shp.fill.background()
    if line:
        shp.line.color.rgb = rgb(line)
        shp.line.width = Pt(lw)
    else:
        shp.line.fill.background()
    shp.shadow.inherit = False
    return shp


def rect(sl, x, y, w, h, fill=None, line=None, lw=0.75, alpha=None):
    return shape(sl, MSO_SHAPE.RECTANGLE, x, y, w, h, fill, line, lw, alpha=alpha)


def rrect(sl, x, y, w, h, fill=None, line=None, lw=0.75, adj=0.06, alpha=None):
    return shape(sl, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h, fill, line, lw, adj, alpha)


def oval(sl, x, y, d, fill=None, line=None, lw=0.75, alpha=None):
    return shape(sl, MSO_SHAPE.OVAL, x, y, d, d, fill, line, lw, alpha=alpha)


def line(sl, x1, y1, x2, y2, color, lw=1.5):
    c = sl.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    c.line.color.rgb = rgb(color)
    c.line.width = Pt(lw)
    return c


ALIGN = {"l": PP_ALIGN.LEFT, "c": PP_ALIGN.CENTER, "r": PP_ALIGN.RIGHT}
ANCH = {"t": MSO_ANCHOR.TOP, "m": MSO_ANCHOR.MIDDLE, "b": MSO_ANCHOR.BOTTOM}


def TR(sl, x, y, w, h, paras, align="l", anchor="t", space=0):
    """paras: список абзацев; абзац — список ранов (текст, pt, цвет, bold)."""
    box = sl.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = ANCH[anchor]
    for i, runs in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = ALIGN[align]
        if space and i:
            p.space_before = Pt(space)
        for text, size, color, bold in runs:
            r = p.add_run()
            r.text = nb(text)
            r.font.name = FONT
            r.font.size = Pt(size)
            r.font.bold = bold
            r.font.color.rgb = rgb(color)
    return box


def T(sl, x, y, w, h, text, size, color=INK, bold=False, align="l", anchor="t"):
    return TR(sl, x, y, w, h, [[(text, size, color, bold)]], align, anchor)


def pill_w(text: str, size: float, bold=True) -> float:
    return len(text) * size * (0.0086 if bold else 0.0078) + 0.42


def badge(sl, x, y, text, color=PLUM, bg=PLUM_L, size=10, fixed_w=None):
    w = fixed_w or pill_w(text, size)
    rrect(sl, x, y, w, 0.3, bg, adj=0.5)
    T(sl, x, y, w, 0.3, text, size, color, True, "c", "m")


def light_slide(n: int, tag: str, title: str, tag_color=PLUM, tag_bg=PLUM_L):
    sl = prs.slides.add_slide(BLANK)
    rect(sl, 0, 0, W, H, BG)
    badge(sl, ML, 0.5, tag.upper(), tag_color, tag_bg)
    T(sl, ML, 0.95, CW, 1.0, title, 27, INK, True)
    rect(sl, ML, 6.98, CW, 0.012, LINE)
    T(sl, ML, 7.06, 6, 0.25, "RWB Mesh · ПВЗ как узлы сети", 9.5, MUTE)
    T(sl, W - ML - 2, 7.06, 2, 0.25, f"{n:02d} / {TOTAL}", 9.5, MUTE, align="r")
    return sl


def card(sl, x, y, w, h, accent=PLUM, side="top", fill=CARD):
    rrect(sl, x, y, w, h, fill, LINE, 0.75, adj=0.03)
    t = 0.06
    if side == "top":
        rect(sl, x + 0.12, y, w - 0.24, t, accent)
    else:
        rect(sl, x, y + 0.12, t, h - 0.24, accent)


def frame(sl, img: Path, x, y, w):
    """Тёмная «рамка браузера»: полоска с тремя точками и URL, тонкая обводка."""
    bar = 0.32
    ih = w * 900 / 1440
    rrect(sl, x, y, w, bar + ih, "1E1B26", "2D2638", 1.0, adj=0.02)
    rect(sl, x + 0.01, y + bar - 0.01, w - 0.02, 0.01, "2D2638")
    for i, c in enumerate(("FF5F56", "FFBD2E", "27C93F")):
        oval(sl, x + 0.14 + i * 0.2, y + 0.095, 0.13, c)
    rrect(sl, x + 0.9, y + 0.06, w - 1.8, 0.2, "2A2533", adj=0.5)
    T(sl, x + 0.9, y + 0.06, w - 1.8, 0.2, "localhost:5173", 9.5, "A59BB5", False, "c", "m")
    sl.shapes.add_picture(str(img), Inches(x + 0.02), Inches(y + bar), Inches(w - 0.04), Inches(ih - 0.02))
    return bar + ih


def dark_slide():
    sl = prs.slides.add_slide(BLANK)
    rect(sl, 0, 0, W, H, OBS)
    return sl


# ------------------------------------------------------------------ 1. Титул
sl = dark_slide()
oval(sl, 8.4, -2.4, 7.6, PLUM_D, alpha=55)
oval(sl, 10.6, 2.9, 4.6, MAGENTA, alpha=38)
oval(sl, -1.6, 5.3, 3.8, PLUM_D, alpha=40)
rect(sl, 0, 0, 0.14, H, MAGENTA)
badge(sl, ML + 0.2, 0.95, "ХАКАТОН RWB · ТОП ИИ", WHITE, MAGENTA, 11)
TR(sl, ML + 0.2, 1.55, 10.4, 2.3, [[("Посылка не должна", 54, WHITE, True)],
                                  [("ехать через Казань", 54, WHITE, True)]])
TR(sl, ML + 0.2, 3.95, 10.4, 0.6, [[("Mesh-доставка через ПВЗ: ", 24, WSUB, False),
                                   ("3 часа вместо 30", 24, MINT, True)]])
rrect(sl, ML + 0.2, 5.45, CW - 0.2, 1.3, "24172F", "3A2B4A", 0.75, adj=0.08)
TR(sl, ML + 0.5, 5.65, CW - 0.8, 0.9, [
    [("Меркушев Михаил · Казаков Евгений · Родионова Дарья · Степанова Татьяна", 15, WHITE, True)],
    [("ПГТУ, Йошкар-Ола  |  Науч. рук. Курасов П. А.", 12, WSUB, False)]], anchor="m", space=6)

# ------------------------------------------------------------------ 2. Проблема
sl = light_slide(2, "Проблема", "Чтобы доехать на соседнюю улицу, посылка едет в другой город",
                 CORAL, CORAL_L)
cw3, arrow = 3.5, (CW - 3 * 3.5) / 2
steps = [("ПВЗ → Хаб", "4 ч"), ("Магистраль", "22 ч"), ("Последняя миля", "4 ч")]
for i, (name, hrs) in enumerate(steps):
    x = ML + i * (cw3 + arrow)
    card(sl, x, 2.2, cw3, 1.65, PLUM)
    T(sl, x + PAD, 2.42, cw3 - 2 * PAD, 0.3, name.upper(), 11, MUTE, True)
    T(sl, x + PAD, 2.85, cw3 - 2 * PAD, 0.9, hrs, 44, PLUM, True)
    if i < 2:
        shape(sl, MSO_SHAPE.RIGHT_ARROW, x + cw3 + 0.14, 2.85, arrow - 0.28, 0.45, CORAL)
rrect(sl, ML, 4.25, CW, 2.35, CORAL_L, "F1C4CD", 0.75, adj=0.04)
rect(sl, ML, 4.4, 0.06, 2.05, CORAL)
T(sl, ML + 0.5, 4.45, 6, 0.3, "ВЕРДИКТ ВНУТРИ ОДНОГО ГОРОДА", 11, CORAL, True)
T(sl, ML + 0.5, 4.9, 4.3, 1.2, "30 ч", 64, CORAL, True)
T(sl, ML + 0.5, 6.0, 4.3, 0.4, "от заказа до выдачи", 13, MUTE)
T(sl, 6.9, 4.9, 5.6, 1.2, "135,50 ₽", 64, CORAL, True)
T(sl, 6.9, 6.0, 5.6, 0.4, "себестоимость логистики одной посылки", 13, MUTE)

# ------------------------------------------------------------------ 3. Решение
sl = light_slide(3, "Решение и эффект", "Мы убрали хаб из города — и оставили его для межгорода", EMER, EMER_L)
pw, py, ph = 3.0, 2.2, 4.1


def ring(cx, cy, n, r):
    return [(cx + r * math.sin(2 * math.pi * k / n), cy - r * math.cos(2 * math.pi * k / n)) for k in range(n)]


for idx, (label, col, sub) in enumerate((("Звезда", CORAL, "135,50 ₽ · 30 ч"), ("Mesh-сеть", EMER, "90 ₽ · 3 ч"))):
    x = ML + idx * (pw + 0.2)
    card(sl, x, py, pw, ph, col)
    T(sl, x + PAD, py + 0.22, pw - 2 * PAD, 0.3, label.upper(), 11, col, True, "c")
    cx, cy = x + pw / 2, py + 2.0
    pts = ring(cx, cy, 5, 1.0)
    if idx == 0:
        for px, py2 in pts:
            line(sl, cx, cy, px, py2, "D9A3B0", 1.5)
        oval(sl, cx - 0.3, cy - 0.3, 0.6, CORAL)
        T(sl, cx - 0.3, cy - 0.3, 0.6, 0.6, "ХАБ", 11, WHITE, True, "c", "m")
    else:
        for a in range(5):
            line(sl, *pts[a], *pts[(a + 1) % 5], "7FCDB3", 1.5)
        for a, b in ((0, 2), (1, 3), (2, 4)):
            line(sl, *pts[a], *pts[b], "B5E3D1", 1.0)
    for px, py2 in pts:
        oval(sl, px - 0.19, py2 - 0.19, 0.38, PLUM if idx else MUTE)
    T(sl, x + PAD, py + 3.45, pw - 2 * PAD, 0.4, sub, 15, col, True, "c")
rx, rw = 7.3, 5.38
cwid = (rw - GAP) / 2
for i, (big, small) in enumerate((("90 ₽", "вместо 135,50 ₽"), ("3 ч", "вместо 30 ч"))):
    x = rx + i * (cwid + GAP)
    card(sl, x, 2.2, cwid, 2.55, EMER)
    T(sl, x, 2.65, cwid, 1.1, big, 56, EMER, True, "c", "m")
    T(sl, x + PAD, 3.95, cwid - 2 * PAD, 0.4, small, 14, MUTE, False, "c")
rrect(sl, rx, 5.05, rw, 1.25, EMER_L, "BFE6D6", 0.75, adj=0.08)
TR(sl, rx + 0.3, 5.1, rw - 0.6, 1.15, [[("Экономия: 45,50 ₽ и 27 часов", 17, EMER, True)],
                                       [("на каждом городском заказе", 14, MUTE, False)]], "c", "m", 4)

# ------------------------------------------------------------------ 4. Экономика
sl = light_slide(4, "Экономика", "Выигрывают все четыре стороны — без перекладывания из кармана в карман")
rows = [
    ("Сторона", "Хаб-схема", "Mesh", "Δ"),
    ("ПВЗ", "27,50 ₽", "42,50 ₽", "+15,00 ₽ / +54,55%"),
    ("Маркетплейс", "358,50 ₽", "359,00 ₽", "+0,50 ₽ / +0,14%"),
    ("Продавец", "367,50 ₽", "367,50 ₽", "0,00 ₽"),
    ("Покупатель", "2 659,00 ₽", "2 629,00 ₽", "−30,00 ₽ / −1,13%"),
    ("Цепочка (прибыль)", "753,50 ₽", "769,00 ₽", "+15,50 ₽ / +2,06%"),
    ("Срок доставки", "30 ч", "3 ч", "−27 ч / −90%"),
]
colw = [3.3, 2.6, 2.6, 3.533]
gt = sl.shapes.add_table(len(rows), 4, Inches(ML), Inches(2.2), Inches(CW), Inches(0.52 * len(rows)))
tbl = gt.table
tblPr = tbl._tbl.tblPr
for attr in ("firstRow", "bandRow"):
    tblPr.set(attr, "0")
sid = tblPr.find(qn("a:tableStyleId"))
if sid is None:
    sid = etree.SubElement(tblPr, qn("a:tableStyleId"))
sid.text = "{2D5ABB26-0587-4C30-8999-92F81FD0307C}"  # No Style, No Grid
for i, wv in enumerate(colw):
    tbl.columns[i].width = Inches(wv)


def border_bottom(cell, color=LINE, w=9525):
    tcPr = cell._tc.get_or_add_tcPr()
    ln = etree.Element(qn("a:lnB"))
    ln.set("w", str(w))
    sf = etree.SubElement(ln, qn("a:solidFill"))
    etree.SubElement(sf, qn("a:srgbClr")).set("val", color)
    tcPr.insert(0, ln)  # границы должны идти раньше заливки


for r, row in enumerate(rows):
    tbl.rows[r].height = Inches(0.52)
    chain = row[0].startswith("Цепочка")
    for c, val in enumerate(row):
        cell = tbl.cell(r, c)
        cell.margin_left = cell.margin_right = Inches(PAD)
        cell.margin_top = cell.margin_bottom = Inches(0.05)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        tf = cell.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.LEFT if c == 0 else PP_ALIGN.RIGHT
        run = p.add_run()
        run.text = nb(val)
        run.font.name = FONT
        if r == 0:
            fill, col, bold, size = PLUM, WHITE, True, 12
        else:
            fill = PLUM_L if chain else (ZEBRA if r % 2 == 0 else CARD)
            col, bold, size = INK, chain or c == 0 or c == 3, 14
            if c == 3:
                col = MUTE if val.startswith("0,00") else EMER
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = rgb(col)
        cell.fill.solid()
        cell.fill.fore_color.rgb = rgb(fill)
        if r:
            border_bottom(cell, PLUM if chain else LINE, 12700 if chain else 9525)
T(sl, ML, 6.15, CW, 0.5,
  "Параметры модели economics/params.yaml (не прайс RWB): эталон 2 500 ₽, 1,5 яч., 3 дня хранения.",
  11.5, MUTE)

# ------------------------------------------------------------------ 5. ПВЗ и очередь
sl = light_slide(5, "Устойчивость", "Партнёр окупается на трети мощности, а очередь держим под контролем",
                 AMBER, AMBER_L)
kw = (CW - 2 * GAP) / 3
kpis = [
    (PLUM, "БЕЗУБЫТОЧНОСТЬ", [("38", 58), (" оп/день", 20)],
     "при 30% mesh-заказов. При 62% — 33 оп/день. Фикс. 1 200 ₽/день, мощность 120."),
    (AMBER, "ОЧЕРЕДЬ M/M/1", [("80–85%", 52)],
     "рабочий коридор: ожидание 0,80–1,13 ч. Выше 85% — авто-перелив в хаб."),
    (EMER, "ХРАНЕНИЕ", [("21 → 52", 46), (" дн", 20)],
     "на 21-й день алерт и переезд за 60 ₽. На 52-й день плата обнуляет прибыль продавца."),
]
for i, (col, lab, big, desc) in enumerate(kpis):
    x = ML + i * (kw + GAP)
    card(sl, x, 2.2, kw, 4.2, col)
    T(sl, x + 0.25, 2.55, kw - 0.5, 0.3, lab, 11, col, True)
    TR(sl, x + 0.25, 3.0, kw - 0.5, 1.2, [[(t, s, col, True) for t, s in big]], anchor="m")
    rect(sl, x + 0.25, 4.45, 0.6, 0.04, LINE)
    T(sl, x + 0.25, 4.7, kw - 0.5, 1.6, desc, 14, MUTE)

# ------------------------------------------------------------------ 6. Межгород и стресс-тест
sl = light_slide(6, "Масштаб", "Адаптивный overflow спасает сеть при двойной перегрузке", EMER, EMER_L)
lw_ = 5.4
rw_ = CW - lw_ - GAP
card(sl, ML, 2.2, lw_, 4.35, PLUM)
badge(sl, ML + 0.25, 2.5, "МЕЖГОРОД", PLUM, PLUM_L)
T(sl, ML + 0.25, 2.95, lw_ - 0.5, 0.7, "Остаток заранее лежит в городе получателя", 15, INK, True)
for j, (big, cap, col) in enumerate((("10,50 ₽", "магистраль на заказ", PLUM), ("+5,00 ₽", "цепочке", EMER),
                                     ("−30 ₽ · 3 ч", "покупателю", EMER))):
    yy = 3.75 + j * 0.72
    T(sl, ML + 0.25, yy, 2.6, 0.55, big, 22, col, True, anchor="m")
    T(sl, ML + 2.9, yy, lw_ - 3.1, 0.55, cap, 13, MUTE, anchor="m")
T(sl, ML + 0.25, 5.95, lw_ - 0.5, 0.5, "Нет остатка — честный откат на хаб.", 13, CORAL, True)
rx = ML + lw_ + GAP
card(sl, rx, 2.2, rw_, 4.35, EMER)
badge(sl, rx + 0.25, 2.5, "2 000 ЗАКАЗОВ/ДЕНЬ · SEED 42", EMER, EMER_L)
data = [("Метрика", "Наивный mesh", "Overflow 85%"),
        ("Опоздания", "2 000", "953"),
        ("Макс. загрузка", "226%", "136%"),
        ("Потери отмен", "701,6 тыс. ₽", "351,9 тыс. ₽"),
        ("Маржа МП", "5,75%", "10,00%"),
        ("Доставка", "107,15 ₽", "124,12 ₽")]
cx = [rx + 0.25, rx + 2.2, rx + 4.0]
cwd = [1.9, 1.7, 1.55]
for r, row in enumerate(data):
    yy = 3.0 + r * 0.56
    if r:
        rect(sl, rx + 0.25, yy - 0.04, rw_ - 0.5, 0.01, LINE)
    for c, val in enumerate(row):
        if r == 0:
            T(sl, cx[c], yy, cwd[c], 0.45, val, 11, MUTE, True, "l" if c == 0 else "r", "m")
        else:
            colr = INK if c == 0 else (MUTE if c == 1 else EMER)
            size = 12 if c == 0 else (12.5 if c == 1 else 14)
            T(sl, cx[c], yy, cwd[c], 0.45, val, size, colr, c != 1, "l" if c == 0 else "r", "m")

# ------------------------------------------------------------------ 7. Архитектура и стенд
sl = light_slide(7, "Архитектура", "Работающий MVP: от приёмки до начислений в одном Docker-контуре")
bw, bg_ = 1.85, 0.3
arch = [("UI ПВЗ / Продавца", "Vanilla JS + nginx"), ("FastAPI", "40 эндпоинтов, роли, RBAC"),
        ("SQLite", "WAL + BEGIN IMMEDIATE")]
for i, (a, b) in enumerate(arch):
    x = ML + i * (bw + bg_)
    card(sl, x, 2.2, bw, 1.35, PLUM if i != 1 else EMER, "top")
    TR(sl, x + 0.12, 2.4, bw - 0.24, 1.1, [[(a, 13, INK, True)], [(b, 11, MUTE, False)]], "c", "m", 4)
    if i < 2:
        shape(sl, MSO_SHAPE.RIGHT_ARROW, x + bw + 0.04, 2.7, bg_ - 0.08, 0.3, PLUM)
ex = ML + bw + bg_
card(sl, ex, 3.8, 2 * bw + bg_, 0.7, AMBER, "left")
T(sl, ex + 0.2, 3.8, 2 * bw + bg_ - 0.3, 0.7, "Economics Engine · params.yaml", 12.5, INK, True, "c", "m")
line(sl, ex + bw / 2, 3.55, ex + bw / 2, 3.8, "B9B2C4", 1.5)
mw = (3 * bw + 2 * bg_ - 2 * 0.2) / 3
mini = [("100%", "5/5 сценариев ТЗ", EMER), (str(STAND["tests"]), "автотестов, CI green", PLUM),
        (f"{STAND['mesh_pct']}%", f"mesh на стенде: {STAND['points']} точек, {STAND['orders']} заказов", AMBER)]
for i, (big, cap, col) in enumerate(mini):
    x = ML + i * (mw + 0.2)
    card(sl, x, 4.85, mw, 1.65, col)
    T(sl, x + 0.1, 5.05, mw - 0.2, 0.6, big, 28, col, True, "c")
    T(sl, x + 0.12, 5.75, mw - 0.24, 0.7, cap, 11, MUTE, False, "c")
fx = 7.35
fw = W - ML - fx
fh = frame(sl, SHOTS / "01-dashboard.png", fx, 2.2, fw)
T(sl, fx, 2.2 + fh + 0.15, fw, 0.3, "Дашборд живого стенда: чистая БД, без правок", 11, MUTE, False, "c")

# ------------------------------------------------------------------ 8. Нештатные ситуации
sl = light_slide(8, "Устойчивость", "Система не теряет товар при отказе ПВЗ, а клиент видит реальный срок",
                 CORAL, CORAL_L)
blocks = [
    (CORAL, "Отказ узла + split-эвакуация", "партия делится, parent_batch_id хранит родство"),
    (AMBER, "Возврат и длительное хранение", "виртуальные часы и автоперемещение остатка"),
    (PLUM, "Скоринг размещения SKU", "mesh_pvz против hub по спросу и стоимости"),
    (EMER, "Сверка остатков + SLA-рейтинг", "рейтинг A–D, идемпотентный аудит"),
]
bh = 0.92
for i, (col, ttl, desc) in enumerate(blocks):
    y = 2.2 + i * (bh + 0.2)
    card(sl, ML, y, 6.55, bh, col, "left")
    oval(sl, ML + 0.25, y + (bh - 0.5) / 2, 0.5, col)
    T(sl, ML + 0.25, y + (bh - 0.5) / 2, 0.5, 0.5, str(i + 1), 14, WHITE, True, "c", "m")
    TR(sl, ML + 1.0, y + 0.08, 5.4, bh - 0.16, [[(ttl, 14.5, INK, True)], [(desc, 11.5, MUTE, False)]],
       anchor="m", space=3)
fx = 7.75
fw = W - ML - fx
fh = frame(sl, SHOTS / "06-buyer.png", fx, 2.2, fw)
T(sl, fx, 2.2 + fh + 0.15, fw, 0.5, "Покупатель: 19 029 ₽ и ссылка с токеном — без перебора номеров", 11, MUTE,
  False, "c")

# ------------------------------------------------------------------ 9. Ограничения и пилот
sl = light_slide(9, "Дорожная карта", "Честные границы прототипа и дорожная карта на 30 дней", AMBER, AMBER_L)
card(sl, ML, 2.2, 5.1, 4.3, CORAL, "left")
T(sl, ML + 0.35, 2.5, 4.5, 0.7, "Что честно оставлено заглушками", 16, CORAL, True)
for j, txt in enumerate(("Хаб, магистраль, платёжный шлюз", "Синтетические тарифы и данные", "Демо-ключи и SQLite")):
    yy = 3.5 + j * 0.9
    oval(sl, ML + 0.35, yy + 0.12, 0.16, CORAL)
    T(sl, ML + 0.7, yy, 4.1, 0.7, txt, 14, INK, False)
rx = ML + 5.1 + 0.5
steps = [("Шаг 1 · Пилот 30 дней", "8 ПВЗ Йошкар-Олы, 288 тыс. ₽ оперрасходов, метрики M1–M5 и stop-критерии"),
         ("Шаг 2 · Продакшн-ядро", "PostgreSQL, OIDC, журнал аудита, очереди задач и мониторинг"),
         ("Шаг 3 · Тиражирование", "склады, платежи и масштабирование на новые города")]
line(sl, rx + 0.3, 2.65, rx + 0.3, 5.75, "D3B6D6", 2.0)
for j, (ttl, desc) in enumerate(steps):
    yy = 2.2 + j * 1.5
    oval(sl, rx, yy + 0.2, 0.6, PLUM)
    T(sl, rx, yy + 0.2, 0.6, 0.6, str(j + 1), 18, WHITE, True, "c", "m")
    TR(sl, rx + 0.9, yy, W - ML - rx - 0.9, 1.25, [[(ttl, 16, PLUM, True)], [(desc, 13, MUTE, False)]],
       anchor="m", space=4)

# ------------------------------------------------------------------ 10. Финал
sl = dark_slide()
oval(sl, -2.2, -2.6, 7.2, PLUM_D, alpha=50)
oval(sl, 9.6, 3.6, 6.2, MAGENTA, alpha=34)
oval(sl, 5.2, 6.2, 3.0, PLUM_D, alpha=40)
TR(sl, ML, 1.0, CW, 2.3, [[("В 10 раз быстрее", 54, WHITE, True)], [("внутри города.", 54, WHITE, True)]], "c")
T(sl, ML + 1, 3.4, CW - 2, 0.9, "Не нужны новые склады — нужны точки, которые уже стоят.", 22, WSUB, False, "c")
bl = ["−27 ч покупателю", "+15 ₽ ПВЗ", f"5/5 сценариев и {STAND['tests']} тестов"]
widths = [pill_w(t, 13) for t in bl]
x = (W - (sum(widths) + 0.3 * (len(bl) - 1))) / 2
for t, wv in zip(bl, widths):
    rrect(sl, x - 0.06, 4.64, wv + 0.12, 0.62, MINT, adj=0.5, alpha=16)
    rrect(sl, x, 4.7, wv, 0.5, "1C1428", MINT, 1.25, adj=0.5)
    T(sl, x, 4.7, wv, 0.5, t, 13, MINT, True, "c", "m")
    x += wv + 0.3
T(sl, ML, 5.65, CW, 0.3, "Меркушев · Казаков · Родионова · Степанова", 13, WSUB, False, "c")
T(sl, ML, 6.2, CW, 0.5, "Спасибо. Переходим к живому демо →", 19, WHITE, True, "c")

assert len(prs.slides) == TOTAL, len(prs.slides)
OUT.parent.mkdir(parents=True, exist_ok=True)
prs.save(OUT)
print(f"saved {OUT} ({len(prs.slides)} slides)")

res = subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(OUT.parent), str(OUT)],
                     capture_output=True, text=True)
if res.returncode != 0:
    sys.exit(res.stderr)
print("pdf:", OUT.with_suffix(".pdf"))
