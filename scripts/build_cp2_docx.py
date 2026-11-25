#!/usr/bin/env python3
"""Convert the CP2 pitch Markdown docs to formatted .docx (python-docx only).

Formatting: Times New Roman 14pt body, 16pt bold headings,
Consolas 11pt code blocks with light-gray shading, bordered tables.
"""
from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

OUT_DIR = Path("/home/stingcorpse/mesh-pvz/docs/pitch")
FILES = ["CP2-DEMO-GUIDE.md", "CP2-CHEATSHEET.md", "CP2-ARCHITECTURE.md"]

BODY_FONT = "Times New Roman"
CODE_FONT = "Consolas"
BODY_SIZE = 14
HEAD_SIZE = 16
CODE_SIZE = 11
TABLE_SIZE = 12
HEAD_COLOR = RGBColor(0x17, 0x36, 0x5D)
TEXT_COLOR = RGBColor(0x1A, 0x1A, 0x1A)
MUTED = RGBColor(0x40, 0x40, 0x40)
USABLE_CM = 16.6  # A4 21cm minus 2.2cm margins
CODE_WIDTH = 66  # chars that fit at 11pt mono inside A4 margins
COMPACT = False  # dense layout for the one-page cheat sheet

# ---------------------------------------------------------------- xml helpers
PPR_AFTER_PBDR = (
    "w:shd", "w:tabs", "w:suppressAutoHyphens", "w:kinsoku", "w:wordWrap",
    "w:overflowPunct", "w:topLinePunct", "w:autoSpaceDE", "w:autoSpaceDN",
    "w:bidi", "w:adjustRightInd", "w:snapToGrid", "w:spacing", "w:ind",
    "w:contextualSpacing", "w:mirrorIndents", "w:suppressOverlap", "w:jc",
    "w:textDirection", "w:textAlignment", "w:textboxTightWrap", "w:outlineLvl",
    "w:divId", "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange",
)
PPR_AFTER_SHD = PPR_AFTER_PBDR[1:]
TCPR_AFTER_SHD = (
    "w:noWrap", "w:tcMar", "w:textDirection", "w:tcFitText", "w:vAlign",
    "w:hideMark", "w:headers", "w:cellIns", "w:cellDel", "w:cellMerge",
    "w:tcPrChange",
)


def _set_run_font(run, name: str, size: int, bold=None, italic=None, color=None):
    run.font.name = name
    run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold
    if italic is not None:
        run.font.italic = italic
    if color is not None:
        run.font.color.rgb = color
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.insert(0, rFonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rFonts.set(qn(attr), name)


def _shade_run(run, fill: str):
    rPr = run._element.get_or_add_rPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    rPr.append(shd)


def _para_shading(paragraph, fill: str):
    pPr = paragraph._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    pPr.insert_element_before(shd, *PPR_AFTER_SHD)


def _para_borders(paragraph, edges, sz=4, color="C8C8C8", space=4):
    pPr = paragraph._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    for edge in ("top", "left", "bottom", "right"):
        if edge not in edges:
            continue
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), str(edges[edge].get("sz", sz)))
        el.set(qn("w:space"), str(edges[edge].get("space", space)))
        el.set(qn("w:color"), edges[edge].get("color", color))
        pBdr.append(el)
    pPr.insert_element_before(pBdr, *PPR_AFTER_PBDR)


def _cell_shading(cell, fill: str):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tcPr.insert_element_before(shd, *TCPR_AFTER_SHD)


def _table_borders(table, color="A6A6A6", sz=6):
    tblPr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), str(sz))
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), color)
        borders.append(el)
    tblPr.insert_element_before(
        borders, "w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook",
        "w:tblCaption", "w:tblDescription",
    )


def _table_cell_margins(table, top=50, bottom=50, left=90, right=90):
    tblPr = table._tbl.tblPr
    mar = OxmlElement("w:tblCellMar")
    for edge, val in (("top", top), ("left", left), ("bottom", bottom), ("right", right)):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:w"), str(val))
        el.set(qn("w:type"), "dxa")
        mar.append(el)
    tblPr.insert_element_before(
        mar, "w:tblLook", "w:tblCaption", "w:tblDescription",
    )


def _table_fixed_layout(table, width_cm: float):
    # table.autofit = False already writes <w:tblLayout w:type="fixed">
    tblPr = table._tbl.tblPr
    tblW = tblPr.find(qn("w:tblW"))
    if tblW is None:
        tblW = OxmlElement("w:tblW")
        tblPr.append(tblW)
    tblW.set(qn("w:type"), "dxa")
    tblW.set(qn("w:w"), str(int(width_cm * 567)))


def _repeat_header(row):
    trPr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:tblHeader")
    el.set(qn("w:val"), "true")
    trPr.append(el)


def _cant_split(row):
    trPr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:cantSplit")
    el.set(qn("w:val"), "true")
    trPr.append(el)


def _add_field(paragraph, instr: str, size=9):
    run = paragraph.add_run()
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr_el = OxmlElement("w:instrText")
    instr_el.set(qn("xml:space"), "preserve")
    instr_el.text = instr
    fld_sep = OxmlElement("w:fldChar")
    fld_sep.set(qn("w:fldCharType"), "separate")
    placeholder = OxmlElement("w:t")
    placeholder.text = "1"
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    for el in (fld_begin, instr_el, fld_sep, placeholder, fld_end):
        run._r.append(el)
    _set_run_font(run, BODY_FONT, size, color=RGBColor(0x80, 0x80, 0x80))


# ---------------------------------------------------------------- inline text
INLINE_RE = re.compile(
    r"(\*\*[^*]+\*\*|`[^`]+`|\*[^*\n]+\*|\[[^\]]+\]\([^)]+\))"
)


def add_inline(paragraph, text: str, size=BODY_SIZE, bold=False, italic=False,
               color=TEXT_COLOR):
    """Render markdown emphasis/code/links into runs of one paragraph."""
    tokens = INLINE_RE.split(text)
    for token in tokens:
        if not token:
            continue
        if token.startswith("**") and token.endswith("**") and len(token) > 4:
            inner = token[2:-2]
            # allow inline code inside bold
            for sub in re.split(r"(`[^`]+`)", inner):
                if not sub:
                    continue
                if sub.startswith("`") and sub.endswith("`") and len(sub) > 2:
                    run = paragraph.add_run(sub[1:-1])
                    _set_run_font(run, CODE_FONT, CODE_SIZE, bold=True, color=color)
                    _shade_run(run, "F2F2F2")
                else:
                    run = paragraph.add_run(sub)
                    _set_run_font(run, BODY_FONT, size, bold=True, italic=italic,
                                  color=color)
        elif token.startswith("`") and token.endswith("`") and len(token) > 2:
            run = paragraph.add_run(token[1:-1])
            _set_run_font(run, CODE_FONT, CODE_SIZE, bold=bold, color=color)
            _shade_run(run, "F2F2F2")
        elif token.startswith("*") and token.endswith("*") and len(token) > 2:
            run = paragraph.add_run(token[1:-1])
            _set_run_font(run, BODY_FONT, size, bold=bold, italic=True,
                          color=color)
        elif token.startswith("[") and "](" in token:
            m = re.match(r"\[([^\]]+)\]\(([^)]+)\)", token)
            label, url = (m.group(1), m.group(2)) if m else (token, "")
            run = paragraph.add_run(f"{label} ({url})")
            _set_run_font(run, BODY_FONT, size, bold=bold, italic=True,
                          color=RGBColor(0x1F, 0x4E, 0x79))
        else:
            run = paragraph.add_run(token)
            _set_run_font(run, BODY_FONT, size, bold=bold, italic=italic,
                          color=color)


# ---------------------------------------------------------------- doc helpers
def setup_document(doc: Document):
    section = doc.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    if COMPACT:
        section.top_margin = Cm(1.6)
        section.bottom_margin = Cm(1.5)
        section.left_margin = Cm(1.9)
        section.right_margin = Cm(1.9)
    else:
        section.top_margin = Cm(2.0)
        section.bottom_margin = Cm(2.0)
        section.left_margin = Cm(2.2)
        section.right_margin = Cm(2.2)
    section.header_distance = Cm(1.0)
    section.footer_distance = Cm(1.0)

    normal = doc.styles["Normal"]
    normal.font.name = BODY_FONT
    normal.font.size = Pt(BODY_SIZE)
    normal.font.color.rgb = TEXT_COLOR
    normal.paragraph_format.space_after = Pt(3 if COMPACT else 6)
    normal.paragraph_format.line_spacing = 1.05 if COMPACT else 1.15
    rPr = normal.element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.insert(0, rFonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rFonts.set(qn(attr), BODY_FONT)
    lang = OxmlElement("w:lang")
    lang.set(qn("w:val"), "ru-RU")
    lang.set(qn("w:eastAsia"), "ru-RU")
    lang.set(qn("w:bidi"), "ar-SA")
    rPr.append(lang)

    head_spacing = (
        ("Heading 1", 2, 6),
        ("Heading 2", 8, 3),
        ("Heading 3", 7, 2),
    ) if COMPACT else (
        ("Heading 1", 6, 10),
        ("Heading 2", 14, 6),
        ("Heading 3", 12, 4),
    )
    for name, before, after in head_spacing:
        st = doc.styles[name]
        st.font.name = HEAD_FONT = BODY_FONT
        st.font.size = Pt(HEAD_SIZE)
        st.font.bold = True
        st.font.italic = False
        st.font.color.rgb = HEAD_COLOR
        st.paragraph_format.space_before = Pt(before)
        st.paragraph_format.space_after = Pt(after)
        st.paragraph_format.keep_with_next = True
        st.paragraph_format.line_spacing = 1.05
        st_rPr = st.element.get_or_add_rPr()
        st_rFonts = st_rPr.find(qn("w:rFonts"))
        if st_rFonts is None:
            st_rFonts = OxmlElement("w:rFonts")
            st_rPr.insert(0, st_rFonts)
        for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            st_rFonts.set(qn(attr), BODY_FONT)

    # header / footer
    header_p = section.header.paragraphs[0]
    header_p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = header_p.add_run("Mesh-ПВЗ · ЧП2 · Техническая сторона MVP")
    _set_run_font(run, BODY_FONT, 9, color=RGBColor(0x80, 0x80, 0x80))

    footer_p = section.footer.paragraphs[0]
    footer_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer_p.add_run("Стр. ")
    _set_run_font(run, BODY_FONT, 9, color=RGBColor(0x80, 0x80, 0x80))
    _add_field(footer_p, "PAGE")
    run = footer_p.add_run(" из ")
    _set_run_font(run, BODY_FONT, 9, color=RGBColor(0x80, 0x80, 0x80))
    _add_field(footer_p, "NUMPAGES")


def add_heading(doc, text: str, level: int):
    p = doc.add_paragraph(style=f"Heading {level}")
    add_inline(p, text, size=HEAD_SIZE, bold=True, color=HEAD_COLOR)
    if level == 1:
        _para_borders(
            p,
            {"bottom": {"sz": 8, "space": 4, "color": "17365D"}},
            space=4,
        )
        p.paragraph_format.space_after = Pt(12)
    elif level == 2:
        _para_borders(
            p,
            {"bottom": {"sz": 4, "space": 3, "color": "C9D6E8"}},
            space=3,
        )
    return p


def add_body(doc, text: str):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    add_inline(p, text)
    return p


def add_quote(doc, lines):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.left_indent = Cm(0.6)
    pf.right_indent = Cm(0.2)
    pf.space_before = Pt(4)
    pf.space_after = Pt(8)
    pf.line_spacing = 1.1
    _para_shading(p, "F7F7F7")
    _para_borders(
        p,
        {"left": {"sz": 18, "space": 8, "color": "8EA9DB"}},
        space=8,
    )
    for i, line in enumerate(lines):
        add_inline(p, line, color=MUTED, italic=True)
        if i < len(lines) - 1:
            p.add_run().add_break()
    return p


def add_list_item(doc, text: str, ordered, number, level):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    base = 0.75 + 0.65 * level
    pf.left_indent = Cm(base)
    pf.first_line_indent = Cm(-0.45)
    pf.space_before = Pt(1 if COMPACT else 2)
    pf.space_after = Pt(1 if COMPACT else 2)
    pf.line_spacing = 1.02 if COMPACT else 1.12
    pf.tab_stops.add_tab_stop(Cm(base), WD_TAB_ALIGNMENT.LEFT)
    marker = f"{number}." if ordered else ("•" if level == 0 else "–")
    run = p.add_run(marker + "\t")
    _set_run_font(run, BODY_FONT, BODY_SIZE, bold=ordered, color=TEXT_COLOR)
    add_inline(p, text)
    return p


def reflow_line(ln: str, width: int, cont: int):
    """Greedy wrap that keeps runs of spaces (ASCII diagram alignment)."""
    chunks = []
    s = ln
    while len(s) > width:
        cut = s.rfind(" ", 0, width + 1)
        if cut <= len(s) - len(s.lstrip()) + 1:
            cut = width
        chunks.append(s[:cut].rstrip())
        s = " " * cont + s[cut:].lstrip()
    chunks.append(s)
    return chunks


def reflow_diagram(code: str, width=CODE_WIDTH):
    """Soft-wrap ASCII diagrams so wrapped text keeps the tree hierarchy.

    Continuation lines are aligned under the description column (after an
    em dash) when there is one, otherwise indented four spaces past the
    original indentation. Only used for language-less blocks (diagrams).
    """
    out = []
    for ln in code.split("\n"):
        if len(ln) <= width:
            out.append(ln)
            continue
        base = len(ln) - len(ln.lstrip())
        dash = ln.find("—")
        cont = dash + 2 if 0 < dash <= 40 else base + 4
        if cont > width - 24:
            cont = base + 4
        out.extend(reflow_line(ln, width, cont))
    return "\n".join(out)


def add_code_block(doc, code: str, lang: str = ""):
    if not lang and any(len(ln) > CODE_WIDTH for ln in code.split("\n")):
        code = reflow_diagram(code)
    lines_hint = code.count("\n") + 1
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.left_indent = Cm(0.3)
    pf.right_indent = Cm(0.15)
    pf.space_before = Pt(3 if COMPACT else 5)
    pf.space_after = Pt(5 if COMPACT else 9)
    pf.line_spacing = 1.0
    # long directory trees must be allowed to flow across pages
    pf.keep_together = lines_hint <= 26
    _para_shading(p, "F2F2F2")
    _para_borders(p, {e: {} for e in ("top", "left", "bottom", "right")})
    lines = code.split("\n")
    for i, line in enumerate(lines):
        run = p.add_run(line)
        _set_run_font(run, CODE_FONT, CODE_SIZE, color=RGBColor(0x1F, 0x1F, 0x1F))
        if i < len(lines) - 1:
            run.add_break()
    return p


def add_hr(doc):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.line_spacing = 1.0
    run = p.add_run()
    _set_run_font(run, BODY_FONT, 2)
    _para_borders(p, {"bottom": {"sz": 4, "space": 1, "color": "D9D9D9"}})


def add_table(doc, rows, aligns):
    n_cols = max(len(r) for r in rows)
    table = doc.add_table(rows=0, cols=n_cols)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    _table_borders(table)
    _table_cell_margins(table)
    _table_fixed_layout(table, USABLE_CM)

    # column widths from content weight
    weights = []
    for c in range(n_cols):
        longest = max((len(r[c]) if c < len(r) else 0) for r in rows)
        weights.append(min(max(longest, 6), 46))
    total = sum(weights)
    widths = [max(2.0, USABLE_CM * w / total) for w in weights]
    scale = USABLE_CM / sum(widths)
    widths = [w * scale for w in widths]

    for r_idx, row_vals in enumerate(rows):
        row = table.add_row()
        _cant_split(row)
        if r_idx == 0:
            _repeat_header(row)
        for c_idx in range(n_cols):
            cell = row.cells[c_idx]
            cell.width = Cm(widths[c_idx])
            text = row_vals[c_idx] if c_idx < len(row_vals) else ""
            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(1)
            p.paragraph_format.space_after = Pt(1)
            p.paragraph_format.line_spacing = 1.0
            al = aligns[c_idx] if c_idx < len(aligns) else "left"
            p.alignment = {
                "left": WD_ALIGN_PARAGRAPH.LEFT,
                "center": WD_ALIGN_PARAGRAPH.CENTER,
                "right": WD_ALIGN_PARAGRAPH.RIGHT,
            }[al]
            if r_idx == 0:
                add_inline(p, text, size=TABLE_SIZE, bold=True,
                           color=RGBColor(0x17, 0x36, 0x5D))
                _cell_shading(cell, "DCE6F1")
            else:
                add_inline(p, text, size=TABLE_SIZE)
                if r_idx % 2 == 0:
                    _cell_shading(cell, "F5F8FC")
    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_before = Pt(0)
    spacer.paragraph_format.space_after = Pt(2)
    spacer.paragraph_format.line_spacing = 1.0
    for run in spacer.runs:
        run.font.size = Pt(2)
    return table


# ---------------------------------------------------------------- md parsing
FENCE_RE = re.compile(r"^\s*(```|~~~)\s*([\w+-]*)\s*$")
HEAD_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
UL_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
OL_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")


def split_row(line: str):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def is_table_start(lines, i):
    return (
        "|" in lines[i]
        and i + 1 < len(lines)
        and bool(TABLE_SEP_RE.match(lines[i + 1].strip()))
        and "-" in lines[i + 1]
    )


def parse_table(lines, i):
    rows = [split_row(lines[i])]
    aligns = []
    for cell in split_row(lines[i + 1]):
        cell = cell.strip()
        if cell.startswith(":") and cell.endswith(":"):
            aligns.append("center")
        elif cell.endswith(":"):
            aligns.append("right")
        else:
            aligns.append("left")
    i += 2
    while i < len(lines) and lines[i].strip().startswith("|"):
        if TABLE_SEP_RE.match(lines[i].strip()):
            i += 1
            continue
        rows.append(split_row(lines[i]))
        i += 1
    n = max(len(r) for r in rows)
    rows = [r + [""] * (n - len(r)) for r in rows]
    aligns = (aligns + ["left"] * n)[:n]
    return rows, aligns, i


def dedent(code_lines):
    non_empty = [ln for ln in code_lines if ln.strip()]
    if not non_empty:
        return code_lines
    indent = min(len(ln) - len(ln.lstrip()) for ln in non_empty)
    return [ln[indent:] if len(ln) >= indent else "" for ln in code_lines]


def convert(md_path: Path, docx_path: Path):
    global COMPACT, USABLE_CM
    COMPACT = md_path.stem == "CP2-CHEATSHEET"
    USABLE_CM = 16.6 if not COMPACT else 17.2
    text = md_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    lines = text.expandtabs(4).split("\n")

    doc = Document()
    setup_document(doc)
    title = None
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # fenced code
        fence = FENCE_RE.match(line)
        if fence:
            i += 1
            code_lines = []
            while i < n and not FENCE_RE.match(lines[i]):
                code_lines.append(lines[i])
                i += 1
            i += 1  # closing fence
            add_code_block(doc, "\n".join(dedent(code_lines)).rstrip(),
                           fence.group(2).strip())
            continue

        # heading
        head = HEAD_RE.match(line)
        if head:
            level = min(len(head.group(1)), 3)
            add_heading(doc, head.group(2).strip(), level)
            if level == 1 and title is None:
                title = head.group(2).strip()
            i += 1
            continue

        # horizontal rule
        if re.match(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", line):
            add_hr(doc)
            i += 1
            continue

        # blockquote
        if stripped.startswith(">"):
            quote_lines = []
            while i < n and lines[i].strip().startswith(">"):
                quote_lines.append(re.sub(r"^\s*>\s?", "", lines[i]).strip())
                i += 1
            add_quote(doc, [q for q in quote_lines if q] or [""])
            continue

        # table
        if is_table_start(lines, i):
            rows, aligns, i = parse_table(lines, i)
            add_table(doc, rows, aligns)
            continue

        # list item
        om = OL_RE.match(line)
        um = None if om else UL_RE.match(line)
        if om or um:
            if om:
                indent = len(om.group(1))
                number = om.group(2)
                item_text = om.group(3)
                ordered = True
            else:
                indent = len(um.group(1))
                number = None
                item_text = um.group(2)
                ordered = False
            level = min(indent // 2, 3)
            # continuation lines (deeper indent, plain text)
            i += 1
            while i < n:
                nxt = lines[i]
                if not nxt.strip():
                    break
                if (
                    FENCE_RE.match(nxt)
                    or HEAD_RE.match(nxt)
                    or OL_RE.match(nxt)
                    or UL_RE.match(nxt)
                    or nxt.strip().startswith((">", "|"))
                    or re.match(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", nxt)
                ):
                    break
                if len(nxt) - len(nxt.lstrip()) > indent:
                    item_text += " " + nxt.strip()
                    i += 1
                    continue
                break
            add_list_item(doc, item_text, ordered, number, level)
            continue

        # paragraph: gather until blank or block start
        para = [stripped]
        i += 1
        while i < n:
            nxt = lines[i]
            if not nxt.strip():
                break
            if (
                FENCE_RE.match(nxt)
                or HEAD_RE.match(nxt)
                or OL_RE.match(nxt)
                or UL_RE.match(nxt)
                or nxt.strip().startswith((">", "|"))
                or re.match(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", nxt)
            ):
                break
            para.append(nxt.strip())
            i += 1
        add_body(doc, " ".join(para))

    doc.core_properties.title = title or md_path.stem
    doc.core_properties.subject = "ЧП2 — техническая сторона MVP"
    doc.core_properties.author = "Mesh-ПВЗ"
    doc.core_properties.language = "ru-RU"
    doc.save(str(docx_path))
    return docx_path


def main():
    for name in FILES:
        md = OUT_DIR / name
        out = OUT_DIR / (Path(name).stem + ".docx")
        convert(md, out)
        print(f"{out.name}: {out.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()