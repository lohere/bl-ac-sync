"""Manual check: sync from a running Archicad inside headless Blender.

    blender --background --factory-startup --python tests/blender/live_archicad_check.py

Needs ACSYNC_EXTRA_PATHS (see run.py) and a running Archicad. Refuses to run unless the open
project's name contains the text in ACSYNC_LIVE_PROJECT (default "sandbox"), because it makes
Archicad write an IFC file.
"""

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "blender_addon"))

import ac_sync
import bpy
from ac_sync.acsync import find_instances

ac_sync.register()
want = os.environ.get("ACSYNC_LIVE_PROJECT", "sandbox").lower()
found = find_instances()
if not found or want not in found[0].project_name.lower():
    print("REFUSING: open project is", found[0].project_name if found else "none")
    os._exit(2)

s = bpy.context.scene.acsync
s.source = "ARCHICAD"
print("project:", found[0].project_name, "| tapir", found[0].tapir_version)
print("refresh:", bpy.ops.acsync.refresh("EXEC_DEFAULT"), "->", ac_sync.operators.connection.message)
print("last_port saved:", s.last_port)
print("translators:", len(s.translators), "| default present:", s.translator in {t.name for t in s.translators})
print("dry run:", bpy.ops.acsync.sync("EXEC_DEFAULT", dry_run=True, blocking=True), "|", s.last_summary)
print("sync:", bpy.ops.acsync.sync("EXEC_DEFAULT", blocking=True), "|", s.last_summary)
print("completeness:", repr(s.last_check), "| ok:", s.last_check_ok)
print(bpy.data.texts["AC Sync Report"].as_string())
print("select missing:", bpy.ops.acsync.select_missing("EXEC_DEFAULT"))
print("selected in Archicad:", len(ac_sync.acsync.TapirClient(s.last_port).get_selection()))
print("objects:", len(ac_sync.engine.managed_objects(s.root_collection)))
print("again:", bpy.ops.acsync.sync("EXEC_DEFAULT", blocking=True), "|", s.last_summary)
tmp = os.path.join(os.environ.get("TEMP", "."), "acsync")
print("temp files left:", os.listdir(tmp) if os.path.isdir(tmp) else [])
sys.stdout.flush()
os._exit(0)
