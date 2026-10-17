"""Apply an IFC model to the Blender scene. All bpy work happens here.

Bookkeeping lives in custom properties on the objects the tool creates:

``acsync_root``    name of the root collection (so several projects can coexist)
``acsync_guid``    Archicad element GUID, the matching key
``acsync_id``      Archicad Element ID (label only)
``acsync_hash``    geometry hash of the last applied state
``acsync_offset``  model offset in effect when it was applied
``acsync_slots``   JSON list: which Archicad surface each material slot stands for
``acsync_removed`` set when the element disappeared from Archicad

Rules (see docs/ARCHITECTURE.md): only objects carrying ``acsync_root`` == our root are ever
touched; an update replaces vertices and faces inside the existing mesh datablock, so
modifiers, parenting, names and the user's material choices survive; material slots the user
re-assigned are never overwritten.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import bpy

from acsync import Diff, MaterialMap, SceneRecord, compute_diff
from acsync.ifc_reader import Element, IfcModel, SurfaceStyle

PROP_ROOT = "acsync_root"
PROP_GUID = "acsync_guid"
PROP_ID = "acsync_id"
PROP_HASH = "acsync_hash"
PROP_OFFSET = "acsync_offset"
PROP_SLOTS = "acsync_slots"
PROP_REMOVED = "acsync_removed"

TYPE_COLLECTIONS = {
    "IfcWall": "Walls",
    "IfcWallStandardCase": "Walls",
    "IfcSlab": "Slabs",
    "IfcRoof": "Roofs",
    "IfcBeam": "Beams",
    "IfcColumn": "Columns",
    "IfcMember": "Members",
    "IfcPlate": "Plates",
    "IfcWindow": "Windows",
    "IfcDoor": "Doors",
    "IfcCurtainWall": "Curtain Walls",
    "IfcStair": "Stairs",
    "IfcStairFlight": "Stairs",
    "IfcRailing": "Railings",
    "IfcCovering": "Coverings",
    "IfcSite": "Terrain",
    "IfcSpace": "Zones",
    "IfcBuildingElementProxy": "Objects",
    "IfcFurnishingElement": "Objects",
    "IfcDistributionFlowElement": "Objects",
}
OTHER_COLLECTION = "Other"


@dataclass
class Report:
    dry_run: bool = False
    diff_summary: str = ""
    lines: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    created_materials: list[str] = field(default_factory=list)
    missing_materials: list[str] = field(default_factory=list)

    def add(self, text: str):
        self.lines.append(text)

    def text(self) -> str:
        head = ("DRY RUN - nothing was changed\n" if self.dry_run else "") + self.diff_summary
        parts = [head]
        if self.warnings:
            parts += ["", "Warnings:"] + [f"  {w}" for w in self.warnings]
        if self.created_materials:
            parts += ["", "New materials created: " + ", ".join(self.created_materials)]
        if self.missing_materials:
            parts += ["", "Mapped materials not found (created instead): "
                      + ", ".join(self.missing_materials)]
        if self.lines:
            parts += ["", "Details:"] + [f"  {line}" for line in self.lines]
        return "\n".join(parts)


# -- collections -----------------------------------------------------------------------------


def _get_or_create_collection(name: str, parent: bpy.types.Collection) -> bpy.types.Collection:
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
    if coll.name not in parent.children:
        try:
            parent.children.link(coll)
        except RuntimeError:
            pass  # already linked elsewhere in the hierarchy (e.g. a cycle); leave it
    return coll


def root_collection(scene: bpy.types.Scene, name: str) -> bpy.types.Collection:
    root = bpy.data.collections.get(name)
    if root is None:
        root = bpy.data.collections.new(name)
    if root not in scene.collection.children_recursive:
        scene.collection.children.link(root)
    return root


def type_collection(root: bpy.types.Collection, ifc_type: str) -> bpy.types.Collection:
    label = TYPE_COLLECTIONS.get(ifc_type, OTHER_COLLECTION)
    return _get_or_create_collection(f"{root.name} - {label}", root)


def removed_collection(root: bpy.types.Collection) -> bpy.types.Collection:
    return _get_or_create_collection(f"{root.name} - Removed", root)


# -- scene records ---------------------------------------------------------------------------


def _offset_key(offset) -> str:
    return ",".join(f"{v:.4f}" for v in offset)


def managed_objects(root_name: str) -> dict[str, bpy.types.Object]:
    found: dict[str, bpy.types.Object] = {}
    for obj in bpy.data.objects:
        if obj.get(PROP_ROOT) == root_name and obj.get(PROP_GUID):
            found.setdefault(obj[PROP_GUID], obj)
    return found


def scene_records(objects: dict[str, bpy.types.Object], offset) -> dict[str, SceneRecord]:
    key = _offset_key(offset)
    records = {}
    for guid, obj in objects.items():
        stored = obj.get(PROP_HASH, "")
        if obj.get(PROP_OFFSET, "") != key:
            stored += "!offset"  # forces an update when the model offset changed
        records[guid] = SceneRecord(guid, obj.get(PROP_ID, ""), stored)
    return records


# -- materials -------------------------------------------------------------------------------


def _new_material(name: str, surface: SurfaceStyle) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    r, g, b = surface.diffuse
    alpha = max(0.0, min(1.0, 1.0 - surface.transparency))
    mat.diffuse_color = (r, g, b, alpha)
    try:
        mat.use_nodes = True  # deprecated no-op on newer Blender
    except Exception:
        pass
    tree = mat.node_tree
    if tree is not None:
        bsdf = tree.nodes.get("Principled BSDF")
        if bsdf is not None:
            bsdf.inputs["Base Color"].default_value = (r, g, b, 1.0)
            if "Alpha" in bsdf.inputs:
                bsdf.inputs["Alpha"].default_value = alpha
    if alpha < 1.0:
        try:
            mat.surface_render_method = "BLENDED"
        except (AttributeError, TypeError):
            pass
    mat["acsync_created"] = True
    mat["acsync_surface"] = surface.name
    return mat


def material_for(surface: SurfaceStyle, mapping: MaterialMap, report: Report) -> bpy.types.Material:
    target = mapping.target_for(surface.name)
    mat = bpy.data.materials.get(target)
    if mat is not None:
        return mat
    if mapping.is_mapped(surface.name) and target not in report.missing_materials:
        report.missing_materials.append(target)
    mat = _new_material(target, surface)
    if mat.name not in report.created_materials:
        report.created_materials.append(mat.name)
    return mat


def _load_slots(obj) -> list[str]:
    try:
        slots = json.loads(obj.get(PROP_SLOTS, "[]"))
    except (TypeError, ValueError):
        slots = []
    mesh = obj.data
    slots = list(slots)[: len(mesh.materials)]
    slots += [""] * (len(mesh.materials) - len(slots))  # slots the user added themselves
    return slots


def _slot_index_for(obj, surface: SurfaceStyle, slots: list[str], mapping, report) -> int:
    if surface.name in slots:
        return slots.index(surface.name)
    obj.data.materials.append(material_for(surface, mapping, report))
    slots.append(surface.name)
    return len(slots) - 1


# -- geometry --------------------------------------------------------------------------------


def _write_geometry(obj, element: Element, mapping: MaterialMap, offset, report: Report):
    mesh = obj.data
    ox, oy, oz = offset
    v = element.vertices
    verts = [(v[i] + ox, v[i + 1] + oy, v[i + 2] + oz) for i in range(0, len(v), 3)]
    faces = []
    pos = 0
    idx = element.face_indices
    for size in element.face_sizes:
        faces.append(idx[pos:pos + size])
        pos += size

    slots = _load_slots(obj)
    slot_of_surface = [
        _slot_index_for(obj, s, slots, mapping, report) for s in element.surfaces
    ]

    mesh.clear_geometry()  # keeps the material slots
    mesh.from_pydata(verts, [], faces)
    if len(mesh.polygons) == len(element.face_materials) and slot_of_surface:
        indices = [slot_of_surface[m] if 0 <= m < len(slot_of_surface) else 0
                   for m in element.face_materials]
        mesh.polygons.foreach_set("material_index", indices)
    mesh.update()
    obj[PROP_SLOTS] = json.dumps(slots)
    obj[PROP_HASH] = element.geometry_hash
    obj[PROP_OFFSET] = _offset_key(offset)
    obj[PROP_ID] = element.element_id


# -- apply -----------------------------------------------------------------------------------


def _create_object(element, root, mapping, offset, report):
    mesh = bpy.data.meshes.new(element.element_id or element.guid)
    obj = bpy.data.objects.new(element.element_id or element.guid, mesh)
    obj[PROP_ROOT] = root.name
    obj[PROP_GUID] = element.guid
    obj["acsync_type"] = element.ifc_type
    type_collection(root, element.ifc_type).objects.link(obj)
    _write_geometry(obj, element, mapping, offset, report)
    return obj


def _update_object(change, obj, root, mapping, offset, report):
    element = change.element
    if change.geometry_changed:
        _write_geometry(obj, element, mapping, offset, report)
    if change.label_changed:
        old_label = obj.get(PROP_ID, "")
        obj[PROP_ID] = element.element_id
        if obj.name == old_label:  # only rename if the user has not renamed it themselves
            obj.name = element.element_id
            report.add(f"{old_label} renamed to {element.element_id}")


def _restore_if_removed(obj, element, root):
    if obj.get(PROP_REMOVED):
        del obj[PROP_REMOVED]
        obj.hide_set(False)
        obj.hide_render = False
        removed = bpy.data.collections.get(f"{root.name} - Removed")
        if removed is not None and obj.name in removed.objects:
            removed.objects.unlink(obj)
            type_collection(root, element.ifc_type).objects.link(obj)


def _handle_removed(record, obj, policy, root, report):
    label = record.element_id or record.guid
    if obj.get(PROP_REMOVED):
        return  # already handled in an earlier sync
    if policy == "DELETE":
        mesh = obj.data
        bpy.data.objects.remove(obj)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        report.add(f"{label}: removed in Archicad, deleted")
        return
    obj[PROP_REMOVED] = True
    if policy == "MOVE":
        target = removed_collection(root)
        for coll in list(obj.users_collection):
            coll.objects.unlink(obj)
        target.objects.link(obj)
        report.add(f"{label}: removed in Archicad, moved to '{target.name}'")
    elif policy == "HIDE":
        obj.hide_set(True)
        obj.hide_render = True
        report.add(f"{label}: removed in Archicad, hidden")
    else:
        report.add(f"{label}: removed in Archicad, left untouched")


def plan(scene, settings, model: IfcModel) -> tuple[Diff, dict[str, bpy.types.Object]]:
    objects = managed_objects(settings.root_collection)
    records = scene_records(objects, tuple(settings.offset))
    return compute_diff(model.elements, records), objects


def apply_model(scene, settings, model: IfcModel, dry_run: bool = False) -> Report:
    report = Report(dry_run=dry_run)
    for w in model.warnings:
        report.warnings.append(w)
    diff, objects = plan(scene, settings, model)
    removed = [r for r in diff.removed if not objects[r.guid].get(PROP_REMOVED)]
    report.diff_summary = diff.summary().replace(
        f"{len(diff.removed)} removed", f"{len(removed)} removed"
    )
    if dry_run:
        for e in diff.added:
            report.add(f"{e.element_id} ({e.ifc_type}): would be added")
        for c in diff.changed:
            what = "geometry" if c.geometry_changed else "label"
            report.add(f"{c.element.element_id}: {what} would change")
        for r in removed:
            report.add(f"{r.element_id}: removed in Archicad ({settings.removed_policy})")
        return report

    root = root_collection(scene, settings.root_collection)
    mapping = settings.material_map()
    offset = tuple(settings.offset)

    for element in diff.added:
        _create_object(element, root, mapping, offset, report)
    for change in diff.changed:
        obj = objects[change.element.guid]
        _restore_if_removed(obj, change.element, root)
        _update_object(change, obj, root, mapping, offset, report)
    for element in diff.unchanged:
        _restore_if_removed(objects[element.guid], element, root)
    for record in removed:
        _handle_removed(record, objects[record.guid], settings.removed_policy, root, report)

    settings.sync_surfaces(model.surface_names())
    return report


def apply_material_map(settings) -> tuple[int, list[str]]:
    """Point slots that still hold a tool-created material to the mapped Blender material."""
    mapping = settings.material_map()
    changed = 0
    missing = []
    for obj in managed_objects(settings.root_collection).values():
        slots = _load_slots(obj)
        for i, surface_name in enumerate(slots):
            if not surface_name or not mapping.is_mapped(surface_name):
                continue
            current = obj.data.materials[i] if i < len(obj.data.materials) else None
            if current is None or not current.get("acsync_created"):
                continue  # the user picked this material; leave it alone
            target = bpy.data.materials.get(mapping.target_for(surface_name))
            if target is None:
                if mapping.target_for(surface_name) not in missing:
                    missing.append(mapping.target_for(surface_name))
                continue
            if current is not target:
                obj.data.materials[i] = target
                changed += 1
    return changed, missing
