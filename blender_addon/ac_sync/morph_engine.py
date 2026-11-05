# SPDX-License-Identifier: GPL-3.0-or-later
"""Morph sync between Blender meshes and Archicad Morphs. All bpy work for the morph tool.

Bookkeeping lives in custom properties on the Blender objects:

``acmorph_guid``     GUID of the Archicad morph this object stands for
``acmorph_ac_hash``  fingerprint of the Archicad geometry at the last sync (either direction)
``acmorph_bl_hash``  fingerprint of the Blender geometry at the last sync
``acmorph_removed``  set when the morph no longer exists in Archicad

The two fingerprints tell which side changed since the last sync: a side whose current
fingerprint differs from the stored one has changed. When both changed, that is a conflict.

Coordinates: Archicad project coordinates (metres) plus the scene's model offset are Blender
world coordinates. Blender and Archicad are both Z-up, so no axis swap is needed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import bpy
from mathutils import Matrix, Vector
from mathutils.geometry import tessellate_polygon

from . import engine
from .acsync import (
    MorphApi,
    MorphFace,
    MorphGeometry,
    SurfaceInfo,
    SurfaceStyle,
    planar_deviation,
    polygon_normal,
)

PROP_GUID = "acmorph_guid"
PROP_AC_HASH = "acmorph_ac_hash"
PROP_BL_HASH = "acmorph_bl_hash"
PROP_REMOVED = "acmorph_removed"


# -- collections and objects -----------------------------------------------------------------


def morph_collection(scene: bpy.types.Scene, name: str) -> bpy.types.Collection:
    return engine.root_collection(scene, name)


def morph_objects() -> dict[str, bpy.types.Object]:
    """Every object that stands for an Archicad morph, by (upper-case) GUID."""
    found: dict[str, bpy.types.Object] = {}
    for obj in bpy.data.objects:
        guid = obj.get(PROP_GUID)
        if guid:
            found.setdefault(str(guid).upper(), obj)
    return found


def can_push(obj: bpy.types.Object) -> bool:
    """Plain mesh objects. Objects the IFC sync manages are Archicad walls, slabs ... not morphs."""
    return obj.type == "MESH" and not obj.get(engine.PROP_GUID)


# -- surfaces <-> materials ------------------------------------------------------------------


def surface_resolver(settings, by_name: dict[str, str]):
    """Blender material -> Archicad surface GUID ('' when nothing matches).

    Tries, in order: the surface the IFC sync recorded on the material, the user's material
    table (reversed), the material's own name, the name without Blender's '.001' suffix and
    without the configured prefix."""
    reverse = {row.blender_material: row.name for row in settings.surfaces if row.blender_material}
    prefix = settings.material_prefix

    def resolve(material) -> str:
        if material is None:
            return ""
        names = [
            material.get("acsync_surface"),
            reverse.get(material.name),
            material.name,
            re.sub(r"\.\d{3}$", "", material.name),
        ]
        if prefix and material.name.startswith(prefix):
            names.append(material.name[len(prefix):])
        for name in names:
            if name and name in by_name:
                return by_name[name]
        return ""

    return resolve


# -- Blender mesh -> geometry ----------------------------------------------------------------


@dataclass
class Extracted:
    geometry: MorphGeometry
    triangulated: int = 0  # warped faces split into triangles
    loose_edges: int = 0  # edges without a face (not sent)
    unmatched: set[str] = field(default_factory=set)  # material names with no Archicad surface


def evaluated_depsgraph():
    bpy.context.view_layer.update()
    return bpy.context.evaluated_depsgraph_get()


def extract_geometry(
    obj, depsgraph, apply_modifiers: bool, offset, resolve, tolerance: float
) -> Extracted:
    """The object's faces in Archicad project coordinates (world minus the model offset)."""
    source = obj.evaluated_get(depsgraph) if apply_modifiers else obj
    mesh = source.to_mesh()
    try:
        world = obj.matrix_world
        flip = world.determinant() < 0  # a mirrored object turns its faces inside out
        off = Vector(offset)
        coords = [tuple(world @ v.co - off) for v in mesh.vertices]
        remap: dict[int, int] = {}
        vertices: list[tuple[float, float, float]] = []
        faces: list[MorphFace] = []
        out = Extracted(MorphGeometry(vertices, faces))
        materials = list(mesh.materials)

        def use(i: int) -> int:
            if i not in remap:
                remap[i] = len(vertices)
                vertices.append(coords[i])
            return remap[i]

        for poly in mesh.polygons:
            idx = list(poly.vertices)
            if flip:
                idx.reverse()
            material = materials[poly.material_index] if poly.material_index < len(materials) else None
            surface = resolve(material)
            if material is not None and not surface:
                out.unmatched.add(material.name)
            points = [coords[i] for i in idx]
            if len(idx) > 3 and planar_deviation(points) > tolerance:
                out.triangulated += 1
                for tri in _triangulate(points):
                    faces.append(MorphFace([use(idx[k]) for k in tri], surface=surface))
            else:
                faces.append(MorphFace([use(i) for i in idx], surface=surface))
        out.loose_edges = sum(1 for e in mesh.edges if e.is_loose)
        return out
    finally:
        source.to_mesh_clear()


def _triangulate(points) -> list[tuple[int, int, int]]:
    """Triangles over a (warped) polygon's corners, wound like the polygon itself."""
    tris = tessellate_polygon([[Vector(p) for p in points]])
    normal = polygon_normal(points)
    result = []
    for a, b, c in tris:
        n = polygon_normal([points[a], points[b], points[c]])
        if sum(x * y for x, y in zip(n, normal)) < 0:
            b, c = c, b
        result.append((a, b, c))
    return result


# -- geometry -> Blender mesh ----------------------------------------------------------------


def _face_loops(geometry: MorphGeometry) -> tuple[list[list[int]], list[int], int]:
    """Blender cannot hold a face with a hole, so those are triangulated.

    Returns (faces as vertex index lists, source face index per face, how many had holes)."""
    loops: list[list[int]] = []
    source: list[int] = []
    holed = 0
    for n, face in enumerate(geometry.faces):
        if not face.holes:
            loops.append(list(face.loop))
            source.append(n)
            continue
        holed += 1
        rings = [face.loop] + face.holes
        flat = [i for ring in rings for i in ring]
        polylines = [[Vector(geometry.vertices[i]) for i in ring] for ring in rings]
        for a, b, c in tessellate_polygon(polylines):
            loops.append([flat[a], flat[b], flat[c]])
            source.append(n)
    return loops, source, holed


def write_mesh(obj, geometry: MorphGeometry, surfaces, mapping, offset, report) -> None:
    """Put an Archicad morph's faces into the object's mesh (world -> the object's own space)."""
    mesh = obj.data
    inverse = obj.matrix_world.inverted()
    off = Vector(offset)
    verts = [tuple(inverse @ (Vector(v) + off)) for v in geometry.vertices]
    loops, source, holed = _face_loops(geometry)
    if holed:
        report.add(f"{obj.name}: {holed} face(s) with holes were split into triangles")

    slots = engine.load_slots(obj)
    slot_of: dict[str, int] = {}
    for guid in dict.fromkeys(f.surface for f in geometry.faces):
        info = surfaces.get(guid) or SurfaceInfo(guid, "Archicad default")
        slot_of[guid] = engine.slot_index_for(
            obj, SurfaceStyle(info.name, info.color, info.transparency), slots, mapping, report
        )

    same_topology = len(mesh.vertices) == len(verts) and [
        list(p.vertices) for p in mesh.polygons
    ] == loops
    if same_topology:  # keeps vertex groups, shape keys and so on
        mesh.vertices.foreach_set("co", [c for v in verts for c in v])
    else:
        mesh.clear_geometry()  # keeps the material slots
        mesh.from_pydata(verts, [], loops)
    mesh.polygons.foreach_set(
        "material_index", [slot_of[geometry.faces[n].surface] for n in source]
    )
    mesh.update()
    obj["acsync_slots"] = json.dumps(slots)


# -- reports ---------------------------------------------------------------------------------


@dataclass
class Counts:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    conflicts: int = 0
    newer_other_side: int = 0  # the other side changed, this direction has nothing to send
    failed: int = 0
    removed: int = 0

    def summary(self, verb_new: str, verb_update: str) -> str:
        parts = [f"{self.created} {verb_new}", f"{self.updated} {verb_update}",
                 f"{self.unchanged} unchanged"]
        for n, label in (
            (self.conflicts, "conflict(s) skipped"),
            (self.newer_other_side, "changed on the other side only"),
            (self.failed, "failed"),
            (self.removed, "gone from Archicad"),
        ):
            if n:
                parts.append(f"{n} {label}")
        return ", ".join(parts)


# -- pull: Archicad -> Blender ---------------------------------------------------------------


def pull(scene, settings, msettings, api: MorphApi, dry_run: bool = False) -> engine.Report:
    report = engine.Report(dry_run=dry_run)
    counts = Counts()
    offset = tuple(settings.offset)
    every_guid = api.list_guids()
    existing = set(every_guid)
    wanted = api.selected_morphs() if msettings.pull_scope == "SELECTED" else every_guid
    geometries = api.read(wanted)
    objects = morph_objects()
    depsgraph = evaluated_depsgraph()
    by_name = api.surface_names()
    resolve = surface_resolver(settings, {n: g for g, n in by_name.items()})
    apply_mods = msettings.apply_modifiers

    todo: list[tuple[str, MorphGeometry, bpy.types.Object | None]] = []
    for guid in wanted:
        geometry = geometries[guid]
        new_hash = geometry.fingerprint()
        obj = objects.get(guid)
        if obj is None:
            todo.append((guid, geometry, None))
            counts.created += 1
            continue
        if obj.get(PROP_REMOVED) and not dry_run:
            del obj[PROP_REMOVED]
        if obj.get(PROP_AC_HASH) == new_hash:
            counts.unchanged += 1
            continue
        blender_now = extract_geometry(
            obj, depsgraph, apply_mods, offset, resolve, msettings.planar_tolerance
        ).geometry.fingerprint()
        if blender_now != obj.get(PROP_BL_HASH) and msettings.conflict == "SKIP":
            counts.conflicts += 1
            report.warnings.append(
                f"{obj.name}: changed in Blender and in Archicad since the last sync, skipped "
                "(set 'If both changed' to Overwrite to take Archicad's version)"
            )
            continue
        todo.append((guid, geometry, obj))
        counts.updated += 1

    if msettings.pull_scope == "ALL":
        for guid, obj in objects.items():
            if guid not in existing and not obj.get(PROP_REMOVED):
                counts.removed += 1
                report.add(f"{obj.name}: deleted in Archicad (the Blender object is kept)")
                if not dry_run:
                    obj[PROP_REMOVED] = True

    report.diff_summary = counts.summary("new", "changed")
    if dry_run or not todo:
        for guid, _geometry, obj in todo:
            report.add(f"{obj.name if obj else guid}: would be {'changed' if obj else 'added'}")
        return report

    details = api.details([g for g, _geo, o in todo if o is None])
    infos = api.surface_details({s for _g, geo, _o in todo for s in geo.surface_guids()})
    mapping = settings.material_map()
    collection = morph_collection(scene, msettings.collection)
    for guid, geometry, obj in todo:
        if obj is None:
            d = details.get(guid)
            name = (d.element_id if d else "") or guid
            obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
            collection.objects.link(obj)
            if d:  # the object's pivot is the morph's own origin; set now, matrix_world lags
                obj.matrix_world = Matrix.Translation(Vector(d.origin) + Vector(offset))
            obj[PROP_GUID] = guid
        write_mesh(obj, geometry, infos, mapping, offset, report)
        depsgraph = evaluated_depsgraph()
        obj[PROP_AC_HASH] = geometry.fingerprint()
        obj[PROP_BL_HASH] = extract_geometry(
            obj, depsgraph, apply_mods, offset, resolve, msettings.planar_tolerance
        ).geometry.fingerprint()
        report.changed_guids.append(guid)
    settings.sync_surfaces([i.name for i in infos.values() if i.name])
    return report


# -- push: Blender -> Archicad ---------------------------------------------------------------


def push(scene, settings, msettings, api: MorphApi, objects, dry_run: bool = False) -> engine.Report:
    report = engine.Report(dry_run=dry_run)
    counts = Counts()
    offset = tuple(settings.offset)
    known = set(api.list_guids())
    by_name = {n: g for g, n in api.surface_names().items()}
    resolve = surface_resolver(settings, by_name)
    depsgraph = evaluated_depsgraph()

    creates: list[tuple[bpy.types.Object, MorphGeometry]] = []
    updates: list[tuple[bpy.types.Object, MorphGeometry]] = []
    unmatched: set[str] = set()
    current: dict[str, MorphGeometry] = {}
    update_guids = [
        str(o.get(PROP_GUID)).upper()
        for o in objects
        if o.get(PROP_GUID) and str(o.get(PROP_GUID)).upper() in known
    ]
    if update_guids:
        current = api.read(update_guids)

    for obj in objects:
        ex = extract_geometry(
            obj, depsgraph, msettings.apply_modifiers, offset, resolve, msettings.planar_tolerance
        )
        if not ex.geometry.faces:
            report.warnings.append(f"{obj.name}: no faces, skipped")
            counts.failed += 1
            continue
        unmatched |= ex.unmatched
        if ex.triangulated:
            report.add(f"{obj.name}: {ex.triangulated} warped face(s) were split into triangles")
        if ex.loose_edges:
            report.add(f"{obj.name}: {ex.loose_edges} edge(s) without a face were not sent")
        guid = str(obj.get(PROP_GUID) or "").upper()
        if not guid or guid not in known:
            if guid:
                report.add(f"{obj.name}: its morph is gone from Archicad, a new one is made")
            creates.append((obj, ex.geometry))
            counts.created += 1
            continue
        blender_changed = ex.geometry.fingerprint() != obj.get(PROP_BL_HASH)
        archicad_changed = current[guid].fingerprint() != obj.get(PROP_AC_HASH)
        if not blender_changed and not archicad_changed:
            counts.unchanged += 1
        elif not blender_changed:
            counts.newer_other_side += 1
            report.add(f"{obj.name}: changed in Archicad only (use Pull to bring it over)")
        elif archicad_changed and msettings.conflict == "SKIP":
            counts.conflicts += 1
            report.warnings.append(
                f"{obj.name}: changed in Blender and in Archicad since the last sync, skipped "
                "(set 'If both changed' to Overwrite to replace Archicad's version)"
            )
        else:
            updates.append((obj, ex.geometry))
            counts.updated += 1

    if unmatched:
        report.warnings.append(
            "No Archicad surface with the name of: " + ", ".join(sorted(unmatched))
            + ". Those faces get Archicad's default surface. Name the material like a surface, "
            "or map it in Materials."
        )
    report.diff_summary = counts.summary("created", "updated")
    if dry_run:
        for obj, _g in creates:
            report.add(f"{obj.name}: would be created in Archicad")
        for obj, _g in updates:
            report.add(f"{obj.name}: would be updated in Archicad")
        return report

    _write_creates(api, creates, offset, report, counts, msettings, resolve)
    _write_updates(api, updates, report, counts, msettings, resolve, offset)
    report.diff_summary = counts.summary("created", "updated")
    return report


def _store_sync_state(api, pairs, report, msettings, resolve, offset) -> None:
    """Remember both sides' fingerprints after a write. The Archicad side is read back, since
    Archicad may reorder what it was given."""
    back = api.read([g for _o, g in pairs])
    depsgraph = evaluated_depsgraph()
    for obj, guid in pairs:
        obj[PROP_GUID] = guid
        if PROP_REMOVED in obj:
            del obj[PROP_REMOVED]
        obj[PROP_AC_HASH] = back[guid].fingerprint()
        obj[PROP_BL_HASH] = extract_geometry(
            obj, depsgraph, msettings.apply_modifiers, offset, resolve, msettings.planar_tolerance
        ).geometry.fingerprint()
        report.changed_guids.append(guid)


def _write_creates(api, creates, offset, report, counts, msettings, resolve) -> None:
    if not creates:
        return
    off = Vector(offset)
    items = [(geometry, tuple(obj.matrix_world.translation - off)) for obj, geometry in creates]
    results = api.create(items)
    done = []
    for (obj, _geometry), result in zip(creates, results):
        if isinstance(result, Exception):
            counts.created -= 1
            counts.failed += 1
            report.warnings.append(f"{obj.name}: Archicad refused the morph ({result})")
        else:
            done.append((obj, result))
    if done:
        _store_sync_state(api, [(o, g) for o, g in done], report, msettings, resolve, offset)


def _write_updates(api, updates, report, counts, msettings, resolve, offset) -> None:
    if not updates:
        return
    guids = [str(o[PROP_GUID]).upper() for o, _g in updates]
    placements = api.details(guids)
    items = [(g, geometry, placements[g].origin) for g, (_o, geometry) in zip(guids, updates)]
    results = api.modify(items)
    done = []
    for (obj, _geometry), guid, error in zip(updates, guids, results):
        if error is not None:
            counts.updated -= 1
            counts.failed += 1
            report.warnings.append(f"{obj.name}: Archicad refused the change ({error})")
        else:
            done.append((obj, guid))
    if done:
        _store_sync_state(api, done, report, msettings, resolve, offset)
