"""Use a DEXPI file (ISO 15926 P&ID exchange XML) as the reference list.

DEXPI exports from CAD tools carry every tagged item (equipment, valves, instrument functions) with
typed attributes such as DesignVolumeFlowRate in MetreCubedPerHour. Attributes of untagged children
(the chambers of a vessel, the casing of a pump) belong to the nearest tagged ancestor, which is how
a jacketed tank ends up with two design pressures.
"""
from __future__ import annotations

import re
from pathlib import Path

from .equiplist import EquipmentList, ListedItem
from .tags import parse_tag
from .units import parse_quantities

UNIT = {"MetreCubed": "m³", "MetreCubedPerHour": "m³/h", "MetreSquared": "m²", "Kilowatt": "kW", "Watt": "W",
        "barg": "barg", "bara": "bara", "bar": "bar", "BarGauge": "barg", "BarAbsolute": "bara",
        "DegreeCelsius": "°C", "DegreeFahrenheit": "°F", "Kelvin": "K", "LitrePerSecond": "l/s",
        "KiloPascal": "kPa", "Pascal": "Pa"}
# attribute name -> it is worth comparing (names vary between exporters; matched by substring)
WANTED = ("Capacity", "VolumeFlowRate", "HeatTransferArea", "HeatFlowRate", "Power", "DesignPressure",
          "SetPressure", "DesignTemperature", "NominalDiameter")
TAGGED = {"Equipment", "PipingComponent", "ProcessInstrumentationFunction", "ProcessInstrument",
          "InstrumentComponent", "PropertyBreak", "ActuatingSystem"}


def read(path: str | Path) -> EquipmentList:
    from lxml import etree

    root = etree.parse(str(path)).getroot()
    out = EquipmentList()

    seen_names: dict[str, str] = {}   # ComponentName -> tag: the same object can appear twice in a file

    def position_number(el) -> str | None:
        for ga in el.findall("./GenericAttributes/GenericAttribute"):
            if ga.get("Name") == "PositionNumberAssignmentClass" and (ga.get("Value") or "").strip():
                return ga.get("Value").strip()
        return None

    def full_label_tag(el, short: str | None) -> str | None:
        """IEC 81346 exports (e.g. COMOS) keep the loop number in FullLabel: '=A10.A20.C05.A80.100.LT' is LT-100."""
        for ga in el.findall("./GenericAttributes/GenericAttribute"):
            if ga.get("Name") == "FullLabel":
                m = re.search(r"\.(\d{2,5})\.([A-Z]{1,5})$", ga.get("Value") or "")
                if m and (not short or short == m.group(2)):
                    return f"{m.group(2)}-{m.group(1)}"
        return None

    def visit(el, owner: ListedItem | None):
        if el.tag == "Nozzle":          # nozzle sizes describe connections, not the item itself
            return
        tag_name = (el.get("TagName") or position_number(el)) if el.tag in TAGGED else None
        if el.tag in TAGGED and (not tag_name or re.fullmatch(r"[A-Z]{1,5}", tag_name)):
            tag_name = full_label_tag(el, tag_name) or tag_name
        if owner is not None and tag_name:
            tag_name = None             # a tagged sub-component (actuator, column section) belongs to its owner
        item = owner
        if tag_name:
            tag = parse_tag(tag_name)
            cname = el.get("ComponentName") or ""
            if tag is None:
                out.problems.append(f"'{tag_name}' ({el.get('ComponentClass')}) is not a recognisable tag")
            elif tag.canonical in out.items:
                if seen_names.get(cname) != tag.canonical:
                    out.duplicates.append((tag.canonical, 0, 0))
                item = out.items[tag.canonical]
            else:
                item = ListedItem(tag, 0, el.get("ComponentClass") or el.tag)
                out.items[tag.canonical] = item
                seen_names[cname] = tag.canonical
        if item is not None:
            for ga in el.findall("./GenericAttributes/GenericAttribute"):
                name, value = ga.get("Name") or "", (ga.get("Value") or "").strip()
                if not value or not any(w in name for w in WANTED):
                    continue
                if "NominalDiameter" in name:
                    if "NumericalValue" in name or "Type" in name or "Standard" in name:
                        continue
                    text = value if value.upper().startswith("DN") else f"DN{value}"
                else:
                    unit = UNIT.get(ga.get("Units") or ga.get("Unit") or "", "")
                    if not unit:
                        continue
                    text = f"{value} {unit}"
                for q in parse_quantities(text):
                    if any(q.text == o.text for o in item.specs.get(q.kind, [])):
                        continue
                    item.specs.setdefault(q.kind, []).append(q)
                    item.columns.setdefault(q.kind, name)
        for child in el:
            if isinstance(child.tag, str) and child.tag not in ("GenericAttributes", "Presentation", "Extent",
                                                                  "Position", "Label"):
                visit(child, item)

    visit(root, None)
    return out
