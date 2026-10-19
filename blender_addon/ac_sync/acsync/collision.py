"""Building materials that Archicad's IFC export skips: "Participates in Collision Detection" off.

The usual IFC translators have "Export geometries that participate in Collision Detection
only" switched on, so every element whose Building Material lacks that flag silently disappears
from the export. Archicad switches the flag off for some materials by default (ground and
context, air gaps), and nobody looks there. This module

* finds the materials that are responsible: those with the flag off that explain a missing
  element, or a Mesh that did not reach the IFC (``find_excluded``), and
* can switch the flag on for the length of one export and put the original settings back
  (``MaterialFlagSwitch``), with a recovery file in case the tool dies in between.

Materials are only ever touched when they explain something that is missing, so air gaps and
other materials that should stay excluded keep their flag.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Self

from .errors import AcSyncError, TapirCommandError
from .ifc_reader import IfcModel
from .tapir import TapirClient

MATERIAL_PROPERTY = "BuildingMaterial_Name"  # a component property: one value per skin/part
LOOKUP_CHUNK = 50  # elements per Archicad request
MAX_LOOKUP = 400  # at most this many missing elements are traced back to their materials
SITE_TOLERANCE = 0.05  # metres: how far an outline point may be from an IFC vertex
SITE_MIN_MATCH = 0.8  # share of sampled outline points that must be found to call a mesh present
SAMPLES = 12


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


# -- finding the culprits ----------------------------------------------------------------------


def excluded_building_materials(client: TapirClient) -> dict[str, str]:
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


def element_materials(client: TapirClient, guids: list[str]) -> dict[str, set[str]]:
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


def site_points(model: IfcModel) -> list[tuple[float, float]]:
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
    step = max(1, len(outline) // SAMPLES)
    sample = outline[::step]
    hits = sum(1 for x, y in sample if index.near(x, y))
    return hits / len(sample) >= SITE_MIN_MATCH


def _meshes_not_exported(client: TapirClient, model: IfcModel, excluded) -> list[tuple[str, ...]]:
    """Material names of the visible Meshes that use only excluded materials and whose outline
    cannot be found in the IFC Site."""
    response = client.addon("GetElementsByType", {"elementType": "Mesh", "filters": ["IsVisibleByLayer"]})
    guids = [e["elementId"]["guid"].upper() for e in response.get("elements", [])]
    if not guids:
        return []
    materials = element_materials(client, guids)
    suspects = [g for g in guids if _explained_by(materials[g], excluded)]
    if not suspects:
        return []
    details = client.addon(
        "GetDetailsOfElements",
        {"elements": [{"elementId": {"guid": g}} for g in suspects], "fields": ["details"]},
    )["detailsOfElements"]
    index = _PointIndex(site_points(model))
    missing = []
    for guid, entry in zip(suspects, details, strict=False):
        polygon = (entry.get("details") or {}).get("polygonCoordinates") or []
        outline = [(p["x"], p["y"]) for p in polygon]
        if not _outline_present(index, outline):
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
    excluded = excluded_building_materials(client)
    if not excluded:
        return Findings()
    by_element: dict[str, tuple[str, ...]] = {}
    per_element, per_mesh = Counter(), Counter()
    candidates = candidates[:MAX_LOOKUP]
    if candidates:
        for guid, names in element_materials(client, candidates).items():
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


# -- switching the flag on for one export ------------------------------------------------------


def recovery_path() -> str:
    return os.path.join(tempfile.gettempdir(), "acsync", "pending-restore.json")


def _read_materials(client: TapirClient, guids: list[str]) -> list[dict]:
    response = client.addon(
        "GetBuildingMaterials", {"attributeIds": [{"attributeId": {"guid": g}} for g in guids]}
    )
    return response["buildingMaterials"]


def _write_collision_flag(client: TapirClient, material: dict, value: bool) -> None:
    """Overwrite a Building Material with its own full settings and one changed flag (an
    overwrite may reset the fields that are left out)."""
    item = {k: v for k, v in material.items() if v is not None and k != "index"}
    item["collisionDetection"] = value
    response = client.addon(
        "CreateBuildingMaterials", {"overwriteExisting": True, "buildingMaterialDataArray": [item]}
    )
    for entry in response.get("attributeIds", []):
        if isinstance(entry, dict) and "error" in entry:
            raise TapirCommandError(
                f"Archicad refused to change '{material.get('name')}': {entry['error']}"
            )


def _differences(before: dict, after: dict) -> list[str]:
    return sorted(k for k in set(before) | set(after) if k != "index" and before.get(k) != after.get(k))


def _restore(client: TapirClient, originals: list[dict]) -> list[str]:
    """Put ``originals`` back and check them. Returns problems (empty when all is as before)."""
    problems = []
    for original in originals:
        name = original.get("name", "?")
        try:
            current = _read_materials(client, [original["attributeId"]["guid"]])[0]
            if _differences(original, current):
                _write_collision_flag(client, original, original.get("collisionDetection", True))
                current = _read_materials(client, [original["attributeId"]["guid"]])[0]
            diff = _differences(original, current)
            if diff:
                problems.append(f"'{name}' differs from before in: {', '.join(diff)}")
        except (AcSyncError, KeyError, IndexError) as exc:
            problems.append(f"'{name}' could not be restored ({exc})")
    return problems


class MaterialFlagSwitch:
    """Context manager: collision detection is on for ``materials`` inside the block, and the
    original settings are back afterwards (also when the block fails).

    Before anything is changed the originals are written to a recovery file; it is deleted once
    everything is verified. ``warnings`` collects what went wrong while restoring."""

    def __init__(
        self, client: TapirClient, materials: list[ExcludedMaterial],
        project_path: str = "", project_name: str = "",
    ):
        self.client = client
        self.materials = materials
        self.project_path = project_path
        self.project_name = project_name
        self.originals: list[dict] = []
        self.warnings: list[str] = []

    def __enter__(self) -> Self:
        self.originals = _read_materials(self.client, [m.guid for m in self.materials])
        self.originals = [m for m in self.originals if m.get("collisionDetection") is False]
        if not self.originals:
            return self
        self._save_recovery()
        try:
            for original in self.originals:
                _write_collision_flag(self.client, original, True)
            now = _read_materials(self.client, [m["attributeId"]["guid"] for m in self.originals])
            if not all(m.get("collisionDetection") is True for m in now):
                raise TapirCommandError("Archicad did not accept the change of the material flag.")
        except Exception:
            self._finish()
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._finish()
        return False

    def _finish(self) -> None:
        if not self.originals:
            return
        problems = _restore(self.client, self.originals)
        if problems:
            self.warnings.append(
                "Could not fully restore the Building Materials changed for the export: "
                + "; ".join(problems) + ". Check 'Participates in Collision Detection' in Archicad."
            )
        else:
            self._delete_recovery()

    def _save_recovery(self) -> None:
        path = recovery_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {
            "project_path": self.project_path,
            "project_name": self.project_name,
            "saved": time.strftime("%Y-%m-%d %H:%M:%S"),
            "materials": self.originals,
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)

    @staticmethod
    def _delete_recovery() -> None:
        try:
            os.remove(recovery_path())
        except OSError:
            pass


def reexport_including(
    client: TapirClient,
    materials: list[ExcludedMaterial],
    project: tuple[str, str],
    export,
    read,
    check,
    progress=lambda text: None,
):
    """Export again with collision detection on for ``materials``, then restore them.

    ``export()`` writes the IFC, ``read()`` loads it and ``check(model)`` runs the completeness
    check; all three run while the flag is on. ``project`` is (path, name) of the open project.
    Returns (model, check result, restore warnings)."""
    names = ", ".join(m.name for m in materials)
    progress(f"Switching on collision detection for: {names}")
    with MaterialFlagSwitch(client, materials, *project) as switch:
        progress("Archicad is exporting again (including those materials)...")
        export()
        progress("Reading geometry...")
        model = read()
        progress("Checking completeness...")
        result = check(model)
    return model, result, switch.warnings


def restore_pending(client: TapirClient, project_path: str) -> list[str]:
    """Undo a switch that was interrupted (crash, Blender closed during an export). Only acts
    when the recovery file belongs to the project that is open now. Returns messages."""
    path = recovery_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    if data.get("project_path") != project_path:
        return []
    originals = data.get("materials") or []
    problems = _restore(client, originals)
    if problems:
        return [
            "An earlier sync was interrupted and some Building Materials could not be put back: "
            + "; ".join(problems)
        ]
    MaterialFlagSwitch._delete_recovery()
    names = ", ".join(f"'{m.get('name')}'" for m in originals)
    return [f"An earlier sync was interrupted; restored the Collision Detection setting of {names}."]
