"""Headless Blender tests of the morph tool, against an in-memory stand-in for Archicad.

    blender --background --factory-startup --python tests/blender/run_morph_tests.py

(run.py starts it.) The stand-in mimics what was measured on Archicad 29 / Tapir 1.8.0: it keeps
the GUID on a modify and hands the vertices back in a different order after a create.
"""

import os
import sys
import traceback

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "blender_addon"))

import ac_sync
import bpy
from ac_sync import morph_engine as me
from ac_sync import morph_ops
from ac_sync.acsync import MorphDetails, MorphFace, MorphGeometry, SurfaceInfo, polygon_normal

REAL_CONNECT = morph_ops._connect
REAL_PICK_CLIENT = ac_sync.operators._pick_client
RED, BLUE, GREY = "G-RED", "G-BLUE", "G-GREY"
SURFACES = {RED: "Brick - Red", BLUE: "Glass - Blue", GREY: "Paint - Light Gray"}


class FakeArchicad:
    """Just enough of MorphApi: keeps morphs by GUID, like Archicad does."""

    def __init__(self):
        self.morphs: dict[str, MorphGeometry] = {}
        self.origins: dict[str, tuple] = {}
        self.ids: dict[str, str] = {}
        self.selection: list[str] = []
        self.refuse_create = False
        self._n = 0

    def add(self, geometry, origin=(0.0, 0.0, 0.0), element_id=None):
        self._n += 1
        guid = f"M-{self._n:04d}"
        self.morphs[guid] = geometry
        self.origins[guid] = origin
        self.ids[guid] = element_id or f"MORPH-{self._n:03d}"
        return guid

    def list_guids(self):
        return list(self.morphs)

    def selected_morphs(self):
        return [g for g in self.selection if g in self.morphs]

    def read(self, guids):
        return {g: self.morphs[g] for g in guids}

    def details(self, guids):
        return {g: MorphDetails(self.ids[g], self.origins[g]) for g in guids}

    def surface_names(self):
        return dict(SURFACES)

    def surface_details(self, guids):
        return {g: SurfaceInfo(g, SURFACES[g], (0.5, 0.1, 0.1)) for g in guids if g in SURFACES}

    def create(self, items):
        if self.refuse_create:
            return [RuntimeError("no way") for _ in items]
        out = []
        for geometry, origin in items:
            n = len(geometry.vertices)  # Archicad reorders: simulate by reversing
            flipped = MorphGeometry(
                list(reversed(geometry.vertices)),
                [MorphFace([n - 1 - i for i in f.loop],
                           [[n - 1 - i for i in h] for h in f.holes], f.surface or GREY)
                 for f in geometry.faces],
            )
            out.append(self.add(flipped, origin))
        return out

    def modify(self, items):
        for guid, geometry, _origin in items:
            self.morphs[guid] = MorphGeometry(
                geometry.vertices, [MorphFace(f.loop, f.holes, f.surface or GREY) for f in geometry.faces]
            )
        return [None] * len(items)


FAKE = FakeArchicad()


def fresh():
    global FAKE
    bpy.ops.wm.read_factory_settings(use_empty=True)
    FAKE = FakeArchicad()
    morph_ops._connect = lambda context: (FAKE, "")  # no real Archicad needed
    return bpy.context.scene


def expect_error(text, operator, *args):
    """Operators that report an error raise RuntimeError when called from a script."""
    try:
        operator(*args)
    except RuntimeError as exc:
        assert text in str(exc), str(exc)
        return
    raise AssertionError(f"expected an error containing {text!r}")


def make_mesh(name, verts, faces, location=(0, 0, 0)):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    bpy.context.view_layer.update()
    return obj


CUBE_V = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
CUBE_F = [(3, 2, 1, 0), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]


def cube(name="Cube", location=(0, 0, 0)):
    return make_mesh(name, CUBE_V, CUBE_F, location)


def select_only(*objs):
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)


def push(objs, dry=False):
    scene = bpy.context.scene
    return me.push(scene, scene.acsync, scene.acmorph, FAKE, objs, dry)


def pull(dry=False):
    scene = bpy.context.scene
    return me.pull(scene, scene.acsync, scene.acmorph, FAKE, dry)


def world_vertices(obj):
    return sorted(tuple(round(c, 6) for c in obj.matrix_world @ v.co) for v in obj.data.vertices)


def ac_vertices(guid):
    return sorted(tuple(round(c, 6) for c in v) for v in FAKE.morphs[guid].vertices)


def assert_faces_point_outward(geometry):
    n = len(geometry.vertices)
    cx, cy, cz = (sum(v[i] for v in geometry.vertices) / n for i in range(3))
    for face in geometry.faces:
        points = [geometry.vertices[i] for i in face.loop]
        nx, ny, nz = polygon_normal(points)
        fx, fy, fz = (sum(p[i] for p in points) / len(points) for i in range(3))
        assert nx * (fx - cx) + ny * (fy - cy) + nz * (fz - cz) > 0, "a face points inward"


def box_geometry(origin=(0.0, 0.0, 0.0), size=1.0, surface=RED):
    ox, oy, oz = origin
    verts = [(ox + x * size, oy + y * size, oz + z * size) for x, y, z in CUBE_V]
    return MorphGeometry(verts, [MorphFace(list(f), surface=surface) for f in CUBE_F])


# -- tests -----------------------------------------------------------------------------------


def test_push_creates_a_morph_and_a_second_push_changes_nothing():
    fresh()
    obj = cube(location=(2, 3, 0))
    report = push([obj])
    assert "1 created" in report.diff_summary, report.diff_summary
    guid = obj[me.PROP_GUID]
    assert list(FAKE.morphs) == [guid]
    assert ac_vertices(guid) == world_vertices(obj)  # world coordinates, no offset
    assert_faces_point_outward(FAKE.morphs[guid])
    assert FAKE.origins[guid] == (2.0, 3.0, 0.0)  # the morph's pivot is the object's origin
    again = push([obj])
    assert "0 created, 0 updated, 1 unchanged" in again.diff_summary, again.diff_summary


def test_blender_edit_updates_the_same_morph():
    fresh()
    obj = cube()
    push([obj])
    guid = obj[me.PROP_GUID]
    obj.data.vertices[6].co.z += 2.0
    obj.data.update()
    report = push([obj])
    assert "1 updated" in report.diff_summary, report.diff_summary
    assert obj[me.PROP_GUID] == guid and list(FAKE.morphs) == [guid]
    assert ac_vertices(guid) == world_vertices(obj)
    assert "1 unchanged" in push([obj]).diff_summary


def test_archicad_edit_is_pulled_in_place():
    fresh()
    obj = cube()
    push([obj])
    guid = obj[me.PROP_GUID]
    moved = [(x + 5.0, y, z) for x, y, z in FAKE.morphs[guid].vertices]
    FAKE.morphs[guid] = MorphGeometry(moved, FAKE.morphs[guid].faces)
    assert "changed in Archicad only" in "\n".join(push([obj]).lines)
    report = pull()
    assert "1 changed" in report.diff_summary, report.diff_summary
    assert world_vertices(obj) == ac_vertices(guid)
    assert "1 unchanged" in pull().diff_summary
    assert "1 unchanged" in push([obj]).diff_summary


def test_vertex_data_survives_a_pull_that_keeps_the_topology():
    fresh()
    guid = FAKE.add(box_geometry())
    pull()
    obj = me.morph_objects()[guid]
    obj.vertex_groups.new(name="keep").add([0, 1, 2], 0.5, "REPLACE")
    # Archicad edit that moves vertices but keeps every face the same
    FAKE.morphs[guid] = MorphGeometry(
        [(x, y, z * 2) for x, y, z in FAKE.morphs[guid].vertices], FAKE.morphs[guid].faces
    )
    assert "1 changed" in pull().diff_summary
    assert max(v.co.z for v in obj.data.vertices) == 2.0
    weights = {v.index: v.groups[0].weight for v in obj.data.vertices if v.groups}
    assert weights == {0: 0.5, 1: 0.5, 2: 0.5}, weights


def test_both_sides_changed_is_a_conflict_until_overwrite():
    fresh()
    obj = cube()
    push([obj])
    guid = obj[me.PROP_GUID]
    obj.data.vertices[0].co.x -= 1.0
    obj.data.update()
    FAKE.morphs[guid] = MorphGeometry(
        [(x, y, z + 3) for x, y, z in FAKE.morphs[guid].vertices], FAKE.morphs[guid].faces
    )
    before_ac = ac_vertices(guid)
    bpy.context.view_layer.update()
    report = push([obj])
    assert "1 conflict(s) skipped" in report.diff_summary and report.warnings
    assert ac_vertices(guid) == before_ac
    before_bl = world_vertices(obj)
    assert "1 conflict(s) skipped" in pull().diff_summary
    assert world_vertices(obj) == before_bl
    bpy.context.scene.acmorph.conflict = "OVERWRITE"
    assert "1 updated" in push([obj]).diff_summary
    assert ac_vertices(guid) == world_vertices(obj)


def test_pull_makes_objects_with_pivot_name_and_materials():
    scene = fresh()
    hole = MorphGeometry(
        [(0, 0, 0), (4, 0, 0), (4, 4, 0), (0, 4, 0), (1, 1, 0), (1, 3, 0), (3, 3, 0), (3, 1, 0)],
        [MorphFace([0, 1, 2, 3], [[4, 5, 6, 7]], surface=BLUE)],
    )
    FAKE.add(box_geometry(origin=(2.0, 0.0, 0.0)), origin=(2.0, 0.0, 0.0), element_id="MORPH-007")
    FAKE.add(hole)
    report = pull()
    assert "2 new" in report.diff_summary, report.diff_summary
    objects = me.morph_objects()
    box = bpy.data.objects["MORPH-007"]
    assert [c.name for c in box.users_collection] == ["bl-ac morphs"]
    assert "bl-ac morphs" in scene.collection.children
    assert tuple(box.matrix_world.translation) == (2.0, 0.0, 0.0)
    assert world_vertices(box) == ac_vertices("M-0001")
    assert len(box.data.polygons) == 6 and box.material_slots[0].material.name == "Brick - Red"
    flat = next(o for o in objects.values() if o is not box)
    assert len(flat.data.polygons) == 8, len(flat.data.polygons)  # square ring -> 8 triangles
    assert "face(s) with holes" in report.text()
    assert flat.material_slots[0].material.name == "Glass - Blue"
    assert abs(sum(p.area for p in flat.data.polygons) - 12.0) < 1e-5  # 16 - 4


def test_pull_scope_selected_in_archicad():
    fresh()
    FAKE.add(box_geometry())
    two = FAKE.add(box_geometry(origin=(5.0, 0.0, 0.0)))
    FAKE.selection = [two]
    bpy.context.scene.acmorph.pull_scope = "SELECTED"
    pull()
    assert list(me.morph_objects()) == [two]


def test_dry_runs_change_nothing():
    fresh()
    FAKE.add(box_geometry())
    assert "1 new" in pull(dry=True).diff_summary and not bpy.data.objects
    obj = cube()
    report = push([obj], dry=True)
    assert "1 created" in report.diff_summary and not FAKE.morphs.get("x") and len(FAKE.morphs) == 1
    assert me.PROP_GUID not in obj


def test_model_offset_is_applied_both_ways():
    scene = fresh()
    scene.acsync.offset = (100.0, 0.0, 0.0)
    obj = cube(location=(105.0, 0.0, 0.0))
    push([obj])
    guid = obj[me.PROP_GUID]
    assert min(v[0] for v in FAKE.morphs[guid].vertices) == 5.0  # Blender 105 = Archicad 5
    assert FAKE.origins[guid][0] == 5.0
    other = FAKE.add(box_geometry(origin=(1.0, 0.0, 0.0)), origin=(1.0, 0.0, 0.0))
    pull()
    assert min(v.co.x for v in me.morph_objects()[other].data.vertices) + me.morph_objects()[other].location.x == 101.0


def test_materials_resolve_to_surfaces_and_unmatched_ones_are_reported():
    fresh()
    obj = cube()
    red = bpy.data.materials.new("Brick - Red")
    other = bpy.data.materials.new("My wood")
    named = bpy.data.materials.new("Anything")
    named["acsync_surface"] = "Glass - Blue"  # recorded by the IFC sync
    for m in (red, other, named):
        obj.data.materials.append(m)
    obj.data.polygons.foreach_set("material_index", [0, 0, 1, 2, 2, 2])
    report = push([obj])
    faces = FAKE.morphs[obj[me.PROP_GUID]].faces
    surfaces = sorted(f.surface for f in faces)
    assert surfaces.count(RED) == 2 and surfaces.count(BLUE) == 3 and surfaces.count(GREY) == 1
    assert "My wood" in "\n".join(report.warnings)


def test_material_table_maps_blender_material_to_surface():
    scene = fresh()
    scene.acsync.surfaces.add().name = "Brick - Red"
    scene.acsync.surfaces[0].blender_material = "Rusty brick"
    obj = cube()
    obj.data.materials.append(bpy.data.materials.new("Rusty brick"))
    push([obj])
    assert {f.surface for f in FAKE.morphs[obj[me.PROP_GUID]].faces} == {RED}


def test_mirrored_objects_still_face_outward():
    fresh()
    obj = cube(location=(0, 0, 0))
    obj.scale = (-1.0, 1.0, 1.0)
    bpy.context.view_layer.update()
    push([obj])
    assert_faces_point_outward(FAKE.morphs[obj[me.PROP_GUID]])


def test_warped_quads_are_split_and_flat_ones_are_kept():
    fresh()
    warped = make_mesh("Warped", [(0, 0, 0), (1, 0, 0), (1, 1, 0.3), (0, 1, 0)], [(0, 1, 2, 3)])
    flat = make_mesh("Flat", [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [(0, 1, 2, 3)])
    report = push([warped, flat])
    assert len(FAKE.morphs[warped[me.PROP_GUID]].faces) == 2
    assert len(FAKE.morphs[flat[me.PROP_GUID]].faces) == 1
    assert "warped face(s)" in report.text()


def test_modifiers_are_applied_when_asked():
    scene = fresh()
    obj = cube()
    mod = obj.modifiers.new("Array", "ARRAY")
    mod.count = 2
    bpy.context.view_layer.update()
    push([obj])
    assert len(FAKE.morphs[obj[me.PROP_GUID]].faces) == 12
    scene.acmorph.apply_modifiers = False
    push([obj])  # a different thing is sent now, so it counts as a change
    assert len(FAKE.morphs[obj[me.PROP_GUID]].faces) == 6


def test_deleted_in_archicad_is_flagged_and_pushing_recreates():
    fresh()
    obj = cube()
    push([obj])
    old = obj[me.PROP_GUID]
    del FAKE.morphs[old]
    assert "1 gone from Archicad" in pull().diff_summary
    assert obj[me.PROP_REMOVED] and bpy.data.objects.get(obj.name)
    report = push([obj])
    assert "1 created" in report.diff_summary and obj[me.PROP_GUID] != old
    assert me.PROP_REMOVED not in obj


def test_refused_morph_is_reported_not_raised():
    fresh()
    FAKE.refuse_create = True
    obj = cube()
    report = push([obj])
    assert "1 failed" in report.diff_summary and "refused" in "\n".join(report.warnings)
    assert me.PROP_GUID not in obj


def test_objects_the_ifc_sync_manages_are_not_pushed():
    fresh()
    wall = cube("Wall")
    wall["acsync_guid"] = "SOMETHING"
    assert not me.can_push(wall) and me.can_push(cube("Plain"))
    empty = bpy.data.objects.new("Empty", None)
    assert not me.can_push(empty)


def test_operators_push_and_pull_through_the_panel_buttons():
    scene = fresh()
    obj = cube()
    select_only(obj)
    assert bpy.ops.acmorph.push("EXEC_DEFAULT") == {"FINISHED"}
    assert "1 created" in scene.acmorph.last_summary
    assert bpy.data.texts["bl-ac morphs Report"]
    assert bpy.ops.acmorph.pull("EXEC_DEFAULT", dry_run=True) == {"FINISHED"}
    assert scene.acmorph.last_summary.startswith("Dry run")
    select_only()
    expect_error("select mesh objects", bpy.ops.acmorph.push, "EXEC_DEFAULT")  # nothing selected
    scene.acmorph.push_scope = "COLLECTION"
    expect_error("no mesh objects in the collection", bpy.ops.acmorph.push, "EXEC_DEFAULT")


def test_old_tapir_gets_a_clear_message():
    fresh()
    class OldTapir:
        port = 1

        def require_tapir_version(self):
            return type("Info", (), {"tapir_version": "1.7.3"})()

    morph_ops._connect = REAL_CONNECT  # drop the test stand-in
    ac_sync.operators._pick_client = lambda port, hint=0: (OldTapir(), "")
    try:
        expect_error("1.8.0", bpy.ops.acmorph.pull, "EXEC_DEFAULT")
    finally:
        ac_sync.operators._pick_client = REAL_PICK_CLIENT
    assert "1.8.0" in ac_sync.operators.connection.message


def test_every_morph_setting_and_button_has_a_tooltip():
    props = bpy.types.Scene.bl_rna.properties["acmorph"].fixed_type.bl_rna.properties
    missing = [p.identifier for p in props
               if p.identifier not in ("rna_type", "name") and not p.is_hidden and not p.description]
    missing += [c.bl_idname for c in morph_ops.CLASSES[1:]
                if not (getattr(c, "bl_description", "") or "").strip()]
    for c in morph_ops.CLASSES[1:]:
        rna = getattr(bpy.ops.acmorph, c.bl_idname.split(".")[1]).get_rna_type()
        missing += [f"{c.__name__}.{p.identifier}" for p in rna.properties
                    if p.identifier not in ("rna_type", "name") and not p.is_hidden and not p.description]
    assert not missing, missing


# -- runner ----------------------------------------------------------------------------------


def main():
    ac_sync.register()
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    tests.sort(key=lambda nf: nf[1].__code__.co_firstlineno)
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.stdout.flush()
    os._exit(1 if failed else 0)


main()
