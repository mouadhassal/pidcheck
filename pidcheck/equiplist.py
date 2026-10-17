"""Read an equipment list (.xlsx or .csv) into tag -> specs.

The header row is found automatically (the first row with a "Tag" column). Units can sit in the
header ("Flow (GPM)", "Design pressure [barg]") or in the cells ("150 GPM"). Columns whose header
names a kind but no unit, with bare numbers, are reported instead of guessed.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from .tags import Tag, parse_tag
from .units import Quantity, parse_quantities, parse_with_unit

HEADER_UNIT = re.compile(r"[\(\[]\s*([^\)\]]+?)\s*[\)\]]|,\s*([^,]+)$")
KIND_WORDS = {
    "flow": ("flow", "capacity", "rate"), "volume": ("volume", "capacity (vol", "working volume"),
    "area": ("area", "surface", "duty area"), "size": ("size", "nps", "dn", "diameter (nominal"),
    "rating": ("rating", "class", "pn"), "pressure": ("pressure",), "temperature": ("temp",),
    "power": ("power", "motor", "rated power"),
}


@dataclass
class ListedItem:
    tag: Tag
    row: int
    description: str = ""
    specs: dict[str, list[Quantity]] = field(default_factory=dict)
    columns: dict[str, str] = field(default_factory=dict)   # kind -> header, for the report


@dataclass
class EquipmentList:
    items: dict[str, ListedItem] = field(default_factory=dict)
    duplicates: list[tuple[str, int, int]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _rows(path: Path) -> list[list]:
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.worksheets[0]
        return [list(r) for r in ws.iter_rows(values_only=True)]
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return [row for row in csv.reader(fh)]


def _header_unit(h: str) -> str:
    m = HEADER_UNIT.search(h)
    return (m.group(1) or m.group(2)).strip() if m else ""


def _kind_from_header(h: str) -> str:
    low = h.lower()
    for kind, words in KIND_WORDS.items():
        if any(w in low for w in words):
            return kind
    return ""


def read(path: str | Path) -> EquipmentList:
    path = Path(path)
    if path.suffix.lower() == ".xml":
        from . import dexpi
        return dexpi.read(path)
    rows = _rows(path)
    out = EquipmentList()
    hdr_i = next((i for i, r in enumerate(rows[:15]) if any(isinstance(c, str) and re.search(r"\btag\b", c, re.I)
                                                            for c in r)), None)
    if hdr_i is None:
        raise ValueError(f"{path.name}: no header row with a 'Tag' column in the first 15 rows")
    header = [str(c or "").strip() for c in rows[hdr_i]]
    tag_col = next(i for i, h in enumerate(header) if re.search(r"\btag\b", h, re.I))
    desc_col = next((i for i, h in enumerate(header) if re.search(r"desc|service|name", h, re.I)), None)
    for r_i, row in enumerate(rows[hdr_i + 1:], start=hdr_i + 2):
        if tag_col >= len(row) or row[tag_col] in (None, ""):
            continue
        tag = parse_tag(str(row[tag_col]))
        if not tag:
            out.problems.append(f"row {r_i}: '{row[tag_col]}' is not a recognisable tag")
            continue
        if tag.canonical in out.items:
            out.duplicates.append((tag.canonical, out.items[tag.canonical].row, r_i))
            continue
        item = ListedItem(tag, r_i, str(row[desc_col] or "") if desc_col is not None and desc_col < len(row) else "")
        for c_i, h in enumerate(header):
            if c_i in (tag_col, desc_col) or c_i >= len(row) or row[c_i] in (None, ""):
                continue
            cell, unit = row[c_i], _header_unit(h)
            qs = parse_quantities(str(cell)) if isinstance(cell, str) and re.search(r"[A-Za-z\"#°]", str(cell)) \
                else ([parse_with_unit(cell, unit)] if unit else [])
            qs = [q for q in qs if q is not None]
            if not qs:
                if _kind_from_header(h) and re.match(r"^\s*[\d.,]+\s*$", str(cell)):
                    out.problems.append(f"row {r_i}, column '{h}': number without a unit; not compared")
                continue
            for q in qs:
                item.specs.setdefault(q.kind, []).append(q)
                item.columns.setdefault(q.kind, h)
        out.items[tag.canonical] = item
    return out
