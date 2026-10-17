"""Recognise and normalise equipment and instrument tags.

ISA-5.1 style (P-101, FT-101, PSV-401A) and the DIN/IEC style common in European plants
(P4711, H 1008, TICSA 4750.03, HV 4750.01: no or a space separator, dotted loop numbers).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# letters, an optional separator, a loop number, an optional ".nn" sub-number, an optional suffix.
# Separators: hyphen, the Unicode dashes, middle dot and bullet (PDF fonts without an en dash often
# substitute one), underscore, space. Letters must be capitals: "MNb 47122" is a fluid code, not a tag.
SEP = "\\-\u2010-\u2015\u00b7\u2022_"
NUM = r"(?P<number>[0-9OIl]{2,5})(?P<sub>\.[0-9]{1,3})?(?P<suffix>[A-Z]{0,2})"
TAG = re.compile(r"^(?P<letters>[A-Z]{1,5})\s*(?P<sep>[" + SEP + r" ]?)\s*" + NUM + "$")
# a tag at the start of a longer label ("CV-102 3\" 150#", "CV102 ...", "PSV-I41 ..."): the loop number
# may contain a confusable letter as long as it has a digit
TAG_IN_TEXT = re.compile(r"\b(?P<letters>[A-Z]{1,5})\s?(?P<sep>[" + SEP + r"]?)\s?(?=[0-9OI]*[0-9])"
                         r"(?P<number>[0-9OI]{2,5})(?P<sub>\.[0-9]{1,3})?(?P<suffix>[A-Z]?)(?=\s|$)")
LETTERS_ONLY = re.compile(r"^[A-Z]{1,5}$")
DIGITS_ONLY = re.compile(r"^[0-9]{2,5}(?:\.[0-9]{1,3})?[A-Z]?$")
# confusable characters that OCR, fonts and tired drafters swap
CONFUSABLE = str.maketrans({"O": "0", "I": "1", "l": "1"})
# words that precede a number on drawings without being a tag (ratings, sizes, title-block fields)
NOT_TAGS = {"NOTE", "REV", "DWG", "SHT", "NO", "PN", "DN", "CL", "NPS", "ISO", "DIN", "EN", "UNIT", "SIZE",
            "DATE", "BY", "CHK", "SHEET", "PAGE", "CLASS", "ANSI", "ASME", "SCH", "RATING", "LB", "LBS", "IN",
            "INCH", "API", "PSI", "PSIG", "BAR", "BARG", "GPM", "KW", "HP", "TYPE", "MODEL", "ITEM", "POS"}


def _ok(m: re.Match) -> bool:
    """Reject look-alikes: a word in NOT_TAGS, or 'Q80'-style codes (no separator and only 2 digits)."""
    if m.group("letters") in NOT_TAGS:
        return False
    if len(m.group("letters")) == 5 and not (m.group("sep") or m.group("sub")):
        return False          # "DEXPI2013": five letters glued to a number is a word, not a tag
    return bool(m.group("sep")) or len(m.group("number")) >= 3 or bool(m.group("sub"))


@dataclass(frozen=True)
class Tag:
    canonical: str   # P-101
    written: str     # as on the drawing
    typo_fixed: bool = False  # a letter O / I was read as a digit

    @property
    def letters(self) -> str:
        return self.canonical.split("-")[0]


def parse_tag(text: str) -> Tag | None:
    s = text.strip()
    m = TAG.match(s)
    if not m or not _ok(m):
        return None
    number = m.group("number")
    fixed = number.translate(CONFUSABLE)
    if not fixed.isdigit():
        return None
    return Tag(f"{m.group('letters')}-{fixed}{m.group('sub') or ''}{m.group('suffix')}", s, fixed != number)


def tag_at_start(text: str) -> tuple[Tag, int] | None:
    """A tag at the start of a longer label, and where the rest of the label begins."""
    m = TAG_IN_TEXT.match(text.strip())
    if not m or not _ok(m):
        return None
    tag = parse_tag(m.group(0))
    return (tag, m.end() + (len(text) - len(text.lstrip()))) if tag else None


def written_differently(tag: Tag, house_style: str = "-") -> bool:
    """True when the tag is written with a different separator than the drawing's usual one."""
    if tag.typo_fixed:
        return False
    return tag.written.upper() != tag.canonical.replace("-", house_style, 1)


def house_style(tags: list[Tag]) -> str:
    """The separator most tags on this drawing use ('-', ' ' or ''), so DIN-style sheets that write
    'P4711' everywhere are not reported tag by tag."""
    from collections import Counter
    seps = Counter()
    for t in tags:
        letters = t.canonical.split("-")[0]
        rest = t.written[len(letters):]
        seps["-" if rest[:1] in "-‐‑‒–—―·•_" and rest[:1] else
             (" " if rest[:1] == " " else "")] += 1
    return seps.most_common(1)[0][0] if seps else "-"


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def is_transposition(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
    return len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]


def likely_same(drawing: str, listed: str) -> bool:
    """A drawing tag missing from the list that is probably a mistyped list tag."""
    da, la = drawing.split("-", 1), listed.split("-", 1)
    if da[0] != la[0]:
        return False
    return edit_distance(da[1], la[1]) == 1 or is_transposition(da[1], la[1])
