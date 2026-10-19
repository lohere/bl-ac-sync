# SPDX-License-Identifier: GPL-3.0-or-later
"""Building materials that Archicad's IFC export skips: "Participates in Collision Detection" off.

The usual IFC translators have "Export geometries that participate in Collision Detection
only" switched on, so every element whose Building Material lacks that flag silently disappears
from the export. Archicad switches the flag off for some materials by default (ground and
context, air gaps), and nobody looks there. ``find_excluded`` finds the materials that are
responsible: those with the flag off that explain a missing element, or a Mesh that did not
reach the IFC. Materials that do not explain anything missing (an air gap in a wall that did
arrive) are not named. Switching the flag on for an export is flagswitch.py.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .ifc_reader import IfcModel
from .tapir import TapirClient

MATERIAL_PROPERTY = "BuildingMaterial_Name"  # a component property: one value per skin/part
LOOKUP_CHUNK = 50  # elements per Archicad request
MAX_LOOKUP = 400  # at most this many missing elements are traced back to their materials
SITE_TOLERANCE = 0.05  # metres: how far an outline point may be from an IFC vertex
SITE_MIN_MATCH = 0.8  # share of sampled outline points that must be found to call a mesh present
OUTLINE_SAMPLES = 12  # outline points compared per mesh


@dataclass(frozen=True)
class ExcludedMaterial:
    name: str
    guid: str
    elements: int = 0  # missing elements this material explains
    meshes: int = 0  # Mesh elements (terrain) that did not arrive in the IFC's Site


@dataclass
class Findings:
    materials: list[ExcludedMaterial] = field(default_factory=list)
    by_element: dict[str, tuple[str, ...]] = field(default_factory=dict)  # guid -> material names


def _excluded_building_materials(client: TapirClient) -> dict[str, str]:
    """name -> GUID of every Building Material whose collision-detection flag is off."""
    attributes = client.addon("GetAttributesByType", {"attributeType": "BuildingMaterial"})
    ids = [{"attributeId": a["attributeId"]} for a in attributes.get("attributes", [])]
    if not ids:
        return {}
    details = client.addon(
        "GetBuildingMaterials", {"attributeIds": ids, "fields": ["collisionDetection"]}
    )["buildingMaterials"]
    return {
        m["name"]: m["attributeId"]["guid"]
        for m in details
        if m.get("collisionDetection") is False
    }


def _element_materials(client: TapirClient, guids: list[str]) -> dict[str, set[str]]:
    """Building Material names per element, one per component (skin). An empty string means
    a component whose material could not be read."""
    prop = client.request(
        "API.GetPropertyIds",
        {"properties": [{"type": "BuiltIn", "nonLocalizedName": MATERIAL_PROPERTY}]},
    )["properties"][0]["propertyId"]
    found: dict[str, set[str]] = {g: set() for g in guids}
    for start in range(0, len(guids), LOOKUP_CHUNK):
        chunk = guids[start:start + LOOKUP_CHUNK]
        components = client.request(
            "API.GetComponentsOfElements",
            {"elements": [{"elementId": {"guid": g}} for g in chunk]},
        )["componentsOfElements"]
        owners, ids = [], []
        for guid, entry in zip(chunk, components, strict=False):
            for comp in entry.get("elementComponents", []):
                owners.append(guid)
                ids.append({"elementComponentId": comp["elementComponentId"]})
        if not ids:
            continue
        values = client.request(
            "API.GetPropertyValuesOfElementComponents",
            {"elementComponents": ids, "properties": [{"propertyId": prop}]},
        )["propertyValuesForElementComponents"]
        for guid, entry in zip(owners, values, strict=False):
            try:
                name = entry["propertyValues"][0]["propertyValue"]["value"]
            except (KeyError, IndexError, TypeError):
                name = None
            found[guid].add(str(name) if name else "")
    return found


def _explained_by(materials: set[str], excluded) -> bool:
    """True when every component of an element uses an excluded material."""
    return bool(materials) and "" not in materials and materials <= set(excluded)


def _site_points(model: IfcModel) -> list[tuple[float, float]]:
    """Plan positions of every vertex of the IFC Site (Archicad merges all Meshes into it)."""
    points = []
    for element in model.elements.values():
        if element.ifc_type == "IfcSite":
            v = element.vertices
            points += [(v[i], v[i + 1]) for i in range(0, len(v) - 2, 3)]
    return points


class _PointIndex:
    """Grid lookup: is there a point within SITE_TOLERANCE of (x, y)?"""

    def __init__(self, points):
        self.cells: dict[tuple[int, int], list[tuple[float, float]]] = {}
        for x, y in points:
            self.cells.setdefault(self._cell(x, y), []).append((x, y))

    @staticmethod
    def _cell(x, y):
        return int(x // SITE_TOLERANCE), int(y // SITE_TOLERANCE)

    def near(self, x, y) -> bool:
        cx, cy = self._cell(x, y)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for px, py in self.cells.get((cx + dx, cy + dy), ()):
                    if abs(px - x) <= SITE_TOLERANCE and abs(py - y) <= SITE_TOLERANCE:
                        return True
        return False


def _outline_present(index: _PointIndex, outline: list[tuple[float, float]]) -> bool:
    if not outline:
        return True  # nothing to compare: do not raise a false alarm
    sample = outline[::max(1, len(outline) // OUTLINE_SAMPLES)]
    hits = sum(1 for x, y in sample if index.near(x, y))
    return hits / len(sample) >= SITE_MIN_MATCH


def _meshes_not_exported(client: TapirClient, model: IfcModel, excluded) -> list[tuple[str, ...]]:
    """Material names of the visible Meshes that use only excluded materials and whose outline
    cannot be found in the IFC Site."""
    response = client.addon(
        "GetElementsByType", {"elementType": "Mesh", "filters": ["IsVisibleByLayer"]}
    )
    guids = [e["elementId"]["guid"].upper() for e in response.get("elements", [])]
    if not guids:
        return []
    materials = _element_materials(client, guids)
    suspects = [g for g in guids if _explained_by(materials[g], excluded)]
    if not suspects:
        return []
    details = client.addon(
        "GetDetailsOfElements",
        {"elements": [{"elementId": {"guid": g}} for g in suspects], "fields": ["details"]},
    )["detailsOfElements"]
    index = _PointIndex(_site_points(model))
    missing = []
    for guid, entry in zip(suspects, details, strict=False):
        polygon = (entry.get("details") or {}).get("polygonCoordinates") or []
        if not _outline_present(index, [(p["x"], p["y"]) for p in polygon]):
            missing.append(tuple(sorted(materials[guid])))
    return missing


def find_excluded(
    client: TapirClient,
    candidates: list[str],
    model: IfcModel | None = None,
    check_meshes: bool = False,
) -> Findings:
    """Which excluded materials explain the ``candidates`` (GUIDs of elements that are visible
    in Archicad but missing from the IFC) and the Meshes that did not reach the IFC?

    Cheap when there is nothing to find: two requests if no material has the flag off."""
    excluded = _excluded_building_materials(client)
    if not excluded:
        return Findings()
    by_element: dict[str, tuple[str, ...]] = {}
    per_element, per_mesh = Counter(), Counter()
    candidates = candidates[:MAX_LOOKUP]
    if candidates:
        for guid, names in _element_materials(client, candidates).items():
            if _explained_by(names, excluded):
                by_element[guid] = tuple(sorted(names))
                per_element.update(names)
    if check_meshes and model is not None:
        for names in _meshes_not_exported(client, model, excluded):
            per_mesh.update(names)
    materials = [
        ExcludedMaterial(name, excluded[name], per_element[name], per_mesh[name])
        for name in sorted(set(per_element) | set(per_mesh))
    ]
    return Findings(materials, by_element)
