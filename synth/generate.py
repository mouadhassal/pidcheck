"""Generate synthetic P&IDs (DXF + vector PDF) with matching equipment lists and planted faults.

Each sheet is a process area: process trains of tanks, pumps, heat exchangers and vessels, with
control and hand valves on the lines between them, relief valves and instrument bubbles. The truth
(the equipment list) is written in one unit system and the drawing in the same or the other, so
most comparisons need a unit conversion and a rounding judgement.

Faults are planted on the drawing (or, rarely, in the list) and recorded in <name>.truth.json.
Traps are differences that are NOT errors (unit systems, DN vs NPS, PN vs Class, gauge vs absolute,
decimal commas, tag spelling) and must not be flagged.

    python synth/generate.py --set dev  --out data/dev      # seeds 0-19, the development set
    python synth/generate.py --set test --out data/test     # seeds 1000-1039, held-out (see PREREGISTRATION.md)
    python synth/generate.py --set confirm --out data/confirm  # seeds 2000-2039, fresh set for the v2 amendment
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

import ezdxf
from ezdxf.enums import TextEntityAlignment

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pidcheck.render import render  # noqa: E402
from pidcheck.units import CLASS_TO_PN, NPS_TO_DN  # noqa: E402

GAL, GPM = 3.785411784e-3, 3.785411784e-3 / 60
NPS_LIST = sorted(NPS_TO_DN)
H = 2.5      # tag text height
HS = 2.2     # spec text height


def sig(v: float, n: int = 3) -> tuple[float, int]:
    """Round to n significant figures; returns (value, decimals)."""
    if v == 0:
        return 0.0, 0
    d = max(0, n - 1 - int(math.floor(math.log10(abs(v)))))
    return round(v, d), d


def fmt_num(v: float, decimals: int, comma: bool) -> str:
    s = f"{v:.{decimals}f}"
    if comma:
        s = s.replace(".", ",")
    elif v >= 1000 and decimals == 0:
        s = f"{int(round(v)):,}"
    return s


def nps_text(f: Fraction, style: int) -> str:
    whole, rem = divmod(f.numerator, f.denominator)
    s = (f"{whole}-{Fraction(rem, f.denominator)}" if whole and rem else str(f if not whole else whole))
    return {0: f'{s}"', 1: f"{s} IN", 2: f"NPS {s}"}[style % 3]


# ---------------------------------------------------------------------------- display of quantities
def show(kind: str, si: float, system: str, rng: random.Random, comma=False, traps=None, plain=False) -> str:
    """How a value is written in a unit system. si: SI value (size: NPS Fraction, rating: class)."""
    t = traps if traps is not None else {}
    if kind == "size":
        if system == "metric":
            t["dn_vs_nps"] = t.get("dn_vs_nps", 0) + 1
            return f"DN{NPS_TO_DN[si]}"
        return nps_text(si, rng.randrange(3))
    if kind == "rating":
        if system == "metric" and not plain and rng.random() < 0.5:
            t["pn_vs_class"] = t.get("pn_vs_class", 0) + 1
            return f"PN{CLASS_TO_PN[si]}"
        return rng.choice([f"{si}#", f"CLASS {si}", f"CL{si}"])
    if kind == "temperature":
        c = si - 273.15
        if system == "us":
            return f"{round(c * 9 / 5 + 32)} °F"
        return f"{round(c)} °C"
    if kind == "pressure":
        if system == "us":
            v, d = sig(si / 6894.757293168)
            return f"{fmt_num(v, d, False)} psig"
        r = 1.0 if plain else rng.random()
        if r < 0.12:
            v, d = sig((si + 101325) / 1e5)
            t["absolute_pressure"] = t.get("absolute_pressure", 0) + 1
            return f"{fmt_num(v, d, comma)} bara"
        if r < 0.20:
            v, d = sig(si / 1e5)
            t["unmarked_pressure"] = t.get("unmarked_pressure", 0) + 1
            return f"{fmt_num(v, d, comma)} bar"
        v, d = sig(si / 1e5)
        return f"{fmt_num(v, d, comma)} barg"
    units = {"flow": (("GPM", GPM), ("m³/h", 1 / 3600)), "volume": (("gal", GAL), ("m³", 1.0)),
             "area": (("ft²", 0.09290304), ("m²", 1.0)), "power": (("hp", 745.69987158227), ("kW", 1e3))}[kind]
    name, f = units[0] if system == "us" else units[1]
    if system == "metric" and kind == "flow" and not plain and rng.random() < 0.3:
        name = rng.choice(["m3/h", "m³/hr"])
    v, d = sig(si / f)
    if comma:
        t["decimal_comma"] = t.get("decimal_comma", 0) + 1
    return f"{fmt_num(v, d, comma)} {name}"


def unit_slip(kind: str, si: float, list_system: str, dwg_system: str) -> str | None:
    """The list's number written with the drawing system's unit: '150 GPM' shown as '150 m³/h'."""
    if list_system == dwg_system or kind not in ("flow", "volume", "area", "power"):
        return None
    units = {"flow": (("GPM", GPM), ("m³/h", 1 / 3600)), "volume": (("gal", GAL), ("m³", 1.0)),
             "area": (("ft²", 0.09290304), ("m²", 1.0)), "power": (("hp", 745.69987158227), ("kW", 1e3))}[kind]
    (ln, lf), (dn, _) = (units[0], units[1]) if list_system == "us" else (units[1], units[0])
    v, d = sig(si / lf)
    return f"{fmt_num(v, d, False)} {dn}"


# ---------------------------------------------------------------------------------------- the plant
@dataclass
class Item:
    tag: str
    etype: str
    x: float
    y: float
    specs: dict = field(default_factory=dict)       # kind -> SI value (truth)
    style: str = "text"
    drawn_tag: str = ""                               # as written on the drawing
    drawn: dict = field(default_factory=dict)         # kind -> text on drawing
    rotate: bool = False
    offset: tuple = (0.0, 0.0)                     # spec labels pushed sideways (crowded sheets)
    on_drawing: bool = True
    in_list: bool = True


SPEC_KINDS = {"pump": ("flow", "power"), "tank": ("volume", "temperature"), "hx": ("area", "pressure"),
              "vessel": ("volume", "pressure", "temperature"), "cv": ("size", "rating"),
              "hv": ("size", "rating"), "psv": ("size", "pressure"), "inst": ()}
PREFIX = {"pump": "P", "tank": "TK", "hx": "E", "vessel": "V", "cv": "CV", "hv": "HV", "psv": "PSV"}


def truth_value(kind: str, rng: random.Random) -> float:
    if kind == "flow":
        return rng.choice([25, 40, 60, 75, 90, 120, 150, 180, 220, 300, 450, 600, 800]) * GPM * rng.uniform(0.9, 1.1)
    if kind == "power":
        return rng.uniform(3, 160) * 1e3
    if kind == "volume":
        return rng.uniform(2, 180)
    if kind == "area":
        return rng.uniform(6, 140)
    if kind == "pressure":
        return rng.uniform(3, 30) * 1e5
    if kind == "temperature":
        return 273.15 + rng.choice(range(20, 180, 5))
    if kind == "size":
        return rng.choice(NPS_LIST[2:13])
    if kind == "rating":
        return rng.choice([150, 150, 150, 300, 300, 600])
    raise KeyError(kind)


def plant(rng: random.Random, crowded: bool) -> list[Item]:
    items: list[Item] = []
    n_trains = rng.randint(2, 3)
    dx = 30 if crowded else 58
    for t in range(n_trains):
        area = (t + 1) * 100
        y = 190 - t * 80
        seq = ["tank", "pump", "hx", "vessel"] + rng.sample(["pump", "hx", "vessel"], rng.randint(1, 3))
        x = 30.0
        counter: dict[str, int] = {}
        for i, et in enumerate(seq):
            counter[et] = counter.get(et, 0) + 1
            tag = f"{PREFIX[et]}-{area + counter[et]}"
            items.append(Item(tag, et, x, y, {k: truth_value(k, rng) for k in SPEC_KINDS[et]}))
            if i < len(seq) - 1:
                vt = rng.choice(["cv", "hv", "cv"])
                counter[vt] = counter.get(vt, 0) + 1
                items.append(Item(f"{PREFIX[vt]}-{area + 10 + counter[vt]}", vt, x + dx / 2, y,
                                  {k: truth_value(k, rng) for k in SPEC_KINDS[vt]}))
            if et == "vessel" and rng.random() < 0.8:
                counter["psv"] = counter.get("psv", 0) + 1
                items.append(Item(f"PSV-{area + 40 + counter['psv']}", "psv", x + 7, y + 22,
                                  {k: truth_value(k, rng) for k in SPEC_KINDS["psv"]}))
            if rng.random() < 0.7:
                fn = rng.choice(["FT", "PT", "TT", "LT", "FIC", "PIC", "TIC"])
                counter[fn] = counter.get(fn, 0) + 1
                items.append(Item(f"{fn}-{area + 50 + counter[fn]}", "inst", x - 6, y + 26))
            x += dx
    return items


# ------------------------------------------------------------------------------------ drawing it
def symbol(msp, et, x, y):
    if et == "pump":
        msp.add_circle((x, y), 5)
        msp.add_lwpolyline([(x - 3, y - 3), (x + 4, y), (x - 3, y + 3)], close=True)
    elif et == "tank":
        msp.add_lwpolyline([(x - 7, y - 9), (x + 7, y - 9), (x + 7, y + 7), (x - 7, y + 7)], close=True)
        msp.add_arc((x, y + 7), 7, 0, 180)
    elif et == "hx":
        msp.add_circle((x, y), 6)
        msp.add_lwpolyline([(x - 6, y), (x - 3, y + 3), (x, y - 3), (x + 3, y + 3), (x + 6, y)])
    elif et == "vessel":
        msp.add_lwpolyline([(x - 5, y - 8), (x - 5, y + 8)])
        msp.add_lwpolyline([(x + 5, y - 8), (x + 5, y + 8)])
        msp.add_arc((x, y + 8), 5, 0, 180)
        msp.add_arc((x, y - 8), 5, 180, 360)
    elif et in ("cv", "hv"):
        msp.add_lwpolyline([(x - 3, y - 2), (x + 3, y + 2), (x + 3, y - 2), (x - 3, y + 2)], close=True)
        if et == "cv":
            msp.add_line((x, y), (x, y + 4))
            msp.add_arc((x, y + 4), 2, 0, 180)
    elif et == "psv":
        msp.add_lwpolyline([(x - 2, y - 3), (x + 2, y + 3), (x - 2, y + 3), (x + 2, y - 3)], close=True)
        msp.add_line((x, y - 3), (x, y - 14))
    elif et == "inst":
        msp.add_circle((x, y), 4.2)
        msp.add_line((x, y - 4.2), (x, y - 20))


def place(e, x, y, align=TextEntityAlignment.MIDDLE_CENTER):
    e.set_placement((x, y), align=align)
    return e


def draw(items: list[Item], path: Path, rng: random.Random, variant: dict):
    doc = ezdxf.new("R2018", setup=True)
    msp = doc.modelspace()
    for name in ("pump", "tank", "hx", "vessel", "cv", "hv", "psv", "inst"):
        blk = doc.blocks.new(f"SYM_{name.upper()}")
        symbol(blk, name, 0, 0)
    # process lines between consecutive main equipment on each train
    trains: dict[float, list[Item]] = {}
    for it in items:
        if it.etype in ("pump", "tank", "hx", "vessel"):
            trains.setdefault(it.y, []).append(it)
    for y, row in trains.items():
        row = sorted(row, key=lambda i: i.x)
        msp.add_line((row[0].x, y), (row[-1].x, y))
    for it in items:
        if not it.on_drawing:
            continue
        tag_text = it.drawn_tag or it.tag
        lines = [it.drawn[k] for k in SPEC_KINDS[it.etype] if k in it.drawn]
        if it.style == "attrib":
            ref = msp.add_blockref(f"SYM_{it.etype.upper()}", (it.x, it.y))
            ty = it.y - {"vessel": 17, "tank": 13, "pump": 12, "hx": 12}.get(it.etype, 6)
            tx = it.x
            if it.etype == "inst":
                ty = it.y
            if it.etype == "psv":
                tx, ty = it.x + 14, it.y + 2
            a = ref.add_attrib("TAG", tag_text, (tx, ty), dxfattribs={"height": 1.6 if it.etype == "inst" else H})
            a.set_placement((tx, ty), align=TextEntityAlignment.MIDDLE_CENTER)
            for i, (k, txt) in enumerate((k, it.drawn[k]) for k in SPEC_KINDS[it.etype] if k in it.drawn):
                a = ref.add_attrib(k.upper(), txt, (tx, ty - 4 - i * 3.6), dxfattribs={"height": HS})
                a.set_placement((tx, ty - 4 - i * 3.6), align=TextEntityAlignment.MIDDLE_CENTER)
            continue
        msp.add_blockref(f"SYM_{it.etype.upper()}", (it.x, it.y))
        if it.etype == "inst":
            fn, num = tag_text.split("-", 1) if "-" in tag_text else (tag_text[:2], tag_text[2:])
            place(msp.add_text(fn, height=1.8), it.x, it.y + 1.3)
            place(msp.add_text(num, height=1.8), it.x, it.y - 1.4)
            continue
        if it.rotate:
            # vertical label to the right of a line-mounted item, read bottom-to-top
            lx = it.x + 6
            t = msp.add_text(tag_text, height=H, dxfattribs={"rotation": 90})
            place(t, lx, it.y)
            for i, txt in enumerate(lines):
                t = msp.add_text(txt, height=HS, dxfattribs={"rotation": 90})
                place(t, lx + 3.6 * (i + 1), it.y)
            continue
        ty = it.y - {"vessel": 17, "tank": 13, "pump": 12, "hx": 12}.get(it.etype, 6)
        if it.etype == "psv":
            it = Item(it.tag, it.etype, it.x + 14, it.y + 2, it.specs, it.style, it.drawn_tag, it.drawn)
            ty = it.y
        if it.style == "inline" and it.etype in ("cv", "hv", "psv"):
            place(msp.add_text(" ".join([tag_text] + lines), height=HS), it.x, ty)
            continue
        place(msp.add_text(tag_text, height=H), it.x, ty)
        if not lines:
            continue
        if it.style == "mtext":
            attach = variant.get("mtext_attach", 2)
            content = "\\P".join("{\\fArial|b0|i0;" + ln + "}" if i == 0 else ln for i, ln in enumerate(lines))
            mt = msp.add_mtext(content, dxfattribs={"char_height": HS, "attachment_point": attach})
            row = (attach - 1) // 3
            first_y = ty - 4
            ins_y = {0: first_y, 1: first_y - (len(lines) - 1) * HS * 1.66 / 2,
                     2: first_y - (len(lines) - 1) * HS * 1.66}[row]
            mt.dxf.insert = (it.x, ins_y)
        else:
            ox, oy = it.offset
            for i, txt in enumerate(lines):
                place(msp.add_text(txt, height=HS), it.x + ox, ty - 4 - i * 3.6 + oy)
    # title block and notes: text that is not a tag and must not confuse the reader
    x0 = max(i.x for i in items) + 30
    msp.add_lwpolyline([(x0, 10), (x0 + 90, 10), (x0 + 90, 40), (x0, 40)], close=True)
    place(msp.add_text(f"DWG NO. PID-{rng.randint(1000, 9999)}-01", height=3), x0 + 45, 32)
    place(msp.add_text(f"REV {rng.choice('ABC')}  SCALE NTS", height=2.5), x0 + 45, 25)
    place(msp.add_text("NOTE 1: ALL LINES 150# UNLESS NOTED", height=2.2), x0 + 45, 18, )
    doc.saveas(path)


# ------------------------------------------------------------------------------------- faults
FIELD_FAULTS = ("value_changed", "unit_slip", "size_changed", "rating_changed")


def plant_faults(items: list[Item], rng: random.Random, list_sys: str, dwg_sys: str, n: int) -> list[dict]:
    faults = []
    used: set[str] = set()
    existing = {i.tag for i in items}
    candidates = [i for i in items if i.etype != "inst"]
    rng.shuffle(candidates)
    kinds = ["value_changed", "value_changed", "unit_slip", "size_changed", "rating_changed", "missing",
             "confusable", "transposed"]
    for it in candidates:
        if len(faults) >= n:
            break
        if it.tag in used:
            continue
        f = rng.choice(kinds)
        fields = list(it.specs)
        if f in ("value_changed",) and not any(k not in ("size", "rating") for k in fields):
            f = "size_changed" if "size" in fields else "missing"
        if f == "unit_slip" and (list_sys == dwg_sys or not any(k in ("flow", "volume", "area", "power")
                                                                 for k in fields)):
            f = "value_changed" if any(k not in ("size", "rating") for k in fields) else "missing"
        if f in ("size_changed", "rating_changed") and ("size" if f == "size_changed" else "rating") not in fields:
            f = "missing"
        if f == "confusable" and not any(c in it.tag.split("-")[1] for c in "01"):
            f = "transposed"
        if f == "transposed":
            num = it.tag.split("-")[1]
            swaps = [k for k in range(len(num) - 1) if num[k] != num[k + 1]]
            new = None
            for k in swaps:
                cand = num[:k] + num[k + 1] + num[k] + num[k + 2:]
                if f"{it.tag.split('-')[0]}-{cand}" not in existing:
                    new = f"{it.tag.split('-')[0]}-{cand}"
                    break
            if not new:
                continue
        used.add(it.tag)
        rec = {"type": f, "tag": it.tag}
        if f == "value_changed":
            k = rng.choice([k for k in fields if k not in ("size", "rating")])
            if k == "temperature":
                new_si = it.specs[k] + rng.choice([-1, 1]) * rng.uniform(10, 40)
            else:
                new_si = it.specs[k] * (rng.uniform(1.08, 1.4) if rng.random() < 0.5 else rng.uniform(0.6, 0.92))
            it.drawn[k] = show(k, new_si, dwg_sys, rng)
            rec.update(field=k, drawing=it.drawn[k])
        elif f == "unit_slip":
            k = rng.choice([k for k in fields if k in ("flow", "volume", "area", "power")])
            it.drawn[k] = unit_slip(k, it.specs[k], list_sys, dwg_sys)
            rec.update(field=k, drawing=it.drawn[k])
        elif f == "size_changed":
            i0 = NPS_LIST.index(it.specs["size"])
            j = min(max(i0 + rng.choice([-2, -1, 1, 2]), 0), len(NPS_LIST) - 1)
            if j == i0:
                j = i0 + 1
            it.drawn["size"] = show("size", NPS_LIST[j], dwg_sys, rng)
            rec.update(field="size", drawing=it.drawn["size"])
        elif f == "rating_changed":
            new = {150: 300, 300: rng.choice([150, 600]), 600: 300}[it.specs["rating"]]
            it.drawn["rating"] = show("rating", new, dwg_sys, rng)
            rec.update(field="rating", drawing=it.drawn["rating"])
        elif f == "missing":
            it.on_drawing = False
        elif f == "confusable":
            pre, num = it.tag.split("-")
            pos = [k for k, c in enumerate(num) if c in "01"]
            k = rng.choice(pos)
            it.drawn_tag = f"{pre}-{num[:k]}{'O' if num[k] == '0' else 'I'}{num[k + 1:]}"
            rec.update(drawing=it.drawn_tag)
        elif f == "transposed":
            it.drawn_tag = new
            existing.add(new)
            rec.update(drawing=new)
        faults.append(rec)
    # item-level additions: an extra item and a duplicated tag, placed in free space
    for f in ("extra", "duplicate"):
        if len(faults) >= n + 2 or rng.random() < 0.45:
            continue
        donors = [i for i in items if i.etype in ("cv", "hv") and i.tag not in used and i.on_drawing]
        if not donors:
            continue
        d = rng.choice(donors)
        ymax = max(i.y for i in items)
        x = rng.uniform(40, 200)
        if f == "extra":
            pre = PREFIX[d.etype]
            k = 90
            while f"{pre}-{(int(d.tag.split('-')[1]) // 100) * 100 + k}" in existing:
                k += 1
            tag = f"{pre}-{(int(d.tag.split('-')[1]) // 100) * 100 + k}"
            existing.add(tag)
            new = Item(tag, d.etype, x, ymax + 45, {kk: truth_value(kk, rng) for kk in SPEC_KINDS[d.etype]},
                       style=d.style, in_list=False)
            new.drawn = {kk: show(kk, v, dwg_sys, rng) for kk, v in new.specs.items()}
            items.append(new)
            faults.append({"type": "extra", "tag": tag})
        else:
            used.add(d.tag)
            new = Item(d.tag, d.etype, x, ymax + 45, dict(d.specs), style=d.style, drawn=dict(d.drawn),
                       in_list=False)
            items.append(new)
            faults.append({"type": "duplicate", "tag": d.tag})
    return faults


def write_list(items: list[Item], path: Path, system: str, rng: random.Random, list_dup: bool) -> dict | None:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Equipment"
    ws.append(["EQUIPMENT LIST", "", "", "Rev 2"])
    ws.append([])
    headers_units = {"flow": ("Design flow", "GPM" if system == "us" else "m³/h"),
                     "power": ("Motor power", "hp" if system == "us" else "kW"),
                     "volume": ("Volume", "gal" if system == "us" else "m³"),
                     "area": ("Surface area", "ft²" if system == "us" else "m²"),
                     "pressure": ("Design pressure", "psig" if system == "us" else "barg"),
                     "temperature": ("Design temperature", "°F" if system == "us" else "°C"),
                     "size": ("Size", None), "rating": ("Rating", None)}
    kinds = list(headers_units)
    ws.append(["Tag", "Description"] + [f"{h} ({u})" if u else h for h, u in (headers_units[k] for k in kinds)])
    desc = {"pump": "Centrifugal pump", "tank": "Storage tank", "hx": "Shell & tube exchanger",
            "vessel": "Separator", "cv": "Control valve", "hv": "Isolation valve", "psv": "Relief valve",
            "inst": "Instrument"}
    dup = None
    rows = [i for i in items if i.in_list]
    for it in rows:
        row = [it.tag, desc[it.etype]]
        for k in kinds:
            if k not in it.specs:
                row.append(None)
                continue
            txt = show(k, it.specs[k], system, rng, plain=True)
            if k in ("size", "rating"):
                row.append(txt)
            elif k == "temperature":
                row.append(float(txt.split()[0]))
            else:
                num = txt.split()[0].replace(",", "")
                row.append(float(num))
        ws.append(row)
    if list_dup:
        it = rng.choice([i for i in rows if i.etype != "inst"])
        ws.append([it.tag, desc[it.etype] + " (spare)"])
        dup = {"type": "list_duplicate", "tag": it.tag}
    wb.save(path)
    return dup


def make(seed: int, out: Path, test: bool) -> dict:
    rng = random.Random(seed)
    crowded = test and rng.random() < 0.35
    items = plant(rng, crowded)
    list_sys = rng.choice(["us", "metric"])
    dwg_sys = rng.choice(["us", "metric"])
    comma = dwg_sys == "metric" and rng.random() < (0.4 if test else 0.25)
    traps: dict[str, int] = {}
    style_pool = ["attrib", "text", "text", "mtext", "inline"]
    for it in items:
        it.style = rng.choice(style_pool)
        if test and it.etype in ("cv", "hv") and rng.random() < 0.2:
            it.rotate = True
            traps["rotated_label"] = traps.get("rotated_label", 0) + 1
        it.drawn = {k: show(k, v, dwg_sys, rng, comma, traps) for k, v in it.specs.items()}
        if it.etype != "inst" and rng.random() < 0.1:
            pre, num = it.tag.split("-")
            it.drawn_tag = rng.choice([f"{pre}{num}", f"{pre}–{num}", f"{pre} {num}"])
            traps["tag_spelling"] = traps.get("tag_spelling", 0) + 1
    if crowded:
        # on crowded sheets some valve labels drift towards the next equipment tag: genuinely ambiguous
        main = [i for i in items if i.etype in ("pump", "tank", "hx", "vessel")]
        for it in items:
            if it.etype in ("cv", "hv") and it.style in ("text",) and not it.rotate and rng.random() < 0.5:
                nxt = min((m for m in main if m.y == it.y and m.x > it.x), key=lambda m: m.x, default=None)
                if nxt:
                    it.offset = ((nxt.x - it.x) * 0.5, -6.0)
                    traps["ambiguous_label_expected"] = traps.get("ambiguous_label_expected", 0) + 1
    if list_sys != dwg_sys:
        traps["unit_system_conversions"] = sum(len(i.specs) for i in items)
    n = rng.randint(4, 8)
    faults = plant_faults(items, rng, list_sys, dwg_sys, n)
    variant = {"mtext_attach": rng.choice([1, 2, 5, 8]) if test else rng.choice([1, 2])}
    name = f"pid_{seed:04d}"
    draw(items, out / f"{name}.dxf", rng, variant)
    dup = write_list(items, out / f"{name}_equipment.xlsx", list_sys, rng, rng.random() < 0.15)
    if dup:
        faults.append(dup)
    render(out / f"{name}.dxf", out / f"{name}.pdf")
    truth = {"name": name, "seed": seed, "set": "test" if test else "dev", "list_units": list_sys,
             "drawing_units": dwg_sys, "decimal_comma": comma, "crowded": crowded, "variant": variant,
             "faults": faults, "traps": traps, "items_on_drawing": sum(i.on_drawing for i in items),
             "items_in_list": sum(i.in_list for i in items),
             "values_on_drawing": sum(len(i.drawn) for i in items if i.on_drawing)}
    (out / f"{name}.truth.json").write_text(json.dumps(truth, indent=1, ensure_ascii=False), encoding="utf-8")
    return truth


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=["dev", "test", "confirm"], required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    # confirm: a fresh held-out set with the test-set variants, generated only to check the v2 amendment
    seeds = {"dev": range(0, 20), "test": range(1000, 1040), "confirm": range(2000, 2040)}[a.set]
    tot = {"faults": 0, "values": 0}
    for s in seeds:
        t = make(s, out, a.set != "dev")
        tot["faults"] += len(t["faults"])
        tot["values"] += t["values_on_drawing"]
    print(f"{a.set}: {len(seeds)} drawings, {tot['faults']} planted faults, {tot['values']} values on drawings")


if __name__ == "__main__":
    main()
