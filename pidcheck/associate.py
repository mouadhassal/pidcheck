"""Turn loose drawing text into items: which tag is where, and which specs belong to it.

Links, from most to least certain:
  attribute  - the spec is an attribute of the same block as the tag (CAD tools store data this way)
  inline     - the spec is written in the same text as the tag ("CV-102 3\" 150#")
  proximity  - the spec is a separate label; it belongs to the nearest tag, but only when that tag is
               clearly nearer than the next one. When two tags are about equally close the label is
               marked ambiguous instead of guessed: a wrong confident link is worse than an open question.

v2 (amendment, see PREREGISTRATION.md): each proximity link also keeps the other tags within 2.5x the
nearest distance, so check.py can move a label whose nearest tag cannot carry that field (a pipe size
next to a vessel) to the nearby tag that can.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .extract import Fragment
from .tags import DIGITS_ONLY, LETTERS_ONLY, Tag, parse_tag, tag_at_start
from .units import Quantity, parse_quantities

TAG_FIELDS = {"TAG", "TAGNO", "TAG_NO", "TAGNUMBER", "TAG_NUMBER", "ITEM", "NUMBER", "EQUIPMENT_TAG"}
AMBIGUITY_RATIO = 1.6   # nearest tag must be this much closer than the second-nearest
MAX_DISTANCE = 9.0      # in text heights; farther labels are not linked to any tag
FIELD_RATIO = 2.5       # v2: tags this much farther than the nearest stay candidates for field-aware relinking


@dataclass
class Occurrence:
    tag: Tag
    x: float
    y: float
    height: float
    fragment: Fragment


@dataclass
class SpecLink:
    quantity: Quantity
    fragment: Fragment
    link: str                       # attribute | inline | proximity | resolved-by-value
    distance: float = 0.0           # in text heights, for proximity links
    margin: float = math.inf        # second-nearest / nearest distance
    candidates: list = field(default_factory=list)   # nearby tags, nearest first: [(tag, distance)]


@dataclass
class Ambiguous:
    fragment: Fragment
    quantities: list[Quantity]
    candidates: list[tuple[str, float]]   # (canonical tag, distance in text heights)


@dataclass
class Drawing:
    occurrences: list[Occurrence] = field(default_factory=list)
    specs: dict[str, list[SpecLink]] = field(default_factory=dict)
    ambiguous: list[Ambiguous] = field(default_factory=list)
    orphans: list[tuple[Fragment, list[Quantity]]] = field(default_factory=list)

    def tags(self) -> dict[str, list[Occurrence]]:
        out: dict[str, list[Occurrence]] = {}
        for o in self.occurrences:
            out.setdefault(o.tag.canonical, []).append(o)
        return out


def _merge_bubbles(frags: list[Fragment]) -> tuple[list[Fragment], set[int]]:
    """Instrument bubbles split the tag in two lines: function letters above, loop number below."""
    used: set[int] = set()
    merged: list[Fragment] = []
    letters = [(i, f) for i, f in enumerate(frags) if LETTERS_ONLY.match(f.text.strip())]
    digits = [(i, f) for i, f in enumerate(frags) if DIGITS_ONLY.match(f.text.strip())]
    for i, a in letters:
        best = None
        for j, b in digits:
            if j in used or b.page != a.page:
                continue
            h = max(a.height, b.height)
            dx, dy = abs(a.x - b.x), a.y - b.y
            if dx < 1.2 * h and 0.4 * h < dy < 2.4 * h and (best is None or dy < best[1]):
                best = (j, dy)
        if best and i not in used:
            j = best[0]
            b = frags[j]
            used |= {i, j}
            merged.append(Fragment(f"{a.text.strip()}-{b.text.strip()}", (a.x + b.x) / 2,
                                   (a.y + b.y) / 2, max(a.height, b.height), a.source + "+bubble",
                                   page=a.page, extra={"parts": (a, b)}))
    return merged, used


def _is_line_label(rest: str) -> bool:
    """The text after a would-be tag is a piping class code like 75HB13 / A1A, with no quantity in it."""
    import re
    return bool(re.search(r"\b\d{1,3}[A-Z]{1,3}\d{0,3}\b", rest)) and not parse_quantities(rest)


def build(frags: list[Fragment]) -> Drawing:
    d = Drawing()
    bubbles, used = _merge_bubbles(frags)
    work = [f for i, f in enumerate(frags) if i not in used] + bubbles

    # 1. block attributes: the tag and its data travel together
    groups: dict[str, list[Fragment]] = {}
    for f in work:
        if f.source == "ATTRIB" and f.group:
            groups.setdefault(f.group, []).append(f)
    grouped_ids: set[int] = set()
    for members in groups.values():
        tag_frag = next((m for m in members if m.field_name in TAG_FIELDS and parse_tag(m.text)), None) or \
            next((m for m in members if parse_tag(m.text)), None)
        if not tag_frag:
            continue
        tag = parse_tag(tag_frag.text)
        d.occurrences.append(Occurrence(tag, tag_frag.x, tag_frag.y, tag_frag.height, tag_frag))
        for m in members:
            grouped_ids.add(id(m))
            if m is tag_frag:
                continue
            for q in parse_quantities(m.text):
                d.specs.setdefault(tag.canonical, []).append(SpecLink(q, m, "attribute"))

    # 2. loose text: tags, inline specs, separate spec labels
    loose_specs: list[tuple[Fragment, list[Quantity]]] = []
    for f in work:
        if id(f) in grouped_ids:
            continue
        text = f.text.strip()
        tag = parse_tag(text)
        if tag:
            d.occurrences.append(Occurrence(tag, f.x, f.y, f.height, f))
            continue
        hit = tag_at_start(text)
        if hit and _is_line_label(text[hit[1]:]):
            continue          # "M 48122 75HB 80": a pipe line designation (fluid, number, class, size)
        if hit:
            tag, end = hit
            d.occurrences.append(Occurrence(tag, f.x, f.y, f.height, f))
            for q in parse_quantities(text[end:]):
                d.specs.setdefault(tag.canonical, []).append(SpecLink(q, f, "inline"))
            continue
        qs = parse_quantities(text)
        if qs:
            loose_specs.append((f, qs))

    # 3. link separate spec labels to the nearest tag, refusing to guess when it is close
    for f, qs in loose_specs:
        dists = []
        for o in d.occurrences:
            if o.fragment.page != f.page:
                continue
            h = max(o.height, f.height, 1e-9)
            dists.append((math.hypot(o.x - f.x, o.y - f.y) / h, o.tag.canonical))
        # one tag can appear more than once; keep its nearest occurrence
        best: dict[str, float] = {}
        for dist, tag in dists:
            best[tag] = min(dist, best.get(tag, math.inf))
        ranked = sorted((v, k) for k, v in best.items())
        if not ranked or ranked[0][0] > MAX_DISTANCE:
            d.orphans.append((f, qs))
            continue
        nearest = ranked[0]
        second = ranked[1] if len(ranked) > 1 else (math.inf, "")
        margin = second[0] / max(nearest[0], 1e-9)
        if margin < AMBIGUITY_RATIO:
            d.ambiguous.append(Ambiguous(f, qs, [(t, round(v, 2)) for v, t in ranked[:3]
                                                 if v < nearest[0] * AMBIGUITY_RATIO]))
            continue
        near = [(t, round(v, 2)) for v, t in ranked if v <= nearest[0] * FIELD_RATIO]
        for q in qs:
            d.specs.setdefault(nearest[1], []).append(SpecLink(q, f, "proximity", nearest[0], margin, near))
    return d
