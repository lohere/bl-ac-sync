# SPDX-License-Identifier: GPL-3.0-or-later
"""Walls that inherit the end surface of the adjoining wall.

Archicad's wall setting "inherit end surface" (Tapir: ``inheritEndSurface``, on by default)
gives the exposed end of a wall one uniform finish taken from the wall it joins, for example the
plaster that continues around a corner. The IFC export ignores it: the end faces come out in
thin strips of the wall's own skins (brick, insulation, plaster, ...), so the corner looks cut
open in Blender.

This module reproduces the rule on the model after it has been read. It is an inference from
eight wall ends of one real project compared with what Archicad shows (docs/ARCHICAD_SETUP.md):

* The wall whose end changes needs the setting, and so does the neighbour it takes the finish
  from. Where no neighbour has it, Archicad shows the wall's own layers.
* The neighbours are the walls whose reference line passes through the end point, taken from
  Tapir's connection lists and by position on the same storey (the lists miss, for example, a
  tall wall standing on a plinth wall).
* The finish is that of the neighbour's face that is flush with the end: the face in the same
  plane as the outermost part of the wall end, next to it. A neighbour usually has a different
  finish on each side, and the plane tells which side the end continues. If no face is flush,
  the neighbour's outer finish is used: the surface with the most area among its outward faces
  on its two outermost planes.
* All end faces over the neighbour's footprint get that one surface, so the end is uniform.

Free ends, curved walls and walls with an explicit edge surface are left alone. It needs the
model in Archicad coordinates, because positions are compared with Tapir's.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field, replace

from .ifc_reader import Element, IfcModel, SurfaceStyle, geometry_hash
from .tapir import TapirClient

WALL_TYPES = ("IfcWall", "IfcWallStandardCase")
CHUNK = 60  # elements per Archicad request

# Which faces count (normals are unit vectors, so these are cosines / sines of angles)
UPRIGHT = 0.5  # a face is upright when |normal.z| is below this
END_AXIS_DOT = 0.8  # an end face looks along the wall at least this much
PARALLEL = 0.9  # a neighbour face is parallel to the end plane at least this much
ACROSS = 0.3  # a side face looks across the wall: its normal is at most this much along it
MIN_AREA = 0.02  # m2: smaller neighbour faces are noise as a source of the finish

# Distances in metres
ON_LINE = 0.05  # an end point this close to a neighbour's reference line is on it
TOUCH = 0.15  # an end face lies over a neighbour within this margin (in plan). At an L-corner
# the skins of the two walls are cut against each other, so a brick end face can stop ~10 cm
# short of the neighbour's outline.
PLANE_BIN = 0.005  # end faces this close together are in one plane
FLUSH = 0.02  # a neighbour face this close to the end plane may be flush with it
LAYER = 0.003  # of the faces looking one way, only those this close to the outermost one are
# visible. Each skin is a closed solid, so the covered face of the skin behind it is ~1 cm further
# in.
ADJACENT = 0.1  # a flush face must be this close to the main end faces

# How much end face makes an end plane "substantial": slivers (a centimetre of flashing) do not
# decide where the end is.
MIN_PLANE_AREA = 0.05  # m2 ...
MIN_PLANE_SHARE = 0.05  # ... and this share of all the end faces


@dataclass
class EndSurfaceResult:
    walls: int = 0  # walls that got changed faces
    faces: int = 0  # faces that took the neighbour's surface
    skipped: int = 0  # walls left alone because they have an explicit edge surface
    lines: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Wall:
    """What Archicad says about a wall (positions in plan, metres)."""

    beg: tuple[float, float]
    end: tuple[float, float]
    floor: int | None
    inherit: bool
    curved: bool
    edge_override: bool  # the edge (end) surface is set explicitly

    @property
    def axis(self) -> tuple[float, float] | None:
        length = math.hypot(self.end[0] - self.beg[0], self.end[1] - self.beg[1])
        if length < 1e-9:
            return None
        return (self.end[0] - self.beg[0]) / length, (self.end[1] - self.beg[1]) / length

    def point(self, label: str) -> tuple[float, float]:
        return self.beg if label == "beg" else self.end

    def passes_through(self, point) -> bool:
        """Is ``point`` on this wall's reference line (within ON_LINE)?"""
        dx, dy = self.end[0] - self.beg[0], self.end[1] - self.beg[1]
        length2 = dx * dx + dy * dy
        t = 0.0
        if length2 > 1e-12:
            t = max(0.0, min(1.0, ((point[0] - self.beg[0]) * dx + (point[1] - self.beg[1]) * dy)
                             / length2))
        return math.hypot(point[0] - (self.beg[0] + t * dx),
                          point[1] - (self.beg[1] + t * dy)) <= ON_LINE


@dataclass(frozen=True)
class _Face:
    number: int  # position in the element's face list
    normal: tuple[float, float, float]
    centroid: tuple[float, float, float]
    area: float
    low: tuple[float, float, float]  # corners of the bounding box
    high: tuple[float, float, float]
    material: int


def _faces(element: Element) -> list[_Face]:
    v, pos, out = element.vertices, 0, []
    for number, (size, material) in enumerate(
        zip(element.face_sizes, element.face_materials, strict=False)
    ):
        pts = [(v[3 * i], v[3 * i + 1], v[3 * i + 2])
               for i in element.face_indices[pos:pos + size]]
        pos += size
        nx = ny = nz = 0.0
        for (x1, y1, z1), (x2, y2, z2) in zip(pts, pts[1:] + pts[:1], strict=False):
            nx += (y1 - y2) * (z1 + z2)
            ny += (z1 - z2) * (x1 + x2)
            nz += (x1 - x2) * (y1 + y2)
        magnitude = math.sqrt(nx * nx + ny * ny + nz * nz)
        if magnitude < 1e-12:
            continue
        out.append(_Face(
            number=number,
            normal=(nx / magnitude, ny / magnitude, nz / magnitude),
            centroid=tuple(sum(p[i] for p in pts) / len(pts) for i in range(3)),
            area=magnitude / 2,
            low=tuple(min(p[i] for p in pts) for i in range(3)),
            high=tuple(max(p[i] for p in pts) for i in range(3)),
            material=material,
        ))
    return out


def _dot(a, b) -> float:
    """Dot product of two vectors in plan (extra components are ignored)."""
    return a[0] * b[0] + a[1] * b[1]


def _gap(face: _Face, low, high) -> float:
    """Distance between the face's bounding box and the box low-high."""
    return math.sqrt(sum(
        max(face.low[i] - high[i], low[i] - face.high[i], 0.0) ** 2 for i in range(3)
    ))


# -- asking Archicad ---------------------------------------------------------------------------


def _wall_details(client: TapirClient, guids: list[str]) -> dict[str, _Wall]:
    found = {}
    for start in range(0, len(guids), CHUNK):
        chunk = guids[start:start + CHUNK]
        response = client.addon(
            "GetDetailsOfElements",
            {
                "elements": [{"elementId": {"guid": g}} for g in chunk],
                "fields": ["floorIndex", "details"],
            },
        )["detailsOfElements"]
        for guid, entry in zip(chunk, response, strict=False):
            det = entry.get("details") or {}
            try:
                beg, end = det["begCoordinate"], det["endCoordinate"]
                found[guid] = _Wall(
                    beg=(beg["x"], beg["y"]),
                    end=(end["x"], end["y"]),
                    floor=entry.get("floorIndex"),
                    inherit=bool(det.get("inheritEndSurface", False)),
                    curved=abs(det.get("arcAngle") or 0.0) > 1e-6,
                    edge_override=bool((det.get("sideMaterial") or {}).get("overridden")),
                )
            except (KeyError, TypeError):
                continue
    return found


def _connections(client: TapirClient, guids: list[str]) -> dict[str, dict[str, list[str]]]:
    """Walls joined at the begin / end of each given wall.

    Tapir lists three kinds. ``connectedToBeginPoint`` / ``connectedToEndPoint`` say which end.
    ``connectedWithReferenceLineToEndPoints`` (plural) are walls whose reference line passes
    through one of this wall's end points, i.e. T-junctions: it does not say which end, so they
    come back under "either" and the caller decides by position. ``connectedToReferenceLine``
    and ``crossingReferenceLine`` are walls that end on / cross this wall's side; they do not
    touch its ends and are ignored.
    """
    found = {}
    for start in range(0, len(guids), CHUNK):
        chunk = guids[start:start + CHUNK]
        relations = client.addon(
            "GetRelationsOfElements", {"elements": [{"elementId": {"guid": g}} for g in chunk]}
        )["relations"]
        for guid, entry in zip(chunk, relations, strict=False):
            ends: dict[str, list[str]] = {"beg": [], "end": [], "either": []}
            for key, items in (entry.get("wallConnections") or {}).items():
                if not isinstance(items, list):
                    continue
                if "BeginPoint" in key:
                    side = "beg"
                elif key.endswith("EndPoint"):
                    side = "end"
                elif "ReferenceLine" in key and key.endswith("EndPoints"):
                    side = "either"
                else:
                    continue
                ends[side] += [i["elementId"]["guid"].upper() for i in items if "elementId" in i]
            found[guid] = ends
    return found


# -- the rule ----------------------------------------------------------------------------------


class _Walls:
    """The walls of a model: Archicad's details and connections, the model as it was read (so
    the order in which walls are processed does not matter) and cached geometry."""

    def __init__(self, details: dict[str, _Wall], joined, original: dict[str, Element]):
        self.details = details
        self.joined = joined
        self.original = original
        self._faces: dict[str, list[_Face]] = {}
        self._footprints: dict[str, tuple] = {}

    def faces(self, guid: str) -> list[_Face]:
        if guid not in self._faces:
            self._faces[guid] = _faces(self.original[guid])
        return self._faces[guid]

    def footprint(self, guid: str):
        """Plan bounding box of the wall: ((x0, y0), (x1, y1))."""
        if guid not in self._footprints:
            faces = self.faces(guid)
            self._footprints[guid] = (
                (min(f.low[0] for f in faces), min(f.low[1] for f in faces)),
                (max(f.high[0] for f in faces), max(f.high[1] for f in faces)),
            ) if faces else None
        return self._footprints[guid]

    def lies_over(self, guid: str, point) -> bool:
        footprint = self.footprint(guid)
        if footprint is None:
            return False
        (x0, y0), (x1, y1) = footprint
        return (x0 - TOUCH <= point[0] <= x1 + TOUCH) and (y0 - TOUCH <= point[1] <= y1 + TOUCH)

    def neighbours(self, guid: str, label: str) -> list[str]:
        """Walls with the setting on that join ``guid`` at the given end."""
        wall = self.details[guid]
        point = wall.point(label)
        listed = self.joined.get(guid, {})
        found = list(listed.get(label, []))
        found += [o for o in listed.get("either", [])
                  if o in self.details and self.details[o].passes_through(point)]
        # by position too: Tapir does not list a wall that stands on a plinth wall. Walls on
        # another storey are not joined to this one.
        found += [o for o, d in self.details.items()
                  if o != guid and d.floor == wall.floor and d.passes_through(point)]
        return [o for o in dict.fromkeys(found)
                if o != guid and o in self.details and self.details[o].inherit]

    def outer_finish(self, guid: str) -> tuple[SurfaceStyle, float] | None:
        """The surface covering most area on the two outermost planes of the wall, and that
        area. Inner skins sit on stepped planes behind these."""
        axis = self.details[guid].axis
        if axis is None:
            return None
        element = self.original[guid]
        perp = (-axis[1], axis[0])
        sides = []  # (offset across the wall, +1/-1 for the way the face looks, face)
        for f in self.faces(guid):
            if (abs(f.normal[2]) < UPRIGHT and f.area >= MIN_AREA
                    and abs(_dot(f.normal, axis)) < ACROSS
                    and 0 <= f.material < len(element.surfaces)):
                sides.append((_dot(f.centroid, perp), 1 if _dot(f.normal, perp) > 0 else -1, f))
        if not sides:
            return None
        low = min(s[0] for s in sides)
        high = max(s[0] for s in sides)
        area: dict[int, float] = defaultdict(float)
        for offset, way, f in sides:
            if (way < 0 and offset <= low + LAYER) or (way > 0 and offset >= high - LAYER):
                area[f.material] += f.area
        if not area:
            return None
        material = max(area, key=area.get)
        return element.surfaces[material], area[material]


def _end_faces(walls: _Walls, guid: str, n_end, neighbours: list[str]) -> list[_Face]:
    """The faces at the end of the wall (looking the way it ends) that lie over a neighbour."""
    return [
        f for f in walls.faces(guid)
        if abs(f.normal[2]) < UPRIGHT
        and _dot(f.normal, n_end) >= END_AXIS_DOT
        and any(walls.lies_over(o, f.centroid) for o in neighbours)
    ]


def _outermost_plane(ends: list[_Face], n_end) -> float:
    """Position, along the way the end looks, of the outermost plane with a substantial area of
    end face."""
    planes: dict[int, float] = defaultdict(float)
    for f in ends:
        planes[round(_dot(f.centroid, n_end) / PLANE_BIN)] += f.area
    total = sum(planes.values())
    solid = [k for k, a in planes.items() if a >= max(MIN_PLANE_AREA, MIN_PLANE_SHARE * total)]
    return max(solid or planes) * PLANE_BIN


def _flush_finish(
    walls: _Walls, neighbours: list[str], n_end, plane: float, main: list[_Face]
) -> SurfaceStyle | None:
    """The finish of the neighbours' faces in the end plane, next to the main end faces.

    The faces look the same way as the end (the end continues the neighbour's face) or the
    opposite way (the end butts against it: a T-junction). Of the faces looking one way only the
    outermost layer is visible; the first way that has faces decides."""
    low = tuple(min(f.low[i] for f in main) for i in range(3))
    high = tuple(max(f.high[i] for f in main) for i in range(3))
    near: dict[int, list[tuple[float, float, SurfaceStyle]]] = {1: [], -1: []}  # depth, area, style
    for other in neighbours:
        surfaces = walls.original[other].surfaces
        for g in walls.faces(other):
            facing = _dot(g.normal, n_end)
            if (abs(g.normal[2]) < UPRIGHT and g.area >= MIN_AREA and abs(facing) >= PARALLEL
                    and abs(_dot(g.centroid, n_end) - plane) <= FLUSH
                    and 0 <= g.material < len(surfaces)
                    and _gap(g, low, high) <= ADJACENT):
                depth = _dot(g.centroid, g.normal)  # how far out the face is, the way it looks
                near[1 if facing > 0 else -1].append((depth, g.area, surfaces[g.material]))
    for faces in (near[1], near[-1]):
        if faces:
            top = max(depth for depth, _area, _style in faces)
            visible: dict[SurfaceStyle, float] = defaultdict(float)
            for depth, area, style in faces:
                if depth >= top - LAYER:
                    visible[style] += area
            return max(visible, key=visible.get)
    return None


def _outer_finish_of(walls: _Walls, neighbours: list[str]) -> SurfaceStyle | None:
    """The outer finish of the neighbours, the one with most outer area if they differ."""
    area: dict[SurfaceStyle, float] = defaultdict(float)
    for other in neighbours:
        finish = walls.outer_finish(other)
        if finish:
            area[finish[0]] += finish[1]
    return max(area, key=area.get) if area else None


def _inherited(
    walls: _Walls, guid: str, label: str, n_end
) -> tuple[SurfaceStyle, list[_Face]] | None:
    """(surface, end faces that take it) for one end of a wall, or None."""
    neighbours = walls.neighbours(guid, label)
    ends = _end_faces(walls, guid, n_end, neighbours) if neighbours else []
    if not ends:
        return None
    plane = _outermost_plane(ends, n_end)
    main = [f for f in ends if abs(_dot(f.centroid, n_end) - plane) <= PLANE_BIN]
    style = _flush_finish(walls, neighbours, n_end, plane, main) \
        or _outer_finish_of(walls, neighbours)
    return (style, ends) if style else None


def apply_inherited_end_surfaces(client: TapirClient, model: IfcModel) -> EndSurfaceResult:
    """Change ``model`` in place as described in the module docstring."""
    result = EndSurfaceResult()
    keys = {g.upper(): g for g, e in model.elements.items()
            if e.ifc_type in WALL_TYPES and not g.startswith("ifc:")}
    details = {g: d for g, d in _wall_details(client, list(keys)).items() if g in keys}
    flagged = [g for g, d in details.items() if d.inherit and not d.curved]
    # An explicit edge surface is the user's choice for the wall ends; whether Archicad lets the
    # neighbour override it is not known, so such walls keep what the export gave them.
    result.skipped = sum(1 for g in flagged if details[g].edge_override)
    flagged = [g for g in flagged if not details[g].edge_override]
    if flagged:
        walls = _Walls(
            details, _connections(client, flagged), {g: model.elements[k] for g, k in keys.items()}
        )
        for guid in flagged:
            _apply_to_wall(walls, guid, model, keys[guid], result)
    return _summarize(result)


def _apply_to_wall(walls: _Walls, guid: str, model: IfcModel, key: str, result) -> None:
    axis = walls.details[guid].axis
    if axis is None:
        return
    element = walls.original[guid]
    surfaces = list(element.surfaces)
    materials = list(element.face_materials)
    changed = 0
    for label, sign in (("beg", -1), ("end", 1)):
        found = _inherited(walls, guid, label, (axis[0] * sign, axis[1] * sign))
        if found is None:
            continue
        style, end_faces = found
        if style not in surfaces:
            surfaces.append(style)
        index = surfaces.index(style)
        for face in end_faces:
            if materials[face.number] != index:
                materials[face.number] = index
                changed += 1
    if changed:
        model.elements[key] = _with_surfaces(element, surfaces, materials)
        result.walls += 1
        result.faces += changed


def _summarize(result: EndSurfaceResult) -> EndSurfaceResult:
    if result.faces:
        result.lines.append(
            f"Wall ends: {result.faces} end face(s) on {result.walls} wall(s) took the surface of "
            "the adjoining wall (Archicad's 'inherit end surface')."
        )
    if result.skipped:
        result.lines.append(
            f"Wall ends: {result.skipped} wall(s) with an explicit edge surface were left as "
            "exported."
        )
    return result


def _with_surfaces(element: Element, surfaces: list[SurfaceStyle], materials: list[int]) -> Element:
    """The element with new per-face surfaces: unused ones dropped, hash recomputed."""
    used = sorted({m for m in materials if m >= 0})
    remap = {old: new for new, old in enumerate(used)}
    kept = tuple(surfaces[i] for i in used)
    face_materials = tuple(remap.get(m, -1) for m in materials)
    names = [kept[m].name if 0 <= m < len(kept) else "" for m in face_materials]
    return replace(
        element,
        surfaces=kept,
        face_materials=face_materials,
        geometry_hash=geometry_hash(
            element.vertices, element.face_sizes, element.face_indices, names
        ),
    )
