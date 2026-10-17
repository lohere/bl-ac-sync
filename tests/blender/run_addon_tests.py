"""Headless tests of the Blender add-on.

    blender --background --factory-startup --python tests/blender/run_addon_tests.py

ifcopenshell must be importable inside Blender: put an unpacked wheel on ACSYNC_EXTRA_PATHS.
Set ACSYNC_TEST_IFC to also run the real-model test. Exit code is non-zero on any failure.
"""

import os
import sys
import tempfile
import traceback

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "blender_addon"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import ac_sync
import bpy
from ac_sync import engine
from ifc_fixture import DUP_ID, SLAB_GUID, WALL_GUID, WINDOW_GUID

TMP = tempfile.mkdtemp(prefix="acsync_test_")
FIXTURES = os.environ["ACSYNC_FIXTURE_DIR"]  # built by run.py (needs shapely, so not in Blender)
ROOT_NAME = "AC_Sync"


def fresh():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    s = bpy.context.scene.acsync
    s.source = "FILE"
    s.root_collection = ROOT_NAME
    return s


def ifc(name):
    return os.path.join(FIXTURES, name + ".ifc")


def sync(s, path, dry=False):
    s.ifc_path = path
    result = bpy.ops.acsync.sync("EXEC_DEFAULT", dry_run=dry, blocking=True)
    assert result == {"FINISHED"}, f"sync returned {result}"
    return s.last_summary


def objs():
    return engine.managed_objects(ROOT_NAME)


def zrange(obj):
    zs = [v.co.z for v in obj.data.vertices]
    return min(zs), max(zs)


# -- tests -----------------------------------------------------------------------------------


def test_dry_run_changes_nothing():
    s = fresh()
    summary = sync(s, ifc("a"), dry=True)
    assert "3 added" in summary, summary
    assert not objs()
    assert not bpy.data.collections.get(ROOT_NAME)
    assert not bpy.data.meshes and not bpy.data.materials


def test_first_sync_creates_objects_collections_materials():
    s = fresh()
    sync(s, ifc("a"))
    o = objs()
    assert set(o) == {WALL_GUID, SLAB_GUID, WINDOW_GUID}
    wall = o[WALL_GUID]
    assert wall.name == "WALL-0001" and wall["acsync_id"] == "WALL-0001"
    assert wall.users_collection[0].name == "AC_Sync - Walls"
    assert o[WINDOW_GUID].users_collection[0].name == "AC_Sync - Windows"
    assert bpy.data.collections["AC_Sync - Walls"] in bpy.data.collections[ROOT_NAME].children_recursive
    # wall: 2 materials, faces split between them, geometry in metres
    names = [m.name for m in wall.data.materials]
    assert names == ["Brick - Aged Light", "Stucco - White Fine"], names
    used = {p.material_index for p in wall.data.polygons}
    assert used == {0, 1}
    assert all(len(p.vertices) == 4 for p in wall.data.polygons)  # quads kept
    lo, hi = zrange(wall)
    assert abs(hi - lo - 3.0) < 1e-4, (lo, hi)
    # colours and transparency came along
    glass = bpy.data.materials["Glass - Clear Fast"]
    assert abs(glass.diffuse_color[3] - 0.3) < 1e-4
    # the settings list the surfaces for the mapping table
    assert {r.name for r in s.surfaces} == {
        "Brick - Aged Light", "Stucco - White Fine", "Glass - Clear Fast"}
    # zones, voids and untagged elements were not imported
    assert not [x for x in bpy.data.objects if "ZONE" in x.name or "9999" in x.name]


def test_second_sync_is_a_noop():
    s = fresh()
    path = ifc("a")
    sync(s, path)
    before = {g: (o.data.name, len(o.data.vertices)) for g, o in objs().items()}
    summary = sync(s, path)
    assert "0 added, 0 geometry changed" in summary and "3 unchanged" in summary, summary
    assert before == {g: (o.data.name, len(o.data.vertices)) for g, o in objs().items()}


def test_user_work_survives_a_geometry_update():
    s = fresh()
    sync(s, ifc("a"))
    wall = objs()[WALL_GUID]
    mesh_before = wall.data
    # the user: own material in slot 0, a modifier, an own slot, a custom property
    mine = bpy.data.materials.new("_MyBrick")
    wall.data.materials[0] = mine
    wall.modifiers.new("Bevel", "BEVEL")
    wall.data.materials.append(bpy.data.materials.new("_Extra"))
    wall["my_note"] = "keep me"
    summary = sync(s, ifc("b"))
    assert "1 geometry changed" in summary, summary
    wall = objs()[WALL_GUID]
    assert wall.data is mesh_before  # same mesh datablock
    lo, hi = zrange(wall)
    assert abs(hi - lo - 3.5) < 1e-4, (lo, hi)
    assert wall.data.materials[0] is mine  # user's material not overwritten
    assert wall.data.materials[1].name == "Stucco - White Fine"
    assert wall.data.materials[2].name == "_Extra"
    assert {p.material_index for p in wall.data.polygons} == {0, 1}
    assert [m.type for m in wall.modifiers] == ["BEVEL"]
    assert wall["my_note"] == "keep me"


def test_removed_element_policies():
    for policy, check in (
        ("MOVE", lambda o, root: o.users_collection[0].name == "AC_Sync - Removed"),
        ("HIDE", lambda o, root: o.hide_get() is True or o.hide_viewport),
        ("REPORT", lambda o, root: o.users_collection[0].name == "AC_Sync - Windows"),
    ):
        s = fresh()
        s.removed_policy = policy
        sync(s, ifc("a"))
        summary = sync(s, ifc("nowin"))
        assert "1 removed" in summary, (policy, summary)
        win = objs()[WINDOW_GUID]
        assert check(win, None), policy
        assert win["acsync_removed"]
        # second sync does not report it again
        assert "0 removed" in sync(s, ifc("nowin"))
        # element comes back -> restored to its type collection and visible
        sync(s, ifc("a"))
        win = objs()[WINDOW_GUID]
        assert not win.get("acsync_removed")
        assert win.users_collection[0].name == "AC_Sync - Windows"
        assert not win.hide_get()
    s = fresh()
    s.removed_policy = "DELETE"
    sync(s, ifc("a"))
    sync(s, ifc("nowin"))
    assert WINDOW_GUID not in objs()
    assert not [m for m in bpy.data.meshes if m.name.startswith("WNDW")]


def test_duplicate_element_ids_are_two_objects():
    s = fresh()
    other = "EEEEEEEE-0000-4000-8000-000000000005"
    sync(s, ifc("dup"))
    o = objs()
    assert WALL_GUID in o and other in o
    assert o[other]["acsync_id"] == DUP_ID
    # a second sync keeps both, unchanged
    assert "0 added" in sync(s, ifc("dup"))
    assert len(objs()) == 4


def test_objects_outside_the_root_are_never_touched():
    s = fresh()
    sync(s, ifc("a"))
    bystander = bpy.data.objects.new("MyOwnCube", bpy.data.meshes.new("MyOwnCube"))
    bpy.context.scene.collection.objects.link(bystander)
    bystander["acsync_guid"] = WALL_GUID  # same key, but not our root -> must be ignored
    sync(s, ifc("nowin"))
    assert bystander.name == "MyOwnCube"
    assert "acsync_removed" not in bystander


def test_offset_change_moves_everything():
    s = fresh()
    path = ifc("a")
    sync(s, path)
    z0 = zrange(objs()[SLAB_GUID])
    s.offset = (0.0, 0.0, 10.0)
    summary = sync(s, path)
    assert "3 geometry changed" in summary, summary
    z1 = zrange(objs()[SLAB_GUID])
    assert abs((z1[0] - z0[0]) - 10.0) < 1e-4


def test_material_map_uses_existing_material_and_never_overrides_user_choice():
    s = fresh()
    custom = bpy.data.materials.new("_Brick")
    row = s.surfaces.add()
    row.name, row.blender_material = "Brick - Aged Light", "_Brick"
    sync(s, ifc("a"))
    wall = objs()[WALL_GUID]
    assert wall.data.materials[0] is custom  # mapped on creation, nothing new created
    assert "Brick - Aged Light" not in bpy.data.materials
    # later mapping for plaster: only tool-created materials are switched
    plaster = bpy.data.materials.new("_Plaster")
    for r in s.surfaces:
        if r.name == "Stucco - White Fine":
            r.blender_material = "_Plaster"
    slab = objs()[SLAB_GUID]
    mine = bpy.data.materials.new("_SlabMine")
    slab.data.materials[0] = mine  # user's own choice on the slab
    assert bpy.ops.acsync.apply_material_map("EXEC_DEFAULT") == {"FINISHED"}
    assert wall.data.materials[1] is plaster
    assert slab.data.materials[0] is mine


def test_unmapped_material_prefix_and_missing_target_reported():
    s = fresh()
    s.material_prefix = "AC_"
    row = s.surfaces.add()
    row.name, row.blender_material = "Glass - Clear Fast", "DoesNotExist"
    sync(s, ifc("a"))
    assert "AC_Brick - Aged Light" in bpy.data.materials
    assert "DoesNotExist" in bpy.data.materials  # created, and listed in the report
    assert "DoesNotExist" in bpy.data.texts["AC Sync Report"].as_string()


def test_bad_inputs_fail_cleanly():
    s = fresh()
    # bpy.ops raises RuntimeError when an operator reports an ERROR
    for path, text in ((os.path.join(TMP, "missing.ifc"), "not found"), ("", "Choose an IFC")):
        s.ifc_path = path
        try:
            bpy.ops.acsync.sync("EXEC_DEFAULT", blocking=True)
        except RuntimeError as exc:
            assert text in str(exc), str(exc)
        else:
            raise AssertionError("expected an error")
    assert not objs()


def test_terrain_site_is_synced_and_skippable():
    s = fresh()
    sync(s, ifc("terrain"))
    sites = [o for o in objs().values() if o["acsync_type"] == "IfcSite"]
    assert len(sites) == 1 and sites[0].name == "Site"
    assert sites[0].users_collection[0].name == "AC_Sync - Terrain"
    assert len(sites[0].data.polygons) == 6
    assert "0 added, 0 geometry changed" in sync(s, ifc("terrain"))  # stable on re-sync
    s2 = fresh()
    s2.skip_site = True
    sync(s2, ifc("terrain"))
    assert not [o for o in objs().values() if o["acsync_type"] == "IfcSite"]


def test_archicad_coordinates_toggle():
    s = fresh()
    sync(s, ifc("a"))
    plain = [tuple(v.co) for v in objs()[SLAB_GUID].data.vertices]
    s = fresh()
    sync(s, ifc("rot"))  # default: undo the IfcSite rotation
    restored = [tuple(v.co) for v in objs()[SLAB_GUID].data.vertices]
    assert all(abs(a - b) < 1e-4 for p, r in zip(plain, restored) for a, b in zip(p, r))
    s.archicad_coordinates = False  # same file, IFC world coordinates -> every object changes
    summary = sync(s, ifc("rot"))
    assert "3 geometry changed" in summary, summary
    rotated = [tuple(v.co) for v in objs()[SLAB_GUID].data.vertices]
    assert any(abs(a - b) > 0.1 for p, r in zip(plain, rotated) for a, b in zip(p, r))


def test_real_model():
    path = os.environ.get("ACSYNC_TEST_IFC")
    if not path:
        return "skipped"
    s = fresh()
    sync(s, path)
    n = len(objs())
    assert n > 100, n
    summary = sync(s, path)
    assert "0 added, 0 geometry changed" in summary, summary
    walls = [o for o in objs().values() if o["acsync_type"] == "IfcWall"]
    assert walls and all(o.data.polygons for o in walls)
    return f"{n} objects"


# -- runner ----------------------------------------------------------------------------------


def main():
    ac_sync.register()
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    tests.sort(key=lambda nf: nf[1].__code__.co_firstlineno)
    failed = 0
    for name, fn in tests:
        try:
            note = fn()
            print(f"PASS {name}" + (f" ({note})" if note else ""))
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.stdout.flush()
    os._exit(1 if failed else 0)


main()
