"""Manual check of the morph tool against a running Archicad (Tapir 1.8.0 or newer).

    blender --background --factory-startup --python tests/blender/live_morph_check.py

Needs ACSYNC_EXTRA_PATHS (see run.py). It CREATES AND CHANGES MORPHS in the open project, so it
refuses to run unless the project name contains ACSYNC_LIVE_PROJECT (default "untitled").
The morphs it makes are left in the project so they can be inspected.
"""

import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "blender_addon"))

import ac_sync
import bmesh
import bpy
from ac_sync import morph_engine as me
from ac_sync.acsync import MorphApi, TapirClient, find_instances

ac_sync.register()
bpy.ops.wm.read_factory_settings(use_empty=True)
want = os.environ.get("ACSYNC_LIVE_PROJECT", "untitled").lower()
found = find_instances()
name = (found[0].project_name or ("untitled" if found[0].is_untitled else "")) if found else ""
if not found or want not in name.lower():
    print("REFUSING: open project is", name or "none", flush=True)
    os._exit(2)

info = found[0]
api = MorphApi(TapirClient(info.port))
scene = bpy.context.scene
print(f"project: {name} | Archicad {info.archicad_version} | Tapir {info.tapir_version}")
print("surfaces in project:", len(api.surface_names()))


def run(label, op, **kw):
    t = time.perf_counter()
    try:
        result = op("EXEC_DEFAULT", **kw)
        text = scene.acmorph.last_summary
    except RuntimeError as exc:
        result, text = "ERROR", str(exc).strip()
    print(f"  {label}: {result} in {time.perf_counter() - t:.2f}s | {text}")
    return result


def body_state(guid):
    d = api.client.addon("GetDetailsOfElements", {"elements": [{"elementId": {"guid": guid}}]})
    body = d["detailsOfElements"][0]["details"]["body"]
    return f"{len(body['vertices'])} verts, {len(body.get('polygons', []))} faces, closed={body.get('isClosed')}, {body.get('bodyType')}"


def select(*objs):
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)


def mesh_object(name, verts, faces, location=(0, 0, 0)):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    obj.location = location
    bpy.context.view_layer.update()
    return obj


def push(label, *objs):
    select(*objs)
    return run(label, bpy.ops.acmorph.push)


before = set(api.list_guids())
print(f"\n1. pull the {len(before)} morph(s) already in Archicad")
run("pull", bpy.ops.acmorph.pull)
print("  objects:", [(o.name, len(o.data.polygons)) for o in bpy.data.objects])
run("pull again (should be all unchanged)", bpy.ops.acmorph.pull)

print("\n2. push a new cube with a named material, edit it, push again")
cube = mesh_object(
    "LiveCube",
    [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)],
    [(3, 2, 1, 0), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)],
    location=(0, -10, 0),
)
cube.data.materials.append(bpy.data.materials.new("Paint - Vermilion"))
push("push new", cube)
guid = cube[me.PROP_GUID]
print("  archicad:", body_state(guid))
cube.data.vertices[6].co.z += 1.5
cube.data.update()
push("push edit", cube)
print("  same morph:", cube[me.PROP_GUID] == guid, "| morphs in Archicad:", len(api.list_guids()))
push("push unchanged", cube)

print("\n3. spike questions")
tests = {
    "open single quad": ([(0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0)], [(0, 1, 2, 3)], (6, -10, 0)),
    "warped quad": ([(0, 0, 0), (2, 0, 0), (2, 2, 0.4), (0, 2, 0)], [(0, 1, 2, 3)], (10, -10, 0)),
}
# concave L-shaped prism with n-gon caps
L = [(0, 0), (4, 0), (4, 2), (2, 2), (2, 4), (0, 4)]
verts = [(x, y, 0) for x, y in L] + [(x, y, 3) for x, y in L]
faces = [tuple(reversed(range(6))), tuple(range(6, 12))]
faces += [(i, (i + 1) % 6, 6 + (i + 1) % 6, 6 + i) for i in range(6)]
tests["concave L prism (n-gon caps)"] = (verts, faces, (14, -10, 0))
for name, (v, f, loc) in tests.items():
    obj = mesh_object(name, v, f, loc)
    push(name, obj)
    if obj.get(me.PROP_GUID):
        print("    archicad:", body_state(obj[me.PROP_GUID]))

print("\n4. size")
for u, w in ((32, 16), (96, 48)):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=u, v_segments=w, radius=2.0)
    mesh = bpy.data.meshes.new(f"sphere{u}")
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(f"sphere{u}", mesh)
    scene.collection.objects.link(obj)
    obj.location = (20 + u / 8, -10, 2)
    bpy.context.view_layer.update()
    push(f"sphere {len(mesh.polygons)} faces", obj)
    if obj.get(me.PROP_GUID):
        print("    archicad:", body_state(obj[me.PROP_GUID]))

print("\n5. round trip: pull everything into a clean Blender scene")
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o)
run("pull all", bpy.ops.acmorph.pull)
print("  objects:", len(bpy.data.objects), "| morphs in Archicad:", len(api.list_guids()))
print(bpy.data.texts["bl-ac morphs Report"].as_string()[:1500])
sys.stdout.flush()
os._exit(0)
