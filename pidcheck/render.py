"""A small DXF -> vector PDF renderer (lines, polylines, circles, arcs, text, blocks).

Text is written as real PDF text, as CAD exporters do, so the PDF reader in extract.py can read it
back. The same renderer draws findings on top of the drawing for the HTML report.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

PAGE_W, PAGE_H = 1190.55, 841.89   # A3 landscape, points
MARGIN = 24.0


@dataclass
class Mark:
    x: float           # drawing coordinates (DXF units)
    y: float
    color: tuple       # RGB 0..1
    label: str = ""
    radius: float = 9.0  # in drawing text heights... converted below


class _Map:
    def __init__(self, xmin, ymin, xmax, ymax):
        w, h = max(xmax - xmin, 1e-9), max(ymax - ymin, 1e-9)
        self.s = min((PAGE_W - 2 * MARGIN) / w, (PAGE_H - 2 * MARGIN) / h)
        self.ox = MARGIN + ((PAGE_W - 2 * MARGIN) - w * self.s) / 2 - xmin * self.s
        self.oy = MARGIN + ((PAGE_H - 2 * MARGIN) - h * self.s) / 2 + ymax * self.s

    def __call__(self, x, y):
        return self.ox + x * self.s, self.oy - y * self.s


def _entities(doc):
    """Model-space entities with blocks exploded into virtual entities (attributes kept as text)."""
    for e in doc.modelspace():
        if e.dxftype() == "INSERT":
            for v in e.virtual_entities():
                if v.dxftype() != "ATTDEF":
                    yield v
            for a in e.attribs:
                if not a.dxf.get("invisible", 0):
                    yield a
        else:
            yield e


def _extents(ents):
    xs, ys = [], []
    for e in ents:
        t = e.dxftype()
        if t == "LINE":
            xs += [e.dxf.start.x, e.dxf.end.x]; ys += [e.dxf.start.y, e.dxf.end.y]
        elif t == "LWPOLYLINE":
            for p in e.get_points("xy"):
                xs.append(p[0]); ys.append(p[1])
        elif t in ("CIRCLE", "ARC"):
            c, r = e.dxf.center, e.dxf.radius
            xs += [c.x - r, c.x + r]; ys += [c.y - r, c.y + r]
        elif t in ("TEXT", "ATTRIB", "MTEXT"):
            xs.append(e.dxf.insert.x); ys.append(e.dxf.insert.y)
    return min(xs), min(ys), max(xs), max(ys)


def render(dxf_path: str | Path, pdf_path: str | Path | None = None, marks: list[Mark] | None = None,
           png_path: str | Path | None = None, dpi: int = 110):
    import ezdxf
    import fitz

    doc = ezdxf.readfile(dxf_path)
    ents = list(_entities(doc))
    m = _Map(*_extents(ents))
    pdf = fitz.open()
    page = pdf.new_page(width=PAGE_W, height=PAGE_H)
    shape = page.new_shape()
    black = (0, 0, 0)
    for e in ents:
        t = e.dxftype()
        if t == "LINE":
            shape.draw_line(m(e.dxf.start.x, e.dxf.start.y), m(e.dxf.end.x, e.dxf.end.y))
            shape.finish(color=black, width=0.6)
        elif t == "LWPOLYLINE":
            pts = [m(p[0], p[1]) for p in e.get_points("xy")]
            if e.closed:
                pts.append(pts[0])
            shape.draw_polyline(pts)
            shape.finish(color=black, width=0.6, closePath=False)
        elif t == "CIRCLE":
            c = m(e.dxf.center.x, e.dxf.center.y)
            shape.draw_circle(c, e.dxf.radius * m.s)
            shape.finish(color=black, width=0.6)
        elif t == "ARC":
            c, r = e.dxf.center, e.dxf.radius
            a0, a1 = math.radians(e.dxf.start_angle), math.radians(e.dxf.end_angle)
            if a1 < a0:
                a1 += 2 * math.pi
            pts = [m(c.x + r * math.cos(a0 + (a1 - a0) * i / 24), c.y + r * math.sin(a0 + (a1 - a0) * i / 24))
                   for i in range(25)]
            shape.draw_polyline(pts)
            shape.finish(color=black, width=0.6, closePath=False)
    shape.commit()
    for e in ents:
        t = e.dxftype()
        if t in ("TEXT", "ATTRIB"):
            _text(page, m, e.dxf.text, e.dxf.insert, e.dxf.align_point if e.dxf.hasattr("align_point") else None,
                  e.dxf.height, e.dxf.get("halign", 0), e.dxf.get("valign", 0), e.dxf.get("rotation", 0.0))
        elif t == "MTEXT":
            try:
                lines = e.plain_text(split=True)
            except TypeError:
                lines = e.plain_text().split("\n")
            h = e.dxf.char_height
            attach = e.dxf.get("attachment_point", 1)
            step = h * 1.66 * (e.dxf.get("line_spacing_factor", 1.0) or 1.0)
            shift = {0: 0.0, 1: (len(lines) - 1) / 2, 2: (len(lines) - 1)}[(attach - 1) // 3] * step
            ins = e.dxf.insert
            col = (attach - 1) % 3
            valign = {0: 3, 1: 2, 2: 1}[(attach - 1) // 3]
            for i, ln in enumerate(lines):
                y = ins.y - i * step + shift
                _text(page, m, ln, type(ins)(ins.x, y, 0), type(ins)(ins.x, y, 0), h, col, valign, 0.0)
    for mk in marks or []:
        cx, cy = m(mk.x, mk.y)
        r = mk.radius
        page.draw_circle((cx, cy), r, color=mk.color, width=1.8)
        if mk.label:
            page.insert_text((cx + r + 2, cy - r), mk.label, fontsize=6.5, color=mk.color, fontname="helv")
    if pdf_path:
        pdf.save(pdf_path)
    if png_path:
        page.get_pixmap(dpi=dpi).save(png_path)
    pdf.close()
    return m


def _text(page, m, text, ins, align, h, halign, valign, rot):
    if not text:
        return
    size = max(h * m.s, 1.0)
    w = 0.6 * size * len(text)      # helvetica-ish width estimate, same rule as extract.CHAR_W
    p = align if (halign or valign) and align is not None else ins
    x, y = m(p.x, p.y)
    dx = {0: 0.0, 1: -w / 2, 2: -w, 4: -w / 2}.get(halign, 0.0)
    dy = {0: 0.0, 1: 0.0, 2: size * 0.35, 3: size * 0.75}.get(valign, 0.0)
    if halign == 4:
        dy = size * 0.35
    r = int(round(rot / 90.0)) % 4 * 90
    if r == 0:
        page.insert_text((x + dx, y + dy), text, fontsize=size, fontname="helv")
    else:
        # rotate the offset with the text; PyMuPDF rotates about the insertion point
        a = math.radians(r)
        ox, oy = dx * math.cos(a) + dy * math.sin(a), -dx * math.sin(a) + dy * math.cos(a)
        page.insert_text((x + ox, y + oy), text, fontsize=size, fontname="helv", rotate=r)
