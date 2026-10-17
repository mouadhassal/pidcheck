"""Compare a drawing with its equipment list and explain every difference.

No model makes a decision here. Text is read and linked by geometry and CAD structure, numbers are
compared by unit-aware, rounding-aware code, and every finding carries the evidence and a confidence
level that says how much of the chain was certain.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from dataclasses import field as dfield
from pathlib import Path

from . import associate, equiplist, extract
from .tags import likely_same, written_differently
from .units import Quantity, compare

ERROR, WARNING, INFO = "error", "warning", "info"
HIGH, MEDIUM, LOW = "high", "medium", "low"


@dataclass
class Finding:
    kind: str
    severity: str
    confidence: str
    tag: str
    message: str
    field: str = ""
    drawing: str = ""
    listed: str = ""
    evidence: list[str] = dfield(default_factory=list)
    x: float | None = None
    y: float | None = None
    page: int = 0
    related: list[str] = dfield(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Report:
    drawing: str
    equipment_list: str
    findings: list[Finding] = dfield(default_factory=list)
    agreements: list[dict] = dfield(default_factory=list)
    stats: dict = dfield(default_factory=dict)

    def by_severity(self, sev: str) -> list[Finding]:
        return [f for f in self.findings if f.severity == sev]


def _lower(conf: str) -> str:
    return {HIGH: MEDIUM, MEDIUM: LOW, LOW: LOW}[conf]


def _link_confidence(s: associate.SpecLink) -> tuple[str, str]:
    if s.link in ("attribute", "inline"):
        return HIGH, f"{s.link} link"
    if s.link == "resolved-by-value":
        return MEDIUM, "label was between two tags; linked to the one whose list value it matches"
    if s.distance <= 4.0 and s.margin >= 2.5:
        return HIGH, f"label {s.distance:.1f} text heights from the tag, next tag {s.margin:.1f}x farther"
    return MEDIUM, f"label {s.distance:.1f} text heights from the tag, next tag only {s.margin:.1f}x farther"


def check(drawing_path: str | Path, list_path: str | Path) -> Report:
    frags = extract.read(drawing_path)
    dwg = associate.build(frags)
    lst = equiplist.read(list_path)
    rep = Report(str(drawing_path), str(list_path))
    F = rep.findings.append

    on_dwg = dwg.tags()
    listed = lst.items

    # --- tags written in a non-standard way, or with a confusable character -------------------
    for tag, occ in on_dwg.items():
        for o in occ:
            if o.tag.typo_fixed:
                F(Finding("TAG_TYPO", WARNING, HIGH if tag in listed else MEDIUM, tag,
                          f"'{o.tag.written}' uses a letter where a digit belongs; read as {tag}",
                          drawing=o.tag.written, x=o.x, y=o.y, page=o.fragment.page,
                          evidence=["letters O/I/l inside the loop number"]))
            elif written_differently(o.tag):
                F(Finding("TAG_FORMAT", INFO, HIGH, tag, f"written '{o.tag.written}', list uses {tag}",
                          drawing=o.tag.written, x=o.x, y=o.y, page=o.fragment.page))

    # --- the same tag drawn more than once -----------------------------------------------------
    for tag, occ in on_dwg.items():
        if len(occ) > 1:
            F(Finding("DUPLICATE_ON_DRAWING", ERROR, MEDIUM, tag, f"{tag} appears {len(occ)} times on the drawing",
                      x=occ[1].x, y=occ[1].y, page=occ[1].fragment.page,
                      evidence=[f"at ({o.x:.0f}, {o.y:.0f})" for o in occ]))
    for tag, r1, r2 in lst.duplicates:
        F(Finding("DUPLICATE_IN_LIST", ERROR, HIGH, tag, f"{tag} is listed twice (rows {r1} and {r2})"))

    # --- tags on one side only, with likely typos paired up ------------------------------------
    only_dwg = sorted(set(on_dwg) - set(listed))
    only_list = sorted(set(listed) - set(on_dwg))
    paired_d, paired_l = set(), set()
    for d in only_dwg:
        matches = [l_ for l_ in only_list if l_ not in paired_l and likely_same(d, l_)]
        if len(matches) == 1:
            l_ = matches[0]
            paired_d.add(d)
            paired_l.add(l_)
            o = on_dwg[d][0]
            F(Finding("POSSIBLE_TYPO", WARNING, MEDIUM, l_, f"drawing shows {d}, which is not in the list; the "
                      f"list has {l_}, which is not on the drawing. Probably the same item mistyped",
                      drawing=d, listed=l_, x=o.x, y=o.y, page=o.fragment.page, related=[d]))
        elif len(matches) > 1:
            # several list tags are one keystroke away: say so, but keep the hard errors too
            o = on_dwg[d][0]
            F(Finding("POSSIBLE_TYPO", WARNING, LOW, matches[0], f"drawing shows {d}, which is not in the list; it "
                      f"could be a mistyped {' or '.join(matches)}, all missing from the drawing",
                      drawing=d, listed=", ".join(matches), x=o.x, y=o.y, page=o.fragment.page,
                      related=[d] + matches[1:]))
    for d in only_dwg:
        if d not in paired_d:
            o = on_dwg[d][0]
            F(Finding("NOT_IN_LIST", ERROR, HIGH, d, f"{d} is on the drawing but not in the equipment list",
                      drawing=d, x=o.x, y=o.y, page=o.fragment.page))
    for l_ in only_list:
        if l_ not in paired_l:
            F(Finding("MISSING_ON_DRAWING", ERROR, HIGH, l_, f"{l_} ({listed[l_].description or 'no description'})"
                      f" is in the equipment list but not on the drawing", listed=l_))

    # --- labels between two tags: resolve only when the values decide it ------------------------
    for amb in dwg.ambiguous:
        cands = [t for t, _ in amb.candidates]
        fits = []
        for t in cands:
            item = listed.get(t)
            if item and all(any(compare(q, lq).equal for lq in item.specs.get(q.kind, [])) for q in amb.quantities):
                fits.append(t)
        if len(fits) >= 1:
            t = fits[0]
            for q in amb.quantities:
                dwg.specs.setdefault(t, []).append(associate.SpecLink(q, amb.fragment, "resolved-by-value"))
            rep.agreements.append({"tag": t, "field": ",".join(q.kind for q in amb.quantities),
                                   "drawing": amb.fragment.text, "note": f"label between {', '.join(cands)}; "
                                   f"its value matches {t}"})
        else:
            listed_vals = "; ".join(
                f"{t}: " + (", ".join(lq.text for q in amb.quantities for lq in listed[t].specs.get(q.kind, []))
                            or "no value") for t in cands if t in listed)
            F(Finding("AMBIGUOUS_LABEL", WARNING, LOW, cands[0],
                      f"'{amb.fragment.text}' sits between {' and '.join(cands)} and matches neither "
                      f"list value ({listed_vals}). Could be a wrong value on either; needs a human look",
                      drawing=amb.fragment.text, x=amb.fragment.x, y=amb.fragment.y, page=amb.fragment.page,
                      related=cands[1:], evidence=[f"{t} at {dist} text heights" for t, dist in amb.candidates]))
    for frag, qs in dwg.orphans:
        F(Finding("ORPHAN_LABEL", INFO, MEDIUM, "", f"'{frag.text}' is not near any tag; not compared",
                  drawing=frag.text, x=frag.x, y=frag.y, page=frag.page))

    # --- compare specs for every tag present on both sides --------------------------------------
    n_compared = 0
    for tag in sorted(set(on_dwg) & set(listed)):
        item = listed[tag]
        links = dwg.specs.get(tag, [])
        by_kind: dict[str, list[associate.SpecLink]] = {}
        for s in links:
            by_kind.setdefault(s.quantity.kind, []).append(s)
        for kind, lqs in item.specs.items():
            shown = by_kind.get(kind, [])
            if not shown:
                F(Finding("SPEC_NOT_SHOWN", INFO, HIGH, tag, f"{tag} {kind} ({lqs[0].text}) is not shown on "
                          f"the drawing", field=kind, listed=lqs[0].text))
                continue
            for s in shown:
                n_compared += 1
                results = [(compare(s.quantity, lq), lq) for lq in lqs]
                ok = [(c, lq) for c, lq in results if c.equal]
                conf, why = _link_confidence(s)
                if ok:
                    c, lq = ok[0]
                    rep.agreements.append({"tag": tag, "field": kind, "drawing": s.quantity.text,
                                           "listed": lq.text, "note": c.detail})
                    if c.assumption:
                        F(Finding("ASSUMPTION", INFO, MEDIUM, tag, f"{tag} {kind}: {s.quantity.text} vs {lq.text} "
                                  f"agree, but {c.assumption}", field=kind, drawing=s.quantity.text,
                                  listed=lq.text, x=s.fragment.x, y=s.fragment.y, page=s.fragment.page))
                    continue
                c, lq = results[0]
                if c.assumption:
                    conf = _lower(conf)
                F(Finding("SPEC_MISMATCH", ERROR, conf, tag,
                          f"{tag} {kind}: drawing says {s.quantity.text}, list says {lq.text} ({c.detail})",
                          field=kind, drawing=s.quantity.text, listed=lq.text,
                          x=s.fragment.x, y=s.fragment.y, page=s.fragment.page,
                          evidence=[why] + ([c.assumption] if c.assumption else [])))
            # one tag showing two different values for the same field
            if len(shown) > 1:
                vals = {s.quantity.text for s in shown}
                if any(not compare(a.quantity, b.quantity).equal for a in shown for b in shown):
                    F(Finding("INCONSISTENT_ON_DRAWING", WARNING, MEDIUM, tag,
                              f"{tag} shows {len(shown)} different {kind} values: {', '.join(sorted(vals))}",
                              field=kind))

    rep.stats = {
        "text_fragments": len(frags), "tags_on_drawing": len(on_dwg), "tags_in_list": len(listed),
        "values_compared": n_compared, "agreements": len(rep.agreements),
        "ambiguous_labels": len(dwg.ambiguous), "list_problems": lst.problems,
        "links": {k: sum(1 for ls in dwg.specs.values() for s in ls if s.link == k)
                  for k in ("attribute", "inline", "proximity", "resolved-by-value")},
    }
    order = {ERROR: 0, WARNING: 1, INFO: 2}
    rep.findings.sort(key=lambda f: (order[f.severity], f.tag, f.kind))
    return rep
