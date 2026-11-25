"""Сборка docs/pitch/FINAL-SPEECH.docx из docs/pitch/SPEECH.md.

Оформление под печать с листа: Calibri 14 pt, акцент #7B1E7A, интерлиньяж 1.3,
поля 2 см. Часть 1 — основной текст, части 2 и 3 — разделы помельче.

Запуск: .venv/bin/python scripts/build_final_speech_docx.py
"""
from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).resolve().parents[1]
_MD_DIR = ROOT / "docs" / "pitch"
MD = next(
    (p for p in (_MD_DIR / "md" / "SPEECH.md", _MD_DIR / "SPEECH.md") if p.exists()),
    _MD_DIR / "md" / "SPEECH.md",
)
OUT = _MD_DIR / "FINAL-SPEECH.docx"

ACCENT = RGBColor(0x7B, 0x1E, 0x7A)
INK = RGBColor(0x1F, 0x1F, 0x24)
MUTED = RGBColor(0x5A, 0x5A, 0x66)
FONT = "Calibri"


# ---------- низкоуровневые хелперы ----------

def set_font(run, size: float, *, bold: bool = False, italic: bool = False, color=INK) -> None:
    run.font.name = FONT
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = color
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(attr), FONT)


def para(doc, *, size=14, bold=False, italic=False, color=INK, space_before=0, space_after=6,
         line=1.3, align=None, keep_next=False, indent=0):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(space_before)
    pf.space_after = Pt(space_after)
    pf.line_spacing = line
    pf.left_indent = Cm(indent)
    pf.keep_with_next = keep_next
    if align is not None:
        p.alignment = align
    p._pstyle = (size, bold, italic, color)
    return p


def add_run(p, text: str, **kw) -> None:
    size, bold, italic, color = getattr(p, "_pstyle", (14, False, False, INK))
    set_font(p.add_run(text), kw.pop("size", size), bold=kw.pop("bold", bold),
             italic=kw.pop("italic", italic), color=kw.pop("color", color))


def bottom_rule(p, color: str = "7B1E7A", size: int = 8) -> None:
    ppr = p._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(size))
    bottom.set(qn("w:space"), "4")
    bottom.set(qn("w:color"), color)
    borders.append(bottom)
    ppr.append(borders)


def shade(cell, hex_fill: str) -> None:
    tcpr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tcpr.append(shd)


def page_number_footer(doc) -> None:
    footer_p = doc.sections[0].footer.paragraphs[0]
    footer_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_font(footer_p.add_run("RWB Mesh · защита · "), 9, color=MUTED)
    run = footer_p.add_run()
    set_font(run, 9, color=MUTED)
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    run._element.addnext(fld)


# ---------- разбор markdown ----------

def parse(md_text: str):
    """Возвращает (заголовок, подзаголовок, [(слайд, тайминг, текст)], таблица демо, вопросы)."""
    lines = md_text.splitlines()
    title = lines[0].lstrip("# ").strip()
    intro = [ln.strip() for ln in lines[1:] if ln.strip() and not ln.startswith("#")][0]

    part1 = md_text.split("## Часть 1")[1].split("## Часть 2")[0]
    slides = []
    head, timing, body = None, "", []
    for ln in part1.splitlines():
        s = ln.strip()
        if s.startswith("### "):
            if head:
                slides.append((head, timing, " ".join(body)))
            mm = re.match(r"###\s+(Слайд\s+\d+)\s+·\s+(.+?)\s*\(([^)]+)\)\s*$", s)
            if mm:
                head, timing, body = f"{mm.group(1)} · {mm.group(2)}", mm.group(3), ""
            else:
                head, timing, body = s[4:].strip(), "", ""
            body = []
        elif s and head:
            body.append(s)
    if head:
        slides.append((head, timing, " ".join(body)))

    part2 = md_text.split("## Часть 2")[1].split("## Часть 3")[0]
    # остаток строки заголовка (". Сценарий живого демо (2:00)") отбрасываем
    part2_lines = [ln for ln in part2.splitlines() if not ln.strip().startswith(".")]
    rows, table_at = [], None
    for i, ln in enumerate(part2_lines):
        s = ln.strip()
        if s.startswith("|") and not set(s) <= set("|-: "):
            if table_at is None:
                table_at = i
            cells = [c.strip() for c in s.strip("|").split("|")]
            if cells[0].lower().startswith("время"):
                continue
            rows.append(cells)
    lead = [ln.strip() for ln in part2_lines[: table_at or 0] if ln.strip()]
    tail = [ln.strip() for ln in part2_lines[(table_at or 0):] if ln.strip() and not ln.strip().startswith("|")]
    note2 = (lead, tail)

    part3 = md_text.split("## Часть 3")[1]
    qa = []
    for block in re.split(r"\n\s*\n", part3):
        block = block.strip()
        if block.startswith("**"):
            first, _, rest = block.partition("\n")
            qa.append((first.strip().strip("*"), rest.strip()))
    return title, intro, slides, rows, note2, qa


def plain(s: str) -> str:
    return re.sub(r"[*`]", "", s.replace("**", "")).strip()


# ---------- документ ----------

def build() -> Path:
    title, intro, slides, rows, note2, qa = parse(MD.read_text(encoding="utf-8"))

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    for side in ("top_margin", "bottom_margin", "left_margin", "right_margin"):
        setattr(sec, side, Cm(2))

    normal = doc.styles["Normal"]
    normal.font.name = FONT
    normal.font.size = Pt(14)
    normal.font.color.rgb = INK
    normal.paragraph_format.line_spacing = 1.3
    normal.paragraph_format.space_after = Pt(6)
    rpr = normal.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(attr), FONT)

    # --- шапка документа
    p = para(doc, size=20, bold=True, color=ACCENT, space_after=2, line=1.15)
    add_run(p, "RWB Mesh")
    p = para(doc, size=15, bold=True, space_after=4, line=1.2)
    add_run(p, "Пункты выдачи как узлы городской сети")
    p = para(doc, size=10.5, color=MUTED, space_after=14, line=1.15)
    add_run(p, "Речь на защиту · 2:50–3:00 · живое демо 2:00 · вопросы жюри")
    bottom_rule(p)

    # --- Часть 1
    p = para(doc, size=14, bold=True, color=ACCENT, space_before=6, space_after=8, keep_next=True)
    add_run(p, "Часть 1. Речь по слайдам")

    for head, timing, body in slides:
        p = para(doc, size=12, bold=True, color=ACCENT, space_before=10, space_after=2,
                 line=1.2, keep_next=True)
        add_run(p, head, size=12, bold=True, color=ACCENT)
        if timing:
            add_run(p, f"   {timing}", size=10, bold=True, color=MUTED)
        p = para(doc, size=14, space_after=8)
        add_run(p, body, size=14)

    # --- Часть 2
    doc.add_page_break()
    p = para(doc, size=13, bold=True, color=ACCENT, space_before=0, space_after=4, keep_next=True)
    add_run(p, "Часть 2. Сценарий живого демо (2:00)", size=13, bold=True, color=ACCENT)
    if note2[0]:
        p = para(doc, size=10, italic=True, color=MUTED, space_after=8)
        add_run(p, plain(note2[0][0]), size=10, italic=True, color=MUTED)

    table = doc.add_table(rows=1, cols=3)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    widths = (Cm(2.4), Cm(6.4), Cm(8.2))
    headers = ("Время", "Что кликнуть", "Что сказать")
    for cell, text, w in zip(table.rows[0].cells, headers, widths):
        cell.width = w
        shade(cell, "7B1E7A")
        cp = cell.paragraphs[0]
        cp.paragraph_format.space_after = Pt(2)
        cp.paragraph_format.line_spacing = 1.1
        add_run(cp, text, size=9.5, bold=True, color=RGBColor(0xFF, 0xFF, 0xFF))

    for idx, cells in enumerate(rows):
        tr = table.add_row()
        for j, (cell, text, w) in enumerate(zip(tr.cells, cells, widths)):
            cell.width = w
            if idx % 2 == 1:
                shade(cell, "F6F0F6")
            cp = cell.paragraphs[0]
            cp.paragraph_format.space_after = Pt(2)
            cp.paragraph_format.line_spacing = 1.1
            add_run(cp, plain(text), size=9, bold=(j == 0), color=INK)

    if note2[1]:
        p = para(doc, size=9.5, italic=True, color=MUTED, space_before=8)
        add_run(p, plain(" ".join(note2[1])), size=9.5, italic=True, color=MUTED)

    # --- Часть 3
    p = para(doc, size=13, bold=True, color=ACCENT, space_before=18, space_after=6,
             line=1.2, keep_next=True)
    add_run(p, "Часть 3. Топ-10 жёстких вопросов жюри", size=13, bold=True, color=ACCENT)

    for q, a in qa:
        p = para(doc, size=11, bold=True, space_before=8, space_after=1, line=1.2, keep_next=True)
        add_run(p, plain(q), size=11, bold=True, color=ACCENT)
        p = para(doc, size=10.5, space_after=4, line=1.2)
        add_run(p, plain(a), size=10.5)

    page_number_footer(doc)
    doc.core_properties.title = "RWB Mesh — речь на защиту"
    doc.core_properties.subject = "ПВЗ как узлы городской сети"
    doc.core_properties.author = "Команда RWB Mesh"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUT)
    return OUT


if __name__ == "__main__":
    path = build()
    print(f"saved: {path} ({path.stat().st_size} bytes)")