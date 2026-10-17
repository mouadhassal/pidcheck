"""Real-world check: pidcheck on the public DEXPI test cases (github.com/DEXPI/TestCases).

Each DEXPI test case has XML exports from different CAD tools and PDF drawings. Two kinds of pair are
checked, both with the XML as the reference list:

  same tool   C03V01-SAG.EX01.XML  vs  C03V01-SAG.EX01.pdf        the exporter's own drawing
  round trip  C01V01-HEX.EX02.xml  vs  C01V01-HEX.EX02-SAG.IM01.pdf  tool B imported tool A's file and drew it

On a same-tool pair the drawing and the data come from one model, so every error is either a defect in
the test case or a false alarm by pidcheck; each one is looked at by hand (see dexpi/FINDINGS.md).
The files are downloaded at run time and cached in dexpi/cache (not redistributed here).

    python dexpi/study.py
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pidcheck.check import ERROR, WARNING, check  # noqa: E402

API = "https://api.github.com/repos/DEXPI/TestCases/git/trees/HEAD?recursive=1"
RAW = "https://raw.githubusercontent.com/DEXPI/TestCases/HEAD/"
CACHE = ROOT / "dexpi" / "cache"


def fetch(url: str, dest: Path) -> Path:
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=60) as r:
            dest.write_bytes(r.read())
    return dest


def pairs() -> list[dict]:
    tree = json.loads(fetch(API, CACHE / "tree.json").read_text(encoding="utf-8"))["tree"]
    files = [t["path"] for t in tree if t["type"] == "blob"]
    by_lower = {p.lower(): p for p in files}
    out = []
    for p in files:
        if not p.lower().endswith(".pdf"):
            continue
        stem = p[:-4]
        m = re.match(r"(.+\.EX\d+)-([A-Z]{3})\.IM\d+$", stem, re.I)
        if m and (m.group(1) + ".xml").lower() in by_lower:
            out.append({"kind": "round trip", "pdf": p, "xml": by_lower[(m.group(1) + ".xml").lower()],
                        "importer": m.group(2)})
        elif (stem + ".xml").lower() in by_lower:
            out.append({"kind": "same tool", "pdf": p, "xml": by_lower[(stem + ".xml").lower()]})
    return sorted(out, key=lambda d: (d["kind"], d["pdf"]))


def main():
    rows = []
    for pr in pairs():
        loc = {}
        for k in ("pdf", "xml"):
            loc[k] = fetch(RAW + urllib.parse.quote(pr[k]), CACHE / pr[k].replace("/", "__"))
        try:
            rep = check(loc["pdf"], loc["xml"])
        except Exception as e:  # scanned page, empty list, unreadable file: report, don't hide
            rows.append({**pr, "error": f"{type(e).__name__}: {e}"})
            continue
        errs = [f for f in rep.findings if f.severity in (ERROR, WARNING)]
        rows.append({**pr, "tags_in_xml": rep.stats["tags_in_list"], "tags_on_pdf": rep.stats["tags_on_drawing"],
                     "values_compared": rep.stats["values_compared"], "agreements": len(rep.agreements),
                     "findings": [{"kind": f.kind, "severity": f.severity, "tag": f.tag, "message": f.message}
                                  for f in errs]})
        print(f"{pr['kind']:10} {Path(pr['pdf']).name:40} tags xml/pdf {rows[-1]['tags_in_xml']:>3}/"
              f"{rows[-1]['tags_on_pdf']:<3} values {rows[-1]['values_compared']:>3} agree {len(rep.agreements):>3}  "
              f"errors/warnings {len(errs)}")
        for f in errs:
            print(f"           {f.severity:7} {f.message[:120]}")
    out = ROOT / "results" / "dexpi.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
