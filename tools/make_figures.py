"""Figures for the README: the annotated example drawing (docs/example.png) and a zoomed crop (docs/zoom.png).

    python tools/make_figures.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import fitz  # noqa: E402

from pidcheck.check import INFO, check  # noqa: E402
from pidcheck.render import Mark, render  # noqa: E402
from pidcheck.report import COLOURS  # noqa: E402

rep = check(ROOT / "examples/example.dxf", ROOT / "examples/example_equipment.xlsx")
marks = [Mark(f.x, f.y, COLOURS[f.severity], f"#{i}", len(f.drawing or f.tag) or 6)
         for i, f in enumerate(rep.findings, 1) if f.x is not None and f.severity != INFO]
pdf = ROOT / "docs" / "_example_annotated.pdf"
pdf.parent.mkdir(exist_ok=True)
render(ROOT / "examples/example.dxf", pdf, marks=marks)
with fitz.open(pdf) as d:
    page = d[0]
    words = page.get_text("words")
    # crop to the process area: everything left of the title block
    title_x = min(w[0] for w in words if w[4] in ("DWG", "REV", "NOTE"))
    body = [w for w in words if w[2] < title_x - 10]
    clip = fitz.Rect(max(min(w[0] for w in body) - 30, 0), max(min(w[1] for w in body) - 40, 0),
                     min(max(w[2] for w in body) + 40, title_x - 5), max(w[3] for w in body) + 25)
    page.get_pixmap(dpi=170, clip=clip).save(ROOT / "docs" / "example.png")
    zoom = fitz.Rect(clip.x0, clip.y0, clip.x0 + clip.width * 0.48, clip.y0 + clip.height * 0.55)
    page.get_pixmap(dpi=260, clip=zoom).save(ROOT / "docs" / "zoom.png")
pdf.unlink()
print("docs/example.png, docs/zoom.png")
