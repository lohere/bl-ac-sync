# SPDX-License-Identifier: GPL-3.0-or-later
"""Read an Archicad IFC export into plain Python records (no Blender needed).

Matching key is the Archicad element GUID, which Archicad writes into the IFC ``Tag``
attribute (spatial elements such as the terrain ``IfcSite`` use ``ifc:<GlobalId>``). The Element ID (the label users see, e.g. ``WALL-0231``) is in ``Name``. Element
IDs are *not* unique in Archicad (copy/paste keeps them), the GUID is.

Geometry comes out of ifcopenshell as polygon faces in metres (``POLYHEDRON_WITHOUT_HOLES``:
quads and n-gons are preserved, only faces with holes are triangulated), with one material
index per face.
"""

from __future__ import annotations

import hashlib
import os
import struct
from collections.abc import Iterable
from dataclasses import dataclass, field

from .errors import IfcReadError
from .uv import box_projection_uvs

# Types that are not useful as Blender geometry by default.
DEFAULT_EXCLUDED_TYPES = frozenset({"IfcSpace", "IfcOpeningElement", "IfcAnnotation", "IfcGrid"})

# Spatial elements have no ``Tag`` attribute. Archicad writes all its Mesh (terrain) elements
# into one IfcSite and zones as IfcSpace, so these are keyed by their IFC GlobalId instead
# (prefixed so it can never collide with an Archicad GUID).
GLOBALID_KEYED_TYPES = frozenset({"IfcSite", "IfcSpace"})
GLOBALID_KEY_PREFIX = "ifc:"

_HASH_PRECISION = 10_000  # 0.1 mm: below this, geometry differences are float noise


@dataclass(frozen=True)
class SurfaceStyle:
    """An Archicad surface as seen in the IFC."""

    name: str
    diffuse: tuple[float, float, float] = (0.8, 0.8, 0.8)
    transparency: float = 0.0


@dataclass(frozen=True)
class Element:
    guid: str  # Archicad element GUID (IFC Tag): the matching key
    element_id: str  # Archicad Element ID (IFC Name): a label, not unique
    global_id: str  # IFC GlobalId
    ifc_type: str  # e.g. IfcWall
    vertices: tuple[float, ...]  # flat x,y,z in metres
    face_sizes: tuple[int, ...]  # number of corners per face
    face_indices: tuple[int, ...]  # flat vertex indices, face after face
    face_materials: tuple[int, ...]  # per face: index into ``surfaces`` (-1 = none)
    surfaces: tuple[SurfaceStyle, ...]
    geometry_hash: str
    uvs: tuple[float, ...] = ()  # u,v per face corner (empty when UV generation is off)

    @property
    def vertex_count(self) -> int:
        return len(self.vertices) // 3

    @property
    def face_count(self) -> int:
        return len(self.face_sizes)


@dataclass(frozen=True)
class Skipped:
    ifc_type: str
    element_id: str
    reason: str


@dataclass
class IfcModel:
    path: str
    schema: str
    elements: dict[str, Element] = field(default_factory=dict)  # keyed by GUID
    skipped: list[Skipped] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    uv_scale: float | None = None  # metres per UV unit, None = no UVs were generated

    def surface_names(self) -> list[str]:
        names = {s.name for e in self.elements.values() for s in e.surfaces}
        return sorted(names)


def geometry_hash(
    vertices: Iterable[float],
    face_sizes: Iterable[int],
    face_indices: Iterable[int],
    face_material_names: Iterable[str],
) -> str:
    """Stable fingerprint of a mesh, insensitive to float noise below 0.1 mm."""
    h = hashlib.sha1()
    verts = [round(v * _HASH_PRECISION) for v in vertices]
    h.update(struct.pack(f"<{len(verts)}q", *verts))
    sizes = list(face_sizes)
    h.update(struct.pack(f"<{len(sizes)}i", *sizes))
    idx = list(face_indices)
    h.update(struct.pack(f"<{len(idx)}i", *idx))
    h.update("\x1f".join(face_material_names).encode("utf-8"))
    return h.hexdigest()


def _site_to_archicad_matrix(ifc):
    """4x4 matrix undoing the IfcSite placement, or ``None`` if there is nothing to undo.

    Archicad bakes Project North (and the survey point) into the IfcSite placement of its
    IFC export: raw geometry is in Archicad model coordinates, ifcopenshell's world
    coordinates are rotated/shifted by that placement. Verified against Archicad's own 3D
    bounding boxes: after undoing it, all slabs and roofs match to 0.000 m.
    """
    import numpy as np
    from ifcopenshell.util.placement import get_local_placement

    sites = ifc.by_type("IfcSite")
    if not sites or sites[0].ObjectPlacement is None:
        return None
    matrix = np.linalg.inv(get_local_placement(sites[0].ObjectPlacement))
    if np.allclose(matrix, np.eye(4), atol=1e-9):
        return None
    return matrix


def _transform(vertices: tuple[float, ...], m) -> tuple[float, ...]:
    r00, r01, r02, t0 = (float(x) for x in m[0])
    r10, r11, r12, t1 = (float(x) for x in m[1])
    r20, r21, r22, t2 = (float(x) for x in m[2])
    out = []
    for i in range(0, len(vertices), 3):
        x, y, z = vertices[i], vertices[i + 1], vertices[i + 2]
        out.append(r00 * x + r01 * y + r02 * z + t0)
        out.append(r10 * x + r11 * y + r12 * z + t1)
        out.append(r20 * x + r21 * y + r22 * z + t2)
    return tuple(out)


def read_ifc(
    path: str | os.PathLike,
    excluded_types: Iterable[str] = DEFAULT_EXCLUDED_TYPES,
    threads: int = 4,
    archicad_coordinates: bool = True,
    uv_scale: float | None = None,
) -> IfcModel:
    """Read all geometry-carrying products of an IFC file, keyed by Archicad GUID.

    With ``archicad_coordinates`` (default) the IfcSite placement is undone so geometry is in
    Archicad's own model coordinates (what Archicad and the old FBX export use). Pass False
    to keep the rotated IFC world coordinates.
    """
    import ifcopenshell
    from ifcopenshell import geom, ifcopenshell_wrapper

    path = os.fspath(path)
    if not os.path.isfile(path):
        raise IfcReadError(f"IFC file not found: {path}")
    try:
        ifc = ifcopenshell.open(path)
    except Exception as exc:  # ifcopenshell raises plain Exceptions
        raise IfcReadError(f"Cannot open IFC file {path}: {exc}") from exc

    excluded = set(excluded_types)
    model = IfcModel(path=path, schema=ifc.schema)

    settings = geom.settings()
    settings.set("use-world-coords", True)
    settings.set("triangulation-type", ifcopenshell_wrapper.POLYHEDRON_WITHOUT_HOLES)

    to_archicad = _site_to_archicad_matrix(ifc) if archicad_coordinates else None
    model.uv_scale = uv_scale

    exclude_entities = []
    for type_name in excluded:
        try:
            exclude_entities.extend(ifc.by_type(type_name))
        except RuntimeError:
            continue  # type not present in this schema (e.g. IfcAnnotation in IFC2X3 variants)
    for entity in exclude_entities:
        model.skipped.append(
            Skipped(entity.is_a(), str(getattr(entity, "Name", "") or ""), "excluded type")
        )

    kwargs = {"exclude": exclude_entities} if exclude_entities else {}
    iterator = geom.iterator(settings, ifc, threads, **kwargs)
    if not iterator.initialize():
        raise IfcReadError("The IFC file contains no geometry (or all of it is excluded).")

    while True:
        shape = iterator.get()
        entity = ifc.by_id(shape.id)
        element_id = str(getattr(entity, "Name", "") or "")
        guid = getattr(entity, "Tag", None)
        if not guid and entity.is_a() in GLOBALID_KEYED_TYPES:
            guid = f"{GLOBALID_KEY_PREFIX}{entity.GlobalId}"
        if not guid:
            model.skipped.append(Skipped(entity.is_a(), element_id, "no Archicad GUID (Tag)"))
        elif guid in model.elements:
            model.warnings.append(
                f"Duplicate GUID {guid} ({element_id}); the second occurrence was ignored."
            )
        else:
            element = _build_element(
                ifc, entity, shape.geometry, guid, element_id, to_archicad, uv_scale
            )
            if element is None:
                model.skipped.append(Skipped(entity.is_a(), element_id, "empty geometry"))
            else:
                model.elements[guid] = element
        if not iterator.next():
            break

    if not model.elements:
        raise IfcReadError("No elements with an Archicad GUID were found in the IFC file.")
    return model


def _build_element(
    ifc, entity, geometry, guid: str, element_id: str, to_archicad=None, uv_scale=None
) -> Element | None:
    verts = tuple(float(v) for v in geometry.verts)
    if to_archicad is not None:
        verts = _transform(verts, to_archicad)
    faces = list(geometry.faces)
    if not verts or not faces:
        return None

    face_sizes = tuple(len(face) for face in faces)
    face_indices = tuple(int(i) for face in faces for i in face)

    surfaces: list[SurfaceStyle] = []
    for material in geometry.materials:
        surfaces.append(_surface_style(ifc, material))
    face_materials = tuple(int(m) for m in geometry.material_ids)

    names_per_face = [surfaces[m].name if 0 <= m < len(surfaces) else "" for m in face_materials]
    return Element(
        guid=guid,
        element_id=element_id,
        global_id=str(entity.GlobalId),
        ifc_type=entity.is_a(),
        vertices=verts,
        face_sizes=face_sizes,
        face_indices=face_indices,
        face_materials=face_materials,
        surfaces=tuple(surfaces),
        geometry_hash=geometry_hash(verts, face_sizes, face_indices, names_per_face),
        uvs=box_projection_uvs(verts, face_sizes, face_indices, uv_scale) if uv_scale else (),
    )


def _surface_style(ifc, material) -> SurfaceStyle:
    """Resolve an ifcopenshell material to the Archicad surface name and colour."""
    name = material.name
    try:
        style = ifc.by_id(material.instance_id())
        if style.is_a("IfcSurfaceStyle") and style.Name:
            name = style.Name
    except RuntimeError:
        pass
    diffuse = (0.8, 0.8, 0.8)
    try:
        c = material.diffuse
        diffuse = (float(c.r()), float(c.g()), float(c.b()))
    except (AttributeError, RuntimeError, TypeError):
        pass
    transparency = 0.0
    try:
        if material.has_transparency():
            transparency = float(material.transparency)
    except (AttributeError, RuntimeError):
        pass
    return SurfaceStyle(name=str(name), diffuse=diffuse, transparency=transparency)
