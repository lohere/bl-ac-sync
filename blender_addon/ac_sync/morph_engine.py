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
    face_size,
    planar_deviation,
    polygon_normal,
)

PROP_GUID = "acmorph_guid"
PROP_AC_HASH = "acmorph_ac_hash"
PROP_BL_HASH = "acmorph_bl_hash"
PROP_REMOVED = "acmorph_removed"
CONFLICT_MARK = "changed in Blender and in Archicad since the last sync, skipped"
BIG_MORPH_FACES = 20_000  # above this the report mentions that Archicad needs a moment


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


def shared_guids() -> set[str]:
    """GUIDs claimed by more than one object. Shift+D copies the custom properties too, so a copy
    of a pushed object points at the original's morph; syncing either would be a guess."""
    seen: set[str] = set()
    shared: set[str] = set()
    for obj in bpy.data.objects:
        guid = obj.get(PROP_GUID)
        if guid:
            guid = str(guid).upper()
            (shared if guid in seen else seen).add(guid)
    return shared


def detach(objects) -> int:
    """Forget the Archicad morph of each object; the next push makes a new one."""
    count = 0
    for obj in objects:
        keys = [k for k in obj.keys() if k.startswith("acmorph_")]  # noqa: SIM118 (an ID, not a dict)
        for key in keys:
            del obj[key]
        count += bool(keys)
    return count


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
    overshared_edges: int = 0  # edges on more than two faces: Archicad will refuse the body
    unmatched: set[str] = field(default_factory=set)  # material names with no Archicad surface


def evaluated_depsgraph():
    bpy.context.view_layer.update()
    return bpy.context.evaluated_depsgraph_get()


def extract_geometry(
    obj, depsgraph, apply_modifiers: bool, offset, resolve, flatness: float
) -> Extracted:
    """The object's faces in Archicad project coordinates (world minus the model offset).

    A face with more than three corners is split into triangles when it is warped by more than
    ``flatness`` times its size (Archicad's own limit is about 0.0035)."""
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
        taken: set | None = None  # edges in use; built when the first face needs splitting

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
            if len(idx) > 3 and planar_deviation(points) > flatness * face_size(points):
                out.triangulated += 1
                if taken is None:  # a new diagonal must not land on an existing edge
                    taken = {tuple(e.key) for e in mesh.edges}
                for tri in _triangulate(points, idx, taken):
                    faces.append(MorphFace([use(idx[k]) for k in tri], surface=surface))
            else:
                faces.append(MorphFace([use(i) for i in idx], surface=surface))
        out.loose_edges = sum(1 for e in mesh.edges if e.is_loose)
        out.overshared_edges = out.geometry.overshared_edges()
        return out
    finally:
        source.to_mesh_clear()


def _triangulate(points, vertex_ids, taken: set) -> list[tuple[int, int, int]]:
    """Triangles over a (warped) polygon's corners, wound like the polygon itself.

    The new diagonals must not coincide with an edge the mesh already has: an edge on three
    faces makes Archicad refuse the whole body. ``vertex_ids`` are the mesh vertex indices of the
    corners and ``taken`` the edges in use (diagonals chosen here are added to it)."""
    n = len(points)
    normal = polygon_normal(points)

    def oriented(tris):
        result = []
        for a, b, c in tris:
            tn = polygon_normal([points[a], points[b], points[c]])
            if sum(x * y for x, y in zip(tn, normal)) < 0:
                b, c = c, b
            result.append((a, b, c))
        return result

    def diagonals(tris) -> set:
        keys = set()
        for tri in tris:
            for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
                if (a - b) % n not in (1, n - 1):  # not neighbours in the polygon's loop
                    keys.add((min(vertex_ids[a], vertex_ids[b]), max(vertex_ids[a], vertex_ids[b])))
        return keys

    def fan(start):
        return [(start, (start + k) % n, (start + k + 1) % n) for k in range(1, n - 1)]

    tessellated = oriented(tessellate_polygon([[Vector(p) for p in points]]))
    fans = [oriented(fan(s)) for s in range(n)]
    candidates = fans[:2] + [tessellated] + fans[2:] if n == 4 else [tessellated] + fans
    for tris in candidates:
        faces_ok = all(
            sum(x * y for x, y in zip(polygon_normal([points[a], points[b], points[c]]), normal)) > 0
            for a, b, c in tris
        )
        keys = diagonals(tris)
        if faces_ok and not keys & taken:
            taken |= keys
            return tris
    taken |= diagonals(tessellated)  # nothing fits: the tessellation, and the report will warn
    return tessellated


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
    obj[engine.PROP_SLOTS] = json.dumps(slots)


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


def pull(
    scene, settings, msettings, api: MorphApi, dry_run: bool = False,
    scope: str = "ALL", only: set[str] | None = None, conflict: str | None = None,
) -> engine.Report:
    """Archicad -> Blender.

    ``scope`` is ``ALL`` (every morph) or ``SELECTED`` (the ones selected in Archicad). ``only``
    narrows it to those GUIDs (the collection sync passes the morphs it already has). ``conflict``
    overrides the setting."""
    report = engine.Report(dry_run=dry_run)
    counts = Counts()
    conflict = conflict or msettings.conflict
    offset = tuple(settings.offset)
    every_guid = api.list_guids()
    existing = set(every_guid)
    wanted = api.selected_morphs() if scope == "SELECTED" else every_guid
    if scope == "SELECTED" and not wanted:
        report.warnings.append(
            "No morph is selected in Archicad. Select the morphs you want first "
            "(only Morph elements count)."
        )
    if only is not None:
        wanted = [g for g in wanted if g in only]
    geometries = api.read(wanted)
    objects = morph_objects()
    shared = shared_guids()
    depsgraph = evaluated_depsgraph()
    resolve = surface_resolver(settings, {n: g for g, n in api.surface_names().items()})
    flatness = msettings.flatness_ratio()

    todo: list[tuple[str, MorphGeometry, bpy.types.Object | None]] = []
    for guid in wanted:
        geometry = geometries[guid]
        obj = objects.get(guid)
        if obj is None:
            todo.append((guid, geometry, None))
            counts.created += 1
            continue
        if guid in shared:
            counts.failed += 1
            report.warnings.append(_shared_warning(guid))
            continue
        if obj.get(PROP_REMOVED) and not dry_run:
            del obj[PROP_REMOVED]
        if obj.get(PROP_AC_HASH) == geometry.fingerprint():
            counts.unchanged += 1
            continue
        blender_now = extract_geometry(
            obj, depsgraph, msettings.apply_modifiers, offset, resolve, flatness
        ).geometry.fingerprint()
        if blender_now != obj.get(PROP_BL_HASH) and conflict == "SKIP":
            counts.conflicts += 1
            report.warnings.append(
                f"{obj.name}: {CONFLICT_MARK} "
                "(set 'If both changed' to Overwrite to take Archicad's version)"
            )
            continue
        todo.append((guid, geometry, obj))
        counts.updated += 1

    if scope == "ALL":
        for guid, obj in objects.items():
            if only is not None and guid not in only:
                continue
            if guid not in existing and not obj.get(PROP_REMOVED):
                counts.removed += 1
                report.add(f"{obj.name}: deleted in Archicad (the Blender object is kept)")
                if not dry_run:
                    obj[PROP_REMOVED] = True

    report.diff_summary = counts.summary("new", "changed")
    if dry_run:
        for guid, _geometry, obj in todo:
            report.add(f"{obj.name if obj else guid}: would be {'changed' if obj else 'added'}")
    if dry_run or not todo:
        return report

    details = api.details([g for g, _geo, o in todo if o is None])
    infos = api.surface_details({s for _g, geo, _o in todo for s in geo.surface_guids()})
    mapping = settings.material_map()
    collection = morph_collection(scene, msettings.collection)
    written: list[tuple[str, MorphGeometry, bpy.types.Object]] = []
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
        written.append((guid, geometry, obj))

    depsgraph = evaluated_depsgraph()  # once for all objects, not once per object
    for guid, geometry, obj in written:
        obj[PROP_AC_HASH] = geometry.fingerprint()
        obj[PROP_BL_HASH] = extract_geometry(
            obj, depsgraph, msettings.apply_modifiers, offset, resolve, flatness
        ).geometry.fingerprint()
        report.changed_guids.append(guid)
    settings.sync_surfaces([i.name for i in infos.values() if i.name])
    return report


def _shared_warning(guid: str) -> str:
    names = ", ".join(
        sorted(o.name for o in bpy.data.objects if str(o.get(PROP_GUID)).upper() == guid)
    )
    return (
        f"{names} all stand for the same Archicad morph (a copy keeps the link), so they are "
        "skipped. Select the copy and press 'Detach from Archicad'."
    )


# -- push: Blender -> Archicad ---------------------------------------------------------------


@dataclass
class Planned:
    obj: bpy.types.Object
    geometry: MorphGeometry
    fingerprint: str  # of the Blender side, stored after a successful write


def push(
    scene, settings, msettings, api: MorphApi, objects, dry_run: bool = False,
    conflict: str | None = None,
) -> engine.Report:
    """Blender -> Archicad for ``objects``. ``conflict`` overrides the setting."""
    report = engine.Report(dry_run=dry_run)
    counts = Counts()
    conflict = conflict or msettings.conflict
    offset = tuple(settings.offset)
    known = set(api.list_guids())
    resolve = surface_resolver(settings, {n: g for g, n in api.surface_names().items()})
    depsgraph = evaluated_depsgraph()
    shared = shared_guids()

    def guid_of(obj) -> str:
        return str(obj.get(PROP_GUID) or "").upper()

    creates: list[Planned] = []
    updates: list[Planned] = []
    unmatched: set[str] = set()
    existing = [g for g in dict.fromkeys(map(guid_of, objects)) if g in known and g not in shared]
    current = api.read(existing) if existing else {}

    for obj in objects:
        guid = guid_of(obj)
        if guid and guid in shared:
            counts.failed += 1
            report.warnings.append(_shared_warning(guid))
            continue
        ex = extract_geometry(
            obj, depsgraph, msettings.apply_modifiers, offset, resolve, msettings.flatness_ratio()
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
        if len(ex.geometry.faces) > BIG_MORPH_FACES:
            report.add(
                f"{obj.name}: {len(ex.geometry.faces):,} faces. Archicad needs several seconds "
                "per 50,000 faces and gets slow with very dense morphs."
            )
        if ex.overshared_edges:
            report.warnings.append(
                f"{obj.name}: {ex.overshared_edges} edge(s) border more than two faces. Archicad "
                "cannot build that and will refuse the morph. Select > Select All by Trait > "
                "Non Manifold (with 'Multiple Faces') shows them."
            )
        planned = Planned(obj, ex.geometry, ex.geometry.fingerprint())
        if not guid or guid not in known:
            if guid:
                report.add(f"{obj.name}: its morph is gone from Archicad, a new one is made")
            creates.append(planned)
            counts.created += 1
            continue
        blender_changed = planned.fingerprint != obj.get(PROP_BL_HASH)
        archicad_changed = current[guid].fingerprint() != obj.get(PROP_AC_HASH)
        if not blender_changed and not archicad_changed:
            counts.unchanged += 1
        elif not blender_changed:
            counts.newer_other_side += 1
            report.add(f"{obj.name}: changed in Archicad only (use Pull to bring it over)")
        elif archicad_changed and conflict == "SKIP":
            counts.conflicts += 1
            report.warnings.append(
                f"{obj.name}: {CONFLICT_MARK} "
                "(set 'If both changed' to Overwrite to replace Archicad's version)"
            )
        else:
            updates.append(planned)
            counts.updated += 1

    if unmatched:
        report.warnings.append(
            "No Archicad surface with the name of: " + ", ".join(sorted(unmatched))
            + ". Those faces get Archicad's default surface. Name the material like a surface, "
            "or map it in Materials."
        )
    report.diff_summary = counts.summary("created", "updated")
    if dry_run:
        report.lines += [f"{p.obj.name}: would be created in Archicad" for p in creates]
        report.lines += [f"{p.obj.name}: would be updated in Archicad" for p in updates]
        return report

    stored = _write_creates(api, creates, offset, report, counts)
    stored += _write_updates(api, updates, report, counts)
    _remember(api, stored, report)
    report.diff_summary = counts.summary("created", "updated")
    return report


def _remember(api, stored: list[tuple[Planned, str]], report) -> None:
    """Keep both sides' fingerprints after a write. The Archicad side is read back, since
    Archicad may reorder what it was given. The Blender side did not change by pushing."""
    if not stored:
        return
    back = api.read([guid for _p, guid in stored])
    for planned, guid in stored:
        obj = planned.obj
        obj[PROP_GUID] = guid
        if PROP_REMOVED in obj:
            del obj[PROP_REMOVED]
        obj[PROP_AC_HASH] = back[guid].fingerprint()
        obj[PROP_BL_HASH] = planned.fingerprint
        report.changed_guids.append(guid)


def _write_creates(
    api, creates: list[Planned], offset, report, counts
) -> list[tuple[Planned, str]]:
    if not creates:
        return []
    off = Vector(offset)
    items = [(p.geometry, tuple(p.obj.matrix_world.translation - off)) for p in creates]
    done = []
    for planned, result in zip(creates, api.create(items)):
        if isinstance(result, Exception):
            counts.created -= 1
            counts.failed += 1
            report.warnings.append(f"{planned.obj.name}: Archicad refused the morph ({result})")
        else:
            done.append((planned, result))
    return done


def _write_updates(api, updates: list[Planned], report, counts) -> list[tuple[Planned, str]]:
    if not updates:
        return []
    guids = [str(p.obj[PROP_GUID]).upper() for p in updates]
    placements = api.details(guids)
    sendable = []
    for planned, guid in zip(updates, guids):
        if guid in placements:
            sendable.append((planned, guid))
        else:
            counts.updated -= 1
            counts.failed += 1
            report.warnings.append(f"{planned.obj.name}: its morph vanished from Archicad, skipped")
    results = api.modify([(g, p.geometry, placements[g].origin) for p, g in sendable])
    done = []
    for (planned, guid), error in zip(sendable, results):
        if error is not None:
            counts.updated -= 1
            counts.failed += 1
            report.warnings.append(f"{planned.obj.name}: Archicad refused the change ({error})")
        else:
            done.append((planned, guid))
    return done


# -- collection sync -------------------------------------------------------------------------


def collection_objects(name: str) -> list[bpy.types.Object]:
    """The pushable mesh objects of a collection, sub-collections included."""
    collection = bpy.data.collections.get(name)
    return [o for o in collection.all_objects if can_push(o)] if collection else []


def _merge_warnings(warnings: list[str]) -> list[str]:
    """Each half of a sync reports a conflict; list it once, with advice that fits a sync."""
    merged: list[str] = []
    for warning in warnings:
        if CONFLICT_MARK in warning:
            name = warning.split(": ", 1)[0]
            warning = (
                f"{name}: {CONFLICT_MARK}. Choose a side with Push or Pull, with 'If both "
                "changed' set to Overwrite."
            )
        if warning not in merged:
            merged.append(warning)
    return merged


def merge_reports(pulled: engine.Report, pushed: engine.Report) -> engine.Report:
    """One report for a two-way sync; a warning both halves raised is listed once."""
    merged = engine.Report(dry_run=pulled.dry_run)
    merged.diff_summary = f"Pull: {pulled.diff_summary}. Push: {pushed.diff_summary}"
    merged.lines = pulled.lines + pushed.lines
    merged.warnings = _merge_warnings(pulled.warnings + pushed.warnings)
    merged.created_materials = list(dict.fromkeys(pulled.created_materials + pushed.created_materials))
    merged.missing_materials = list(dict.fromkeys(pulled.missing_materials + pushed.missing_materials))
    merged.changed_guids = pulled.changed_guids + pushed.changed_guids
    return merged
