"""Read every piece of text from a drawing, with its position.

DXF: TEXT, MTEXT (formatting codes stripped, one fragment per line) and block attributes (ATTRIB),
including the INSERT they belong to. Block attributes are how CAD P&ID tools (AutoCAD Plant 3D,
SmartPlant) usually store a tag and its data, so they give an exact tag-to-spec link.

PDF: vector PDFs exported from CAD keep their text, so each text line is read with its position.
There are no attributes in a PDF, so specs can only be linked to tags by position. Scanned PDFs
(images) have no text and are reported as such.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path


CHAR_W = 0.6  # average glyph width / text height, used to estimate where a label's centre is


def _centre(x, y, h, text, halign=0, valign=0, rot=0.0):
    """Estimated centre of a CAD text from its anchor (CAD stores the anchor, not the box)."""
    w = CHAR_W * h * len(text)
    ox = {0: w / 2, 1: 0.0, 2: -w / 2, 3: 0.0, 4: 0.0, 5: 0.0}.get(halign, w / 2)
    oy = {0: h / 2, 1: h / 2, 2: 0.0, 3: -h / 2}.get(valign, h / 2)
    if halign in (3, 5):          # aligned/fit: anchor is the start point, box spans to align_point
        ox = w / 2
    if halign == 4:               # "middle": anchor is the box centre
        oy = 0.0
    r = math.radians(rot)
    return x + ox * math.cos(r) - oy * math.sin(r), y + ox * math.sin(r) + oy * math.cos(r)


def _mtext_centre(x, y, h, text, attach, rot):
    w = CHAR_W * h * len(text)
    col, row = (attach - 1) % 3, (attach - 1) // 3     # 1..9: top-left .. bottom-right
    ox = (0.5 - col / 2) * w
    oy = (-0.5 + row / 2) * h
    r = math.radians(rot)
    return x + ox * math.cos(r) - oy * math.sin(r), y + ox * math.sin(r) + oy * math.cos(r)


@dataclass
class Fragment:
    text: str
    x: float                    # estimated centre of the text
    y: float
    height: float
    source: str                 # TEXT, MTEXT, ATTRIB, PDF
    group: str = ""             # INSERT handle for block attributes: fragments sharing it belong together
    field_name: str = ""        # ATTRIB tag name, e.g. FLOW or TAG
    rotation: float = 0.0
    page: int = 0
    extra: dict = field(default_factory=dict)


def read(path: str | Path) -> list[Fragment]:
    path = Path(path)
    if path.suffix.lower() == ".dxf":
        return read_dxf(path)
    if path.suffix.lower() == ".pdf":
        return read_pdf(path)
    raise ValueError(f"unsupported drawing format: {path.suffix} (use .dxf or a vector .pdf)")


def _mtext_lines(e) -> list[str]:
    try:
        text = e.plain_text(split=False)
    except TypeError:
        text = e.plain_text()
    return [ln.strip() for ln in text.replace("\r", "").split("\n") if ln.strip()]


def read_dxf(path: Path) -> list[Fragment]:
    import ezdxf
    from ezdxf import recover

    try:
        doc = ezdxf.readfile(path)
    except ezdxf.DXFStructureError:
        doc, _ = recover.readfile(path)
    out: list[Fragment] = []
    for e in doc.modelspace():
        t = e.dxftype()
        if t == "TEXT":
            ha, va, rot = e.dxf.get("halign", 0), e.dxf.get("valign", 0), e.dxf.get("rotation", 0.0)
            p = e.dxf.align_point if (ha or va) and e.dxf.hasattr("align_point") else e.dxf.insert
            txt = e.dxf.text.strip()
            cx, cy = _centre(p.x, p.y, e.dxf.height, txt, ha, va, rot)
            out.append(Fragment(txt, cx, cy, e.dxf.height, "TEXT", rotation=rot))
        elif t == "MTEXT":
            p, h = e.dxf.insert, e.dxf.char_height
            rot = math.radians(e.get_rotation()) if hasattr(e, "get_rotation") else 0.0
            attach = e.dxf.get("attachment_point", 1)
            lines = _mtext_lines(e)
            step = h * 1.66 * (e.dxf.get("line_spacing_factor", 1.0) or 1.0)
            # shift the whole paragraph so the attachment point refers to the block, not line 1
            block_shift = {0: 0.0, 1: (len(lines) - 1) / 2, 2: (len(lines) - 1)}[(attach - 1) // 3] * step
            for i, line in enumerate(lines):
                dy = i * step - block_shift
                lx, ly = p.x + dy * math.sin(rot), p.y - dy * math.cos(rot)
                cx, cy = _mtext_centre(lx, ly, h, line, attach, math.degrees(rot))
                out.append(Fragment(line, cx, cy, h, "MTEXT", rotation=math.degrees(rot)))
        elif t == "INSERT":
            handle = e.dxf.handle
            for a in e.attribs:
                if a.dxf.get("invisible", 0) or not a.dxf.text.strip():
                    continue
                ha, va, rot = a.dxf.get("halign", 0), a.dxf.get("valign", 0), a.dxf.get("rotation", 0.0)
                p = a.dxf.align_point if (ha or va) and a.dxf.hasattr("align_point") else a.dxf.insert
                txt = a.dxf.text.strip()
                cx, cy = _centre(p.x, p.y, a.dxf.height, txt, ha, va, rot)
                out.append(Fragment(txt, cx, cy, a.dxf.height, "ATTRIB", group=handle,
                                    field_name=a.dxf.tag.upper(), rotation=rot))
    return out


def read_pdf(path: Path) -> list[Fragment]:
    import fitz  # PyMuPDF

    out: list[Fragment] = []
    with fitz.open(path) as pdf:
        for pno, page in enumerate(pdf):
            ph = page.rect.height
            d = page.get_text("dict")
            for block in d["blocks"]:
                for line in block.get("lines", []):
                    text = " ".join(s["text"] for s in line["spans"]).strip()
                    if not text:
                        continue
                    x0, y0, x1, y1 = line["bbox"]
                    size = max(s["size"] for s in line["spans"])
                    dx, dy = line.get("dir", (1, 0))
                    # PDF y grows downwards; flip so "above" means the same as in CAD
                    out.append(Fragment(text, (x0 + x1) / 2, ph - (y0 + y1) / 2, size, "PDF", page=pno,
                                        rotation=math.degrees(math.atan2(-dy, dx))))
    if not out:
        raise ValueError(f"{path.name}: no text layer found. This looks like a scanned drawing, "
                         "which needs OCR first; pidcheck reads vector PDFs exported from CAD.")
    return out
