"""Completeness check: did everything Archicad has actually arrive in the IFC?

The IFC export silently leaves elements out for reasons the user may not know: hidden layers
or a Renovation filter, building materials without the "Participates in Collision Detection"
flag, structure display settings, ... This module asks Archicad (through Tapir) for the GUIDs
of all 3D element types, compares them with the GUIDs found in the IFC, and explains what is
missing: which elements, on which layers, and which of Archicad's visibility filters hides them.

The tool reports *what* is missing and which of Archicad's visibility filters hides it; it
deliberately does not guess a cause for elements that are visible but still not exported (on
the test model, a collision-detection flag was ruled out for such walls).

Types that cannot be compared by GUID are skipped: Mesh (all meshes are merged into one
IfcSite), Zone (IfcSpace carries no GUID), Opening (voids).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .errors import AcSyncError
from .ifc_reader import IfcModel
from .tapir import TapirClient

CHECKED_TYPES = (
    "Wall", "Column", "Beam", "Window", "Door", "Object", "Lamp", "Slab", "Roof",
    "CurtainWall", "Shell", "Skylight", "Morph", "Stair", "Railing",
)  # fmt: skip

# Scopes for which "everything in Archicad" is the right comparison base.
CHECKED_SCOPES = ("EntireProject", "VisibleElementsOnAllStories")

# Archicad element filters that can hide an element from an export, with a user-facing reason.
_VISIBILITY_FILTERS = (
    ("IsVisibleByLayer", "on a hidden layer"),
    ("IsVisibleByRenovation", "hidden by the Renovation filter"),
    ("IsVisibleByStructureDisplay", "hidden by Structure Display"),
)

MAX_DETAILED = 40  # resolve Element IDs / layers for at most this many missing elements


@dataclass(frozen=True)
class MissingElement:
    guid: str
    archicad_type: str
    element_id: str = ""
    layer: str = ""
    reasons: tuple[str, ...] = ()


@dataclass
class Completeness:
    checked: bool = False
    note: str = ""  # why the check was skipped / what went wrong
    expected: int = 0  # elements Archicad has (of the checked types)
    missing: list[MissingElement] = field(default_factory=list)
    missing_by_type: dict[str, int] = field(default_factory=dict)

    @property
    def unexplained(self) -> list[MissingElement]:
        """Missing although visible in Archicad: the ones worth a warning."""
        return [m for m in self.missing if not m.reasons]

    @property
    def hidden(self) -> list[MissingElement]:
        """Missing because Archicad itself hides them (hidden layer, Renovation filter, ...)."""
        return [m for m in self.missing if m.reasons]

    @property
    def ok(self) -> bool:
        """True when nothing unexpected is missing (hidden elements are expected to be absent)."""
        return self.checked and not self.unexplained

    def summary(self) -> str:
        """One line for the panel."""
        if not self.checked:
            return ""
        if not self.missing:
            return f"All {self.expected} Archicad elements arrived."
        if not self.unexplained:
            return (
                f"{len(self.hidden)} hidden element(s) in Archicad were not exported (expected); "
                "everything visible arrived."
            )
        text = (
            f"{len(self.unexplained)} visible element(s) in Archicad were NOT exported by "
            f"Archicad's IFC export."
        )
        if self.hidden:
            text += f" ({len(self.hidden)} hidden ones are also absent, as expected.)"
        return text

    def lines(self) -> list[str]:
        """Detailed lines for the report."""
        if not self.checked:
            return [self.note] if self.note else []
        if not self.missing:
            return [self.summary()]
        by_type = ", ".join(f"{n} {t}" for t, n in sorted(self.missing_by_type.items()))
        out = [f"{self.summary()} Missing in total: {len(self.missing)} of {self.expected} ({by_type})."]
        layers = Counter(m.layer for m in self.missing if m.layer)
        if layers:
            out.append("Layers: " + ", ".join(f"{name} ({n})" for name, n in layers.most_common()))
        reasons = Counter(r for m in self.missing for r in m.reasons)
        if reasons:
            out.append("Hidden in Archicad: " + ", ".join(f"{r} ({n})" for r, n in reasons.items()))
        unexplained = [m for m in self.missing if not m.reasons]
        if unexplained:
            out.append(
                f"{len(unexplained)} are visible in Archicad but Archicad's IFC export skipped "
                "them anyway. Cause not known. Things to check: 'Participates in Collision "
                "Detection' on the element's Building Material, very small or degenerate "
                "geometry, elements fully trimmed by Solid Element Operations, and the "
                "translator's filters."
            )
        shown = self.missing[:12]
        out.append(
            "Examples: "
            + ", ".join(f"{m.element_id or m.guid[:8]} [{m.archicad_type}]" for m in shown)
            + (" ..." if len(self.missing) > len(shown) else "")
        )
        return out


def check_completeness(client: TapirClient, model: IfcModel, scope: str) -> Completeness:
    """Compare Archicad's elements with ``model``. Never raises for Archicad-side problems."""
    if scope not in CHECKED_SCOPES:
        return Completeness(
            note="Completeness check skipped (only done for 'Entire project' and "
            "'Visible, all stories')."
        )
    try:
        return _check(client, model)
    except AcSyncError as exc:
        return Completeness(note=f"Completeness check failed: {exc}")


def _guids(client: TapirClient, element_type: str, filters: list[str] | None = None) -> list[str]:
    params: dict = {"elementType": element_type}
    if filters:
        params["filters"] = filters
    response = client.addon("GetElementsByType", params)
    return [e["elementId"]["guid"].upper() for e in response.get("elements", [])]


def _check(client: TapirClient, model: IfcModel) -> Completeness:
    in_ifc = {g.upper() for g in model.elements if not g.startswith("ifc:")}
    result = Completeness(checked=True)
    missing_raw: list[tuple[str, str]] = []  # (guid, archicad type)
    for element_type in CHECKED_TYPES:
        guids = _guids(client, element_type)
        result.expected += len(guids)
        absent = [g for g in guids if g not in in_ifc]
        if absent:
            result.missing_by_type[element_type] = len(absent)
            missing_raw.extend((g, element_type) for g in absent)
    if not missing_raw:
        return result

    # Why are they hidden in Archicad? Ask the same question with each visibility filter.
    visible: dict[str, set[str]] = {name: set() for name, _ in _VISIBILITY_FILTERS}
    for element_type in result.missing_by_type:
        for name, _ in _VISIBILITY_FILTERS:
            visible[name].update(_guids(client, element_type, [name]))

    detailed = missing_raw[:MAX_DETAILED]
    ids = _element_ids(client, [g for g, _ in detailed])
    layers = _layer_names(client, [g for g, _ in detailed])
    for index, (guid, element_type) in enumerate(missing_raw):
        reasons = tuple(text for name, text in _VISIBILITY_FILTERS if guid not in visible[name])
        in_detail = index < MAX_DETAILED
        result.missing.append(
            MissingElement(
                guid=guid,
                archicad_type=element_type,
                element_id=ids.get(guid, "") if in_detail else "",
                layer=layers.get(guid, "") if in_detail else "",
                reasons=reasons,
            )
        )
    return result


def _element_ids(client: TapirClient, guids: list[str]) -> dict[str, str]:
    try:
        prop = client.request(
            "API.GetPropertyIds",
            {"properties": [{"type": "BuiltIn", "nonLocalizedName": "General_ElementID"}]},
        )["properties"][0]["propertyId"]
        values = client.request(
            "API.GetPropertyValuesOfElements",
            {
                "elements": [{"elementId": {"guid": g}} for g in guids],
                "properties": [{"propertyId": prop}],
            },
        )["propertyValuesForElements"]
    except (AcSyncError, KeyError, IndexError):
        return {}
    out = {}
    for guid, entry in zip(guids, values, strict=False):
        try:
            out[guid] = str(entry["propertyValues"][0]["propertyValue"]["value"])
        except (KeyError, IndexError, TypeError):
            continue
    return out


def _layer_names(client: TapirClient, guids: list[str]) -> dict[str, str]:
    try:
        details = client.addon(
            "GetDetailsOfElements",
            {"elements": [{"elementId": {"guid": g}} for g in guids], "fields": ["layerIndex"]},
        )["detailsOfElements"]
        attributes = client.addon("GetAttributesByType", {"attributeType": "Layer"})["attributes"]
    except (AcSyncError, KeyError):
        return {}
    names = {a["index"]: a["name"] for a in attributes}
    out = {}
    for guid, entry in zip(guids, details, strict=False):
        index = entry.get("layerIndex")
        if index in names:
            out[guid] = names[index]
    return out
