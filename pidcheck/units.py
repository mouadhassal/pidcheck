"""Parse engineering quantities as they are written on P&IDs and in equipment lists.

Every quantity is converted to SI, and it remembers how precisely it was written. Two values are
equal when they agree within the rounding of how they were displayed: "150 GPM" and "34.1 m³/h"
agree (150 GPM = 34.07 m³/h, and "34.1" means 34.05..34.15), while "150 GPM" and "36.3 m³/h" do not.

Pipe sizes and flange ratings are *nominal* designations, not measurements, so they are compared
through equivalence tables rather than converted: DN80 is the metric name of NPS 3", even though
3 in = 76.2 mm, and PN20 is the EN 1759 counterpart of ASME Class 150.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction

# --- linear units: kind -> {unit spelling (lowercase, spaces removed): factor to SI} -----------------
_GAL = 3.785411784e-3  # US gallon in m3
LINEAR = {
    "flow": {"gpm": _GAL / 60, "usgpm": _GAL / 60, "gal/min": _GAL / 60, "m3/h": 1 / 3600, "m³/h": 1 / 3600,
             "m3/hr": 1 / 3600, "m³/hr": 1 / 3600, "m^3/h": 1 / 3600, "m^3/hr": 1 / 3600, "l/s": 1e-3,
             "l/min": 1e-3 / 60, "m3/s": 1.0, "m³/s": 1.0},
    "volume": {"gal": _GAL, "usgal": _GAL, "gallons": _GAL, "m3": 1.0, "m³": 1.0, "m^3": 1.0, "l": 1e-3,
               "litres": 1e-3, "liters": 1e-3},
    "area": {"ft2": 0.09290304, "ft²": 0.09290304, "sqft": 0.09290304, "m2": 1.0, "m²": 1.0, "m^2": 1.0},
    "power": {"kw": 1e3, "hp": 745.69987158227, "w": 1.0, "mw": 1e6},
}
# pressure: (factor to Pa, reference) with reference "g" gauge, "a" absolute, "" unspecified
PRESSURE = {"psig": (6894.757293168, "g"), "psia": (6894.757293168, "a"), "psi": (6894.757293168, ""),
            "barg": (1e5, "g"), "bara": (1e5, "a"), "bar": (1e5, ""), "bar(g)": (1e5, "g"), "bar(a)": (1e5, "a"),
            "kpag": (1e3, "g"), "kpaa": (1e3, "a"), "kpa": (1e3, ""), "mpag": (1e6, "g"), "mpa": (1e6, "")}
ATM = 101325.0
TEMPERATURE = {"°c": "C", "degc": "C", "ºc": "C", "c": "C", "°f": "F", "degf": "F", "ºf": "F", "f": "F", "k": "K"}

# NPS (inches) <-> DN (mm) per ASME B36.10 / ISO 6708 nominal designations
NPS_TO_DN = {Fraction(1, 2): 15, Fraction(3, 4): 20, Fraction(1): 25, Fraction(5, 4): 32, Fraction(3, 2): 40,
             Fraction(2): 50, Fraction(5, 2): 65, Fraction(3): 80, Fraction(4): 100, Fraction(5): 125,
             Fraction(6): 150, Fraction(8): 200, Fraction(10): 250, Fraction(12): 300, Fraction(14): 350,
             Fraction(16): 400, Fraction(18): 450, Fraction(20): 500, Fraction(24): 600}
DN_TO_NPS = {v: k for k, v in NPS_TO_DN.items()}
# ASME class <-> PN (EN 1759-1 correspondence)
CLASS_TO_PN = {150: 20, 300: 50, 600: 110, 900: 150, 1500: 260, 2500: 420}
PN_TO_CLASS = {v: k for k, v in CLASS_TO_PN.items()}


@dataclass(frozen=True)
class Quantity:
    kind: str          # flow, volume, area, power, pressure, temperature, size, rating
    si: float          # SI value; for size: DN in mm; for rating: ASME class; temperature: kelvin
    half_step: float   # half of the last displayed digit, in SI (0 for nominal designations)
    text: str          # as written
    ref: str = ""      # pressure reference: "g", "a" or "" (unspecified)

    def describe(self) -> str:
        return self.text


_NUM = r"(?P<num>\d+(?:[.,]\d{3})*(?:[.,]\d+)?|\.\d+)"


def parse_number(s: str) -> tuple[float, float]:
    """Return (value, half_step) for a written number. Handles '1,100.5', '1.100,5', '34,1', '1 100'."""
    s = s.strip().replace(" ", " ")
    if " " in s:
        s = s.replace(" ", "")
    if "," in s and "." in s:            # both: the last one is the decimal mark
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:                       # only commas: thousands if groups of 3, else decimal comma
        parts = s.split(",")
        if len(parts) > 2 or (len(parts[-1]) == 3 and len(parts[0]) <= 3 and parts[0] != "0"):
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    decimals = len(s.split(".")[1]) if "." in s else 0
    return float(s), 0.5 * 10 ** (-decimals)


def _fraction(s: str) -> Fraction | None:
    s = s.strip().replace("-", " ")
    try:
        if " " in s:
            whole, frac = s.split(None, 1)
            return Fraction(int(whole)) + Fraction(frac)
        return Fraction(s)
    except (ValueError, ZeroDivisionError):
        return None


_RATING = re.compile(r"(?:\b(?:CLASS|CL\.?|ANSI|ASME)\s*(?P<a>\d{3,4})\b|\b(?P<b>\d{3,4})\s*(?:#|LB\b|LBS\b))", re.I)
_PN = re.compile(r"\bPN\s*(?P<pn>\d{1,3})\b", re.I)
_DN = re.compile(r"\bDN\s*=?\s*(?P<dn>\d{2,4})\b", re.I)
_NPS = re.compile(r"(?:\bNPS\s*(?P<a>\d+(?:[ -]\d/\d)?|\d/\d)|(?P<b>\d+(?:[ -]\d/\d)?|\d/\d|\d+\.\d+)\s*(?:\"|”|''|IN\b|INCH\b))", re.I)
# a bare "C"/"F" after a number ("100.00 C", as some CAD exporters write it) also counts as a temperature
_TEMP = re.compile(r"(?P<tnum>-?\d+(?:[.,]\d+)?)\s*(?P<u>°\s?[CF]|º\s?[CF]|DEG\s?[CF]|K\b|[CF](?=\s|$|[,;)]))", re.I)
# an optional minus sign that is not part of a range like "0-200": design pressures can be "-1.0 barg"
_GEN = re.compile(r"(?P<sign>(?<![\w.])-)?" + _NUM + r"\s*(?P<u>[A-Za-z°º][A-Za-z0-9°º³²^()]*(?:/[A-Za-z0-9³²]+)?)")


def parse_quantities(text: str) -> list[Quantity]:
    """All quantities found in one text fragment, left to right. Unknown units are ignored."""
    out: list[tuple[int, Quantity]] = []
    taken: list[tuple[int, int]] = []

    def free(a, b):
        return all(b <= x or a >= y for x, y in taken)

    for m in _RATING.finditer(text):
        v = int(m.group("a") or m.group("b"))
        if v in CLASS_TO_PN and free(*m.span()):
            out.append((m.start(), Quantity("rating", float(v), 0.0, m.group(0).strip())))
            taken.append(m.span())
    for m in _PN.finditer(text):
        pn = int(m.group("pn"))
        if free(*m.span()):
            cls = PN_TO_CLASS.get(pn)
            out.append((m.start(), Quantity("rating", float(cls) if cls else -float(pn), 0.0, m.group(0).strip())))
            taken.append(m.span())
    for m in _DN.finditer(text):
        if free(*m.span()):
            out.append((m.start(), Quantity("size", float(m.group("dn")), 0.0, m.group(0).strip())))
            taken.append(m.span())
    for m in _NPS.finditer(text):
        if not free(*m.span()):
            continue
        raw = m.group("a") or m.group("b")
        f = _fraction(raw) if "/" in raw else Fraction(raw).limit_denominator(8)
        dn = NPS_TO_DN.get(f) if f is not None else None
        si = float(dn) if dn else (-float(f) if f is not None else float("nan"))
        out.append((m.start(), Quantity("size", si, 0.0, m.group(0).strip())))
        taken.append(m.span())
    for m in _TEMP.finditer(text):
        if not free(*m.span()):
            continue
        u = re.sub(r"[\s°º]|DEG", "", m.group("u").upper())
        v, h = parse_number(m.group("tnum").lstrip("-"))
        v = -v if m.group("tnum").startswith("-") else v
        k = {"C": v + 273.15, "F": (v - 32) * 5 / 9 + 273.15, "K": v}[u]
        hk = h if u in "CK" else h * 5 / 9
        out.append((m.start(), Quantity("temperature", k, hk, m.group(0).strip())))
        taken.append(m.span())
    for m in _GEN.finditer(text):
        if not free(*m.span()):
            continue
        u = m.group("u").lower().replace(" ", "")
        q = None
        v, h = parse_number(m.group("num"))
        if m.group("sign"):
            v = -v
        for kind, table in LINEAR.items():
            if u in table:
                q = Quantity(kind, v * table[u], h * table[u], m.group(0).strip())
                break
        if q is None and u in PRESSURE:
            f, ref = PRESSURE[u]
            q = Quantity("pressure", v * f, h * f, m.group(0).strip(), ref)
        if q is not None:
            out.append((m.start(), q))
            taken.append(m.span())
    return [q for _, q in sorted(out, key=lambda t: t[0])]


def parse_with_unit(value, unit: str) -> Quantity | None:
    """A spreadsheet cell holding a bare number, with its unit taken from the column header."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)            # Excel stores 150 as 150.0; keep the precision it was typed with
    text = f"{value} {unit}".strip() if unit else str(value)
    if unit and unit.strip() in ('"', "in", "inch", "NPS"):
        text = f'{value}"'
    elif unit and unit.strip().upper() in ("DN",):
        text = f"DN{value}"
    elif unit and unit.strip().lower() in ("class", "#", "asme class"):
        text = f"CLASS {value}"
    qs = parse_quantities(text)
    return qs[0] if qs else None


@dataclass
class Comparison:
    equal: bool
    detail: str
    assumption: str = ""


def compare(a: Quantity, b: Quantity) -> Comparison:
    """Rounding-aware equality of two quantities of the same kind."""
    if a.kind != b.kind:
        return Comparison(False, f"different kinds: {a.kind} vs {b.kind}")
    if a.kind in ("size", "rating"):
        if a.si < 0 or b.si < 0:   # outside the equivalence tables
            return Comparison(a.text.replace(" ", "").upper() == b.text.replace(" ", "").upper(),
                              "no standard equivalent; compared as written")
        eq = a.si == b.si
        name = "DN" if a.kind == "size" else "Class"

        def canon(q):
            c = f"{name} {int(q.si)}"
            return q.text if q.text.replace(" ", "").upper() == c.replace(" ", "").upper() else f"{q.text} = {c}"
        if eq:
            return Comparison(True, f"{canon(a)}, {canon(b)}" if a.text != b.text else "same designation")
        return Comparison(False, f"nominal {name} {int(a.si)} vs {name} {int(b.si)}"
                          if (canon(a), canon(b)) != (a.text, b.text) else "different nominal designations")
    av, bv, assumption = a.si, b.si, ""
    if a.kind == "pressure":
        ra, rb = a.ref, b.ref
        if ra != rb:
            if "" in (ra, rb):
                assumption = "one pressure has no gauge/absolute marker; assumed gauge"
                ra, rb = ra or "g", rb or "g"
            av = av + ATM if ra == "g" else av
            bv = bv + ATM if rb == "g" else bv
    tol = a.half_step + b.half_step + 1e-9 * max(abs(av), abs(bv))
    diff = abs(av - bv)
    if a.kind == "temperature":
        return Comparison(diff <= tol, f"{diff:.1f} °C apart; rounding allows {tol:.1f} °C", assumption)
    rel = diff / max(abs(bv), 1e-12)
    return Comparison(diff <= tol, f"{rel:.1%} apart; rounding allows {tol / max(abs(bv), 1e-12):.2%}",
                      assumption)
