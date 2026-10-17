"""pidcheck DRAWING EQUIPMENT_LIST [--out DIR] [--json]

Check a P&ID (.dxf, or a vector .pdf exported from CAD) against its equipment list (.xlsx or .csv).
Exit code 1 when there are errors, so it can gate a CI job or a document-control workflow.
"""
from __future__ import annotations

import argparse
import json
import sys

from .check import ERROR, INFO, WARNING, check
from . import report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pidcheck", description=__doc__.split("\n\n")[1])
    ap.add_argument("drawing")
    ap.add_argument("equipment_list")
    ap.add_argument("--out", help="write report.html, findings.json and findings.csv here")
    ap.add_argument("--json", action="store_true", help="print findings as JSON")
    ap.add_argument("--all", action="store_true", help="also print info-level findings")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    rep = check(a.drawing, a.equipment_list)
    if a.json:
        print(json.dumps([f.to_dict() for f in rep.findings], indent=1, ensure_ascii=False))
    else:
        s = rep.stats
        print(f"{s['tags_on_drawing']} tags on the drawing, {s['tags_in_list']} in the list; "
              f"{s['values_compared']} values compared, {len(rep.agreements)} agree")
        for i, f in enumerate(rep.findings, 1):
            if f.severity == INFO and not a.all:
                continue
            print(f"#{i:<3} {f.severity.upper():7} {f.confidence:6} {f.message}")
        n_e, n_w = len(rep.by_severity(ERROR)), len(rep.by_severity(WARNING))
        print(f"\n{n_e} error(s), {n_w} warning(s)")
    if a.out:
        paths = report.write(rep, a.out)
        print(f"report: {paths['html']}")
    return 1 if rep.by_severity(ERROR) else 0


if __name__ == "__main__":
    raise SystemExit(main())
