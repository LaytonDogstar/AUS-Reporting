"""Render a small Markdown subset to PDF. Headings, paragraphs, bullet and
numbered lists, pipe tables, bold, inline code, horizontal rules."""
import re, sys
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate, Paragraph,
                                Spacer, Table, TableStyle, KeepTogether)

INK   = colors.HexColor("#1a1a1a")
MUTED = colors.HexColor("#5b6169")
RULE  = colors.HexColor("#d4d7dc")
BAND  = colors.HexColor("#f2f4f6")
ACCENT= colors.HexColor("#1f4e79")

def style(name, **kw):
    base = dict(fontName="Helvetica", fontSize=9.6, leading=13.6, textColor=INK)
    base.update(kw)
    return ParagraphStyle(name, **base)

S = {
    "h1":   style("h1", fontName="Helvetica-Bold", fontSize=19, leading=23,
                  spaceBefore=4, spaceAfter=10, textColor=ACCENT),
    "h2":   style("h2", fontName="Helvetica-Bold", fontSize=13, leading=17,
                  spaceBefore=13, spaceAfter=5, textColor=ACCENT),
    "h3":   style("h3", fontName="Helvetica-Bold", fontSize=10.6, leading=14,
                  spaceBefore=10, spaceAfter=3.5),
    "body": style("body", spaceAfter=6),
    "li":   style("li", leftIndent=12, bulletIndent=2, spaceAfter=3.5),
    "th":   style("th", fontName="Helvetica-Bold", fontSize=8.6, leading=11.4),
    "td":   style("td", fontSize=8.6, leading=11.4),
    "quote":style("quote", leftIndent=10, textColor=MUTED, spaceAfter=7,
                  borderPadding=0),
}

def inline(t):
    t = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"`(.+?)`", r'<font face="Courier" size="8.8">\1</font>', t)
    return t

def is_row(l):   return l.startswith("|") and l.endswith("|")
def cells(l):    return [c.strip() for c in l.strip("|").split("|")]
def is_sep(l):   return is_row(l) and all(set(c) <= set("-: ") and c for c in cells(l))

def build_table(rows, width):
    """Size columns by measured text width, not character count: `code`
    renders in Courier, which is wider per character than Helvetica."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    head, body = rows[0], rows[1:]
    ncol = len(head)
    PAD = 13.0
    CAP = width * 0.42          # no single column may hog the table
    MINW = 34.0

    def measure(text, bold=False):
        # Longest unbreakable run decides the minimum sensible width.
        plain = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
        parts, widest = re.split(r"(`[^`]*`)", plain), 0.0
        total = 0.0
        for part in parts:
            if not part:
                continue
            if part.startswith("`") and part.endswith("`") and len(part) > 1:
                w = stringWidth(part[1:-1], "Courier", 8.8)
            else:
                w = stringWidth(part, "Helvetica-Bold" if bold else "Helvetica", 8.6)
            total += w
            widest = max(widest, w)
        return total, widest

    weights, floors = [], []
    for i in range(ncol):
        col = [(head[i], True)] + [(r[i] if i < len(r) else "", False) for r in body]
        longest_word = 0.0
        longest_cell = 0.0
        for text, bold in col:
            total, widest = measure(text, bold)
            longest_cell = max(longest_cell, total)
            longest_word = max(longest_word, widest)
        # Want the whole cell if it is short; never less than its widest
        # single token, so identifiers are not split mid-word.
        weights.append(max(MINW, min(CAP, max(longest_cell, longest_word)) + PAD))
        # Never squeeze a column below its widest unbreakable token, or
        # identifiers split mid-word.
        floors.append(max(MINW, min(CAP, longest_word + PAD)))

    total = sum(weights)
    if total > width:
        # Shrink only the columns that have slack above their unbreakable
        # minimum, so short identifier columns keep their width.
        floors = [min(w, f) for w, f in zip(weights, floors)]
        slack = total - sum(floors)
        over = total - width
        if slack > 0:
            weights = [w - (w - f) * over / slack for w, f in zip(weights, floors)]
        else:
            weights = [w * width / total for w in weights]
    else:
        weights = [w * width / total for w in weights]

    data = [[Paragraph(inline(c), S["th"]) for c in head]]
    for r in body:
        r = (list(r) + [""] * ncol)[:ncol]
        data.append([Paragraph(inline(c), S["td"]) for c in r])
    t = Table(data, colWidths=weights, repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), BAND),
        ("LINEBELOW",  (0,0), (-1,0), 0.7, RULE),
        ("LINEBELOW",  (0,1), (-1,-2), 0.25, RULE),
        ("VALIGN",     (0,0), (-1,-1), "TOP"),
        ("TOPPADDING", (0,0), (-1,-1), 4.5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4.5),
        ("LEFTPADDING",(0,0), (-1,-1), 6),
        ("RIGHTPADDING",(0,0),(-1,-1), 6),
    ]))
    return t

def render(md, width):
    out, lines, i = [], md.split("\n"), 0
    para = []
    def flush():
        if para:
            out.append(Paragraph(inline(" ".join(para)), S["body"]))
            para.clear()
    while i < len(lines):
        l = lines[i].rstrip()
        if is_row(l) and i + 1 < len(lines) and is_sep(lines[i+1].rstrip()):
            flush()
            rows = [cells(l)]
            i += 2
            while i < len(lines) and is_row(lines[i].rstrip()):
                rows.append(cells(lines[i].rstrip())); i += 1
            out.append(Spacer(1, 3))
            out.append(build_table(rows, width))
            out.append(Spacer(1, 9))
            continue
        if not l.strip():
            flush(); i += 1; continue
        if l.startswith("### "):
            flush(); out.append(Paragraph(inline(l[4:]), S["h3"])); i += 1; continue
        if l.startswith("## "):
            flush(); out.append(Paragraph(inline(l[3:]), S["h2"])); i += 1; continue
        if l.startswith("# "):
            flush(); out.append(Paragraph(inline(l[2:]), S["h1"])); i += 1; continue
        if l.strip() in ("---", "***", "___"):
            flush(); out.append(Spacer(1, 6)); i += 1; continue
        if l.startswith("> "):
            flush(); out.append(Paragraph(inline(l[2:]), S["quote"])); i += 1; continue
        m = re.match(r"^(\s*)[-*]\s+(.*)$", l)
        if m:
            flush()
            txt = m.group(2)
            while i + 1 < len(lines) and re.match(r"^\s{2,}\S", lines[i+1]) \
                  and not re.match(r"^\s*[-*]\s", lines[i+1]) and lines[i+1].strip():
                i += 1; txt += " " + lines[i].strip()
            out.append(Paragraph(inline(txt), S["li"], bulletText="•"))
            i += 1; continue
        m = re.match(r"^(\s*)(\d+)\.\s+(.*)$", l)
        if m:
            flush()
            txt = m.group(3)
            while i + 1 < len(lines) and re.match(r"^\s{2,}\S", lines[i+1]) \
                  and not re.match(r"^\s*(\d+\.|[-*])\s", lines[i+1]) and lines[i+1].strip():
                i += 1; txt += " " + lines[i].strip()
            out.append(Paragraph(inline(txt), S["li"], bulletText=m.group(2) + "."))
            i += 1; continue
        para.append(l.strip()); i += 1
    flush()
    return out

def make(md, path, footer):
    doc = BaseDocTemplate(path, pagesize=A4,
                          leftMargin=22*mm, rightMargin=22*mm,
                          topMargin=20*mm, bottomMargin=20*mm,
                          title=footer, author="Dogstar Digital Group")
    fw = doc.width
    frame = Frame(doc.leftMargin, doc.bottomMargin, fw, doc.height, id="f")

    def deco(canv, d):
        canv.saveState()
        canv.setFont("Helvetica", 7.4)
        canv.setFillColor(MUTED)
        canv.drawString(d.leftMargin, 12*mm, footer)
        canv.drawRightString(d.leftMargin + fw, 12*mm, "Page %d" % canv.getPageNumber())
        canv.setStrokeColor(RULE); canv.setLineWidth(0.4)
        canv.line(d.leftMargin, 14.5*mm, d.leftMargin + fw, 14.5*mm)
        canv.restoreState()

    doc.addPageTemplates([PageTemplate(id="p", frames=[frame], onPage=deco)])
    doc.build(render(md, fw))

if __name__ == "__main__":
    src, dst, footer = sys.argv[1], sys.argv[2], sys.argv[3]
    make(open(src, encoding="utf-8").read(), dst, footer)
    print("wrote", dst)
