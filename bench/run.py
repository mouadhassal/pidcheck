"""Score pidcheck against the planted faults (rules frozen in PREREGISTRATION.md).

    python bench/run.py data/dev  --out results/dev.json
    python bench/run.py data/test --out results/test.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pidcheck.check import ERROR, HIGH, WARNING, check  # noqa: E402

FIELD = {"value_changed", "unit_slip", "size_changed", "rating_changed"}


def caught(fault: dict, findings: list) -> tuple[bool, bool, list[int]]:
    """(caught exactly, caught for review, indices of findings this fault explains)."""
    t, tag = fault["type"], fault["tag"]
    hits, review = [], []
    for i, f in enumerate(findings):
        names = {f.tag, *f.related}
        if t in FIELD:
            if f.kind == "SPEC_MISMATCH" and f.tag == tag and f.field == fault["field"]:
                hits.append(i)
            elif f.kind == "AMBIGUOUS_LABEL" and tag in names:
                review.append(i)
        elif t == "missing":
            if f.kind == "MISSING_ON_DRAWING" and f.tag == tag:
                hits.append(i)
            elif f.kind == "POSSIBLE_TYPO" and tag in (f.listed or "").split(", "):
                review.append(i)
        elif t == "extra":
            if f.kind == "NOT_IN_LIST" and f.tag == tag:
                hits.append(i)
            elif f.kind == "POSSIBLE_TYPO" and f.drawing == tag:
                review.append(i)
        elif t == "duplicate":
            if f.kind == "DUPLICATE_ON_DRAWING" and f.tag == tag:
                hits.append(i)
        elif t == "confusable":
            if f.kind == "TAG_TYPO" and f.tag == tag:
                hits.append(i)
        elif t == "transposed":
            typo = fault["drawing"]
            if f.kind == "POSSIBLE_TYPO" and f.drawing == typo and tag in (f.listed or "").split(", "):
                hits.append(i)
            elif (f.kind == "NOT_IN_LIST" and f.tag == typo) or (f.kind == "MISSING_ON_DRAWING" and f.tag == tag) \
                    or (f.kind == "POSSIBLE_TYPO" and f.drawing == typo):
                review.append(i)
        elif t == "list_duplicate":
            if f.kind == "DUPLICATE_IN_LIST" and f.tag == tag:
                hits.append(i)
    if t == "transposed" and not hits:
        kinds = {findings[i].kind for i in review}
        if {"NOT_IN_LIST", "MISSING_ON_DRAWING"} <= kinds:   # both halves surfaced as hard errors
            return True, True, review
    return bool(hits), bool(hits or review), hits + review


def score(folder: Path) -> dict:
    per_type = defaultdict(Counter)
    false_alarms: list[dict] = []
    high_total = high_ok = 0
    agree = conv = 0
    n_faults = n_draw = 0
    secs = 0.0
    by_drawing = []
    fmt_results = {}
    for fmt in ("dxf", "pdf"):
        per_type = defaultdict(Counter)
        false_alarms = []
        high_total = high_ok = agree = conv = n_faults = n_draw = 0
        secs = 0.0
        for truth_path in sorted(folder.glob("*.truth.json")):
            truth = json.loads(truth_path.read_text(encoding="utf-8"))
            name = truth["name"]
            t0 = time.perf_counter()
            rep = check(folder / f"{name}.{fmt}", folder / f"{name}_equipment.xlsx")
            secs += time.perf_counter() - t0
            n_draw += 1
            findings = [f for f in rep.findings if f.severity in (ERROR, WARNING)]
            explained: set[int] = set()
            for fault in truth["faults"]:
                exact, review, idx = caught(fault, findings)
                per_type[fault["type"]]["planted"] += 1
                per_type[fault["type"]]["caught"] += exact
                per_type[fault["type"]]["caught_or_review"] += review
                explained |= set(idx)
                n_faults += 1
            # a finding about a tag involved in a planted fault is explained by it
            fault_tags = {f["tag"] for f in truth["faults"]} | {f.get("drawing") for f in truth["faults"]
                                                                if f["type"] in ("transposed", "confusable")}
            for i, f in enumerate(findings):
                involved = {f.tag, *f.related, *(f.listed or "").split(", "), f.drawing}
                if i in explained or (f.kind in ("POSSIBLE_TYPO", "AMBIGUOUS_LABEL") and involved & fault_tags):
                    explained.add(i)
            for i, f in enumerate(findings):
                if f.severity == ERROR and f.confidence == HIGH:
                    high_total += 1
                    high_ok += i in explained
                if i not in explained:
                    false_alarms.append({"drawing": name, "kind": f.kind, "severity": f.severity,
                                         "confidence": f.confidence, "message": f.message})
            agree += len(rep.agreements)
            conv += sum(1 for a in rep.agreements if a.get("listed") and a["listed"].split()[-1:] !=
                        a["drawing"].split()[-1:])
            if fmt == "dxf":
                by_drawing.append({"name": name, "faults": len(truth["faults"]),
                                   "errors": sum(f.severity == ERROR for f in findings),
                                   "warnings": sum(f.severity == WARNING for f in findings)})
        planted = sum(c["planted"] for c in per_type.values())
        fmt_results[fmt] = {
            "drawings": n_draw, "planted_faults": planted,
            "caught": sum(c["caught"] for c in per_type.values()),
            "caught_or_review": sum(c["caught_or_review"] for c in per_type.values()),
            "recall": sum(c["caught"] for c in per_type.values()) / max(planted, 1),
            "recall_incl_review": sum(c["caught_or_review"] for c in per_type.values()) / max(planted, 1),
            "false_alarms": len(false_alarms),
            "false_alarms_per_drawing": len(false_alarms) / max(n_draw, 1),
            "false_alarm_errors": sum(f["severity"] == ERROR for f in false_alarms),
            "high_confidence_errors": high_total,
            "high_confidence_precision": high_ok / max(high_total, 1),
            "agreements": agree, "agreements_across_units": conv,
            "seconds_per_drawing": secs / max(n_draw, 1),
            "per_type": {k: dict(v) for k, v in sorted(per_type.items())},
            "false_alarm_list": false_alarms,
        }
    return {"folder": str(folder), "formats": fmt_results, "by_drawing": by_drawing}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--out")
    a = ap.parse_args()
    res = score(Path(a.folder))
    for fmt, r in res["formats"].items():
        print(f"[{fmt}] {r['drawings']} drawings, {r['planted_faults']} faults: caught {r['caught']} "
              f"({r['recall']:.1%}), incl. review {r['recall_incl_review']:.1%}; false alarms {r['false_alarms']} "
              f"({r['false_alarm_errors']} errors); high-confidence precision {r['high_confidence_precision']:.1%} "
              f"of {r['high_confidence_errors']}; agreements {r['agreements']} ({r['agreements_across_units']} "
              f"across units); {r['seconds_per_drawing'] * 1000:.0f} ms/drawing")
        for k, v in r["per_type"].items():
            print(f"    {k:16} {v.get('caught', 0)}/{v['planted']}  (with review {v.get('caught_or_review', 0)})")
        for fa in r["false_alarm_list"][:15]:
            print("    FA", fa["drawing"], fa["kind"], fa["confidence"], fa["message"][:130])
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
