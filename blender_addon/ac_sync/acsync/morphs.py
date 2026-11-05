# SPDX-License-Identifier: GPL-3.0-or-later
"""Archicad Morphs as plain geometry, and the Tapir commands to read and write them.

This is the Blender -> Archicad direction (and its way back). It does not use IFC: Tapir reads a
morph's faces directly (``Get3DGeometryOfElements``, world coordinates, a surface on every face)
and writes them with ``CreateMorphs`` / ``ModifyMorphs``. Everything here is independent of
Blender; the Blender side lives in ``morph_engine.py``.

Facts measured against Archicad 29 with Tapir 1.8.0 (see docs/MORPHS.md):

* A morph body is accepted as an arbitrary indexed mesh: n-gons, concave faces and faces with
  holes all survive a round trip.
* ``ModifyMorphs`` with a ``body`` keeps the element (same GUID) and replaces its geometry.
* The "axes" of a morph placement are matrix rows: ``world = origin + (local . xAxis,
  local . yAxis, local . zAxis)``. Identity axes avoid the question, so every write sends
  identity axes and ``local = world - origin``.
* A face without an explicit surface does *not* take the morph's default surface; it comes back
  as Archicad's default. Every face therefore gets its own surface.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from .errors import TapirCommandError
from .tapir import TapirClient

Vec = tuple[float, float, float]
MIN_TAPIR_VERSION = (1, 8, 0)  # first Tapir with Get3DGeometryOfElements
CHUNK = 50  # elements per request when reading
IDENTITY_AXES = {
    "xAxis": {"x": 1.0, "y": 0.0, "z": 0.0},
    "yAxis": {"x": 0.0, "y": 1.0, "z": 0.0},
    "zAxis": {"x": 0.0, "y": 0.0, "z": 1.0},
}


@dataclass
class MorphFace:
    loop: list[int]  # vertex indices, counter-clockwise seen from outside
    holes: list[list[int]] = field(default_factory=list)  # clockwise
    surface: str = ""  # Archicad surface GUID; empty = leave it to Archicad


@dataclass
class MorphGeometry:
    """A morph's faces in world coordinates (metres, Archicad's project coordinates)."""

    vertices: list[Vec]
    faces: list[MorphFace]

    def fingerprint(self) -> str:
        """Short hash of the geometry and surfaces; equal geometry gives an equal hash."""
        h = hashlib.sha1()
        for x, y, z in self.vertices:
            h.update(f"{x + 0.0:.6f},{y + 0.0:.6f},{z + 0.0:.6f};".encode())
        for f in self.faces:
            holes = "|".join(",".join(map(str, hole)) for hole in f.holes)
            h.update(f"F{','.join(map(str, f.loop))}/{holes}/{f.surface.upper()};".encode())
        return h.hexdigest()[:16]

    def surface_guids(self) -> set[str]:
        return {f.surface.upper() for f in self.faces if f.surface}

    def is_closed(self) -> bool:
        """True for a watertight volume: every edge borders exactly two faces."""
        uses: dict[tuple[int, int], int] = {}
        for f in self.faces:
            for ring in [f.loop, *f.holes]:
                for a, b in zip(ring, ring[1:] + ring[:1]):
                    key = (a, b) if a < b else (b, a)
                    uses[key] = uses.get(key, 0) + 1
        return bool(uses) and all(n == 2 for n in uses.values())


@dataclass(frozen=True)
class MorphDetails:
    element_id: str  # the label shown in Archicad, e.g. "MORPH-001"
    origin: Vec  # placement origin, world


@dataclass(frozen=True)
class SurfaceInfo:
    guid: str
    name: str
    color: tuple[float, float, float] = (0.8, 0.8, 0.8)
    transparency: float = 0.0  # 0 (opaque) .. 1


def parse_geometry(entry: dict) -> MorphGeometry:
    """One item of the ``geometries3D`` answer -> geometry (several bodies are merged)."""
    vertices: list[Vec] = []
    faces: list[MorphFace] = []
    for body in entry.get("bodies", []):
        base = len(vertices)
        vertices += [(v["x"], v["y"], v["z"]) for v in body.get("vertices", [])]
        for poly in body.get("polygons", []):
            faces.append(
                MorphFace(
                    loop=[base + i for i in poly["vertexIds"]],
                    holes=[[base + i for i in h["vertexIds"]] for h in poly.get("holes") or []],
                    surface=((poly.get("surfaceId") or {}).get("guid") or "").upper(),
                )
            )
    return MorphGeometry(vertices, faces)


def morph_body(geometry: MorphGeometry, origin: Vec) -> dict:
    """The ``body`` of CreateMorphs/ModifyMorphs: vertices relative to ``origin``."""
    ox, oy, oz = origin
    polygons = []
    for f in geometry.faces:
        poly: dict = {"vertexIds": list(f.loop)}
        if f.holes:
            poly["holes"] = [{"vertexIds": list(h)} for h in f.holes]
        if f.surface:
            poly["surfaceId"] = {"guid": f.surface}
        polygons.append(poly)
    return {
        # Archicad checks a Solid for a closed volume, so an open mesh must say it is a Surface.
        "bodyType": "Solid" if geometry.is_closed() else "Surface",
        "vertices": [{"x": x - ox, "y": y - oy, "z": z - oz} for x, y, z in geometry.vertices],
        "polygons": polygons,
    }


def polygon_normal(points: Sequence[Vec]) -> Vec:
    """Unit normal of a polygon (Newell's method), counter-clockwise = pointing at the viewer.
    (0, 0, 0) for a degenerate face."""
    nx = ny = nz = 0.0
    n = len(points)
    for i in range(n):
        x1, y1, z1 = points[i]
        x2, y2, z2 = points[(i + 1) % n]
        nx += (y1 - y2) * (z1 + z2)
        ny += (z1 - z2) * (x1 + x2)
        nz += (x1 - x2) * (y1 + y2)
    length = (nx * nx + ny * ny + nz * nz) ** 0.5
    if length < 1e-12:
        return (0.0, 0.0, 0.0)
    return (nx / length, ny / length, nz / length)


def planar_deviation(points: Sequence[Vec]) -> float:
    """Largest distance of the points from the face's best plane, in metres.

    0 for a triangle or any flat face. Archicad builds faces as given, so a clearly warped quad
    has to be triangulated by the caller first.
    """
    n = len(points)
    if n <= 3:
        return 0.0
    nx, ny, nz = polygon_normal(points)
    if (nx, ny, nz) == (0.0, 0.0, 0.0):
        return 0.0  # degenerate (zero-area) face; nothing sensible to measure
    cx = sum(p[0] for p in points) / n
    cy = sum(p[1] for p in points) / n
    cz = sum(p[2] for p in points) / n
    return max(abs((p[0] - cx) * nx + (p[1] - cy) * ny + (p[2] - cz) * nz) for p in points)


def _guid_items(guids: Iterable[str]) -> list[dict]:
    return [{"elementId": {"guid": g}} for g in guids]


def _chunks(items: Sequence, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


class MorphApi:
    """The Tapir commands the morph sync needs. Every call is one Archicad request."""

    def __init__(self, client: TapirClient):
        self.client = client

    # -- reading -----------------------------------------------------------------------------

    def list_guids(self) -> list[str]:
        response = self.client.addon("GetElementsByType", {"elementType": "Morph"})
        return [e["elementId"]["guid"].upper() for e in response.get("elements", [])]

    def selected_morphs(self) -> list[str]:
        """Morphs among the elements currently selected in Archicad."""
        selected = {g.upper() for g in self.client.get_selection()}
        return [g for g in self.list_guids() if g in selected]

    def read(self, guids: Sequence[str]) -> dict[str, MorphGeometry]:
        found: dict[str, MorphGeometry] = {}
        for chunk in _chunks(list(guids), CHUNK):
            response = self.client.addon("Get3DGeometryOfElements", {"elements": _guid_items(chunk)})
            entries = response.get("geometries3D", [])
            if len(entries) != len(chunk):
                raise TapirCommandError(
                    f"Get3DGeometryOfElements: asked for {len(chunk)} elements, got {len(entries)}"
                )
            for guid, entry in zip(chunk, entries):
                found[guid.upper()] = parse_geometry(entry)
        return found

    def details(self, guids: Sequence[str]) -> dict[str, MorphDetails]:
        """Each morph's Element ID and placement origin (what its local vertices are relative to)."""
        found: dict[str, MorphDetails] = {}
        for chunk in _chunks(list(guids), CHUNK):
            response = self.client.addon("GetDetailsOfElements", {"elements": _guid_items(chunk)})
            for guid, item in zip(chunk, response.get("detailsOfElements", [])):
                o = (item.get("details") or {}).get("origin") or {}
                found[guid.upper()] = MorphDetails(
                    item.get("id", ""), (o.get("x", 0.0), o.get("y", 0.0), o.get("z", 0.0))
                )
        return found

    def surface_names(self) -> dict[str, str]:
        """GUID -> name of every surface in the project."""
        response = self.client.addon("GetAttributesByType", {"attributeType": "Surface"})
        return {
            a["attributeId"]["guid"].upper(): a.get("name", "")
            for a in response.get("attributes", [])
        }

    def surface_details(self, guids: Iterable[str]) -> dict[str, SurfaceInfo]:
        wanted = list(dict.fromkeys(g.upper() for g in guids))
        found: dict[str, SurfaceInfo] = {}
        for chunk in _chunks(wanted, CHUNK):
            response = self.client.addon(
                "GetSurfaces",
                {"attributeIds": [{"attributeId": {"guid": g}} for g in chunk]},
            )
            for guid, s in zip(chunk, response.get("surfaces", [])):
                c = s.get("surfaceColor") or {}
                found[guid] = SurfaceInfo(
                    guid,
                    s.get("name", ""),
                    (c.get("red", 0.8), c.get("green", 0.8), c.get("blue", 0.8)),
                    min(1.0, max(0.0, float(s.get("transparency", 0)) / 100.0)),
                )
        return found

    # -- writing -----------------------------------------------------------------------------

    def create(self, items: Sequence[tuple[MorphGeometry, Vec]]) -> list[str | Exception]:
        """Create one morph per ``(geometry, origin)``. Returns the new GUIDs, in order; an item
        that Archicad refused comes back as the exception instead (the others still exist)."""
        if not items:
            return []
        try:
            return self._create_batch(items)
        except TapirCommandError:
            pass  # the whole request failed: retry one by one to find the culprit
        results: list[str | Exception] = []
        for item in items:
            try:
                results += self._create_batch([item])
            except TapirCommandError as exc:
                results.append(exc)
        return results

    def _create_batch(self, items: Sequence[tuple[MorphGeometry, Vec]]) -> list[str | Exception]:
        data = [
            {
                "basePoint": {"x": o[0], "y": o[1], "z": o[2]},
                **IDENTITY_AXES,
                "body": morph_body(geometry, o),
            }
            for geometry, o in items
        ]
        response = self.client.addon("CreateMorphs", {"morphsData": data})
        elements = response.get("elements", [])
        if len(elements) != len(items):
            raise TapirCommandError(f"CreateMorphs made {len(elements)} of {len(items)} morphs")
        return [
            e["elementId"]["guid"].upper()
            if "elementId" in e
            else TapirCommandError((e.get("error") or {}).get("message") or "Archicad refused")
            for e in elements
        ]

    def modify(self, items: Sequence[tuple[str, MorphGeometry, Vec]]) -> list[Exception | None]:
        """Replace the geometry of existing morphs (same elements, same GUIDs).

        ``items`` are ``(guid, geometry, origin)`` where ``origin`` is the morph's current
        placement origin (see :meth:`origins`). Returns None per success, else the failure."""
        if not items:
            return []
        data = [
            {"elementId": {"guid": guid}, **IDENTITY_AXES, "body": morph_body(geometry, origin)}
            for guid, geometry, origin in items
        ]
        response = self.client.addon("ModifyMorphs", {"morphsWithDetails": data})
        results = response.get("executionResults", [])
        if len(results) != len(items):
            raise TapirCommandError(f"ModifyMorphs answered {len(results)} of {len(items)} items")
        return [
            None
            if r.get("success")
            else TapirCommandError(((r.get("error") or {}).get("message")) or "Archicad refused")
            for r in results
        ]
