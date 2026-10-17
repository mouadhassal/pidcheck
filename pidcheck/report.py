"""Write a report: a self-contained HTML page with the annotated drawing, plus JSON and CSV."""
from __future__ import annotations

import base64
import csv
import html
import json
import tempfile
from pathlib import Path

from .check import ERROR, INFO, WARNING, Report

COLOURS = {ERROR: (0.84, 0.11, 0.11), WARNING: (0.93, 0.55, 0.0), INFO: (0.25, 0.45, 0.85)}
HEX = {ERROR: "#d61c1c", WARNING: "#ec8c00", INFO: "#4073d9"}


def annotated_png(rep: Report, out_png: Path, dpi: int = 130) -> Path | None:
    """The drawing with every error and warning circled and numbered."""
    marks = [(i, f) for i, f in enumerate(rep.findings, 1) if f.x is not None and f.severity != INFO]
    src = Path(rep.drawing)
    if src.suffix.lower() == ".dxf":
        from .render import Mark, render
        render(src, png_path=out_png, dpi=dpi,
               marks=[Mark(f.x, f.y, COLOURS[f.severity], f"#{i}") for i, f in marks])
        return out_png
    if src.suffix.lower() == ".pdf":
        import fitz
        with fitz.open(src) as pdf:
            page = pdf[0]
            ph = page.rect.height
            for i, f in marks:
                if f.page != 0:
                    continue
                c = (f.x, ph - f.y)
                page.draw_circle(c, 9, color=COLOURS[f.severity], width=1.8)
                page.insert_text((c[0] + 11, c[1] - 9), f"#{i}", fontsize=6.5, color=COLOURS[f.severity])
            page.get_pixmap(dpi=dpi).save(out_png)
        return out_png
    return None


def write(rep: Report, out_dir: str | Path) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    data = {"drawing": rep.drawing, "equipment_list": rep.equipment_list, "stats": rep.stats,
            "findings": [f.to_dict() for f in rep.findings], "agreements": rep.agreements}
    (out / "findings.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    with open(out / "findings.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["#", "severity", "confidence", "kind", "tag", "field", "drawing", "list", "message"])
        for i, f in enumerate(rep.findings, 1):
            w.writerow([i, f.severity, f.confidence, f.kind, f.tag, f.field, f.drawing, f.listed, f.message])
    with tempfile.TemporaryDirectory() as td:
        png = annotated_png(rep, Path(td) / "drawing.png")
        img = base64.b64encode(png.read_bytes()).decode() if png else ""
    (out / "report.html").write_text(_html(rep, img), encoding="utf-8")
    return {"html": out / "report.html", "json": out / "findings.json", "csv": out / "findings.csv"}


def _html(rep: Report, img_b64: str) -> str:
    e = html.escape
    n = {s: len(rep.by_severity(s)) for s in (ERROR, WARNING, INFO)}
    st = rep.stats
    across = sum(1 for a in rep.agreements if a.get("listed") and a["listed"].split()[-1:] != a["drawing"].split()[-1:])
    rows = []
    for i, f in enumerate(rep.findings, 1):
        ev = "<br>".join(e(x) for x in f.evidence)
        rows.append(f"<tr class='{f.severity}'><td>{i}</td><td><span class='pill {f.severity}'>{f.severity}</span></td>"
                    f"<td>{f.confidence}</td><td class='mono'>{e(f.tag)}</td><td>{e(f.message)}"
                    f"{'<div class=ev>' + ev + '</div>' if ev else ''}</td></tr>")
    agree = "".join(f"<tr><td class='mono'>{e(a['tag'])}</td><td>{e(a['field'])}</td><td>{e(a['drawing'])}</td>"
                    f"<td>{e(a.get('listed', ''))}</td><td>{e(a.get('note', ''))}</td></tr>"
                    for a in rep.agreements[:400])
    img = f"<img src='data:image/png;base64,{img_b64}' alt='annotated drawing'>" if img_b64 else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>pidcheck report</title>
<style>
:root{{--bg:#fff;--fg:#1b1d21;--mut:#5d6470;--line:#e3e6ea;--card:#f6f7f9}}
@media (prefers-color-scheme:dark){{:root{{--bg:#14161a;--fg:#e8eaee;--mut:#9aa2ae;--line:#2a2e35;--card:#1c1f24}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:1180px;margin:0 auto;padding:28px 16px 60px}}
h1{{font-size:24px;margin:0 0 4px}} h2{{font-size:18px;margin:32px 0 10px}}
.mut{{color:var(--mut)}} .mono{{font-family:ui-monospace,Consolas,monospace;white-space:nowrap}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:18px 0}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}}
.card b{{display:block;font-size:24px}}
img{{width:100%;border:1px solid var(--line);border-radius:8px;background:#fff}}
table{{width:100%;border-collapse:collapse;font-size:14px}} td,th{{padding:7px 8px;border-bottom:1px solid var(--line);
text-align:left;vertical-align:top}} th{{font-weight:600;color:var(--mut)}}
.pill{{padding:1px 8px;border-radius:99px;color:#fff;font-size:12px}} .pill.error{{background:{HEX[ERROR]}}}
.pill.warning{{background:{HEX[WARNING]}}} .pill.info{{background:{HEX[INFO]}}}
.ev{{color:var(--mut);font-size:12.5px;margin-top:3px}} details{{margin-top:8px}} .wrap{{overflow-x:auto}}
</style></head><body><main>
<h1>pidcheck report</h1>
<div class="mut">Drawing <span class="mono">{e(Path(rep.drawing).name)}</span> against
<span class="mono">{e(Path(rep.equipment_list).name)}</span></div>
<div class="cards">
<div class="card"><b style="color:{HEX[ERROR]}">{n[ERROR]}</b>errors</div>
<div class="card"><b style="color:{HEX[WARNING]}">{n[WARNING]}</b>warnings (need a human look)</div>
<div class="card"><b>{st.get('values_compared', 0)}</b>values compared</div>
<div class="card"><b>{len(rep.agreements)}</b>agreements, {across} across units</div>
<div class="card"><b>{st.get('tags_on_drawing', 0)} / {st.get('tags_in_list', 0)}</b>tags on drawing / in list</div>
</div>
{img}
<h2>Findings</h2>
<div class="wrap"><table><tr><th>#</th><th>Severity</th><th>Confidence</th><th>Tag</th><th>What and why</th></tr>
{''.join(rows) or '<tr><td colspan=5>No findings.</td></tr>'}</table></div>
<details><summary><b>Values that agree ({len(rep.agreements)})</b>: including conversions such as GPM to m³/h,
NPS to DN and Class to PN</summary><div class="wrap"><table><tr><th>Tag</th><th>Field</th><th>Drawing</th><th>List</th>
<th>Check</th></tr>{agree}</table></div></details>
<p class="mut" style="margin-top:28px">Links: {e(json.dumps(st.get('links', {})))}. No language model makes any
decision in this report: text is read from the file, linked to tags by CAD structure and geometry, and compared
by unit- and rounding-aware code.</p>
</main></body></html>"""
