"""End-to-end test of updating the add-on through an extension repository.

    python tests/blender/update_flow.py          (from the dev venv; takes a minute or two)

Everything happens in a throwaway Blender profile and a temporary folder; your own Blender
settings and extensions are not touched. Steps:

1. build version 0.0.1 into a local repository folder, register it as a repository,
   install and enable the add-on from it;
2. build 0.0.2 into the same repository and run Blender's own update command;
3. check that the installed add-on is now 0.0.2 and still works (policy check, a sync);
4. in-session reload: start a Blender process with the add-on loaded, update the files from a
   second process, then (a) refresh the extensions and (b) disable + enable the add-on in the
   running session, and report which of them picks up the new code.
"""

import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import build_extension
import build_repository
from ifc_fixture import build_synthetic_ifc

REPO_ID = "ac_test"
MODULE = f"bl_ext.{REPO_ID}.ac_sync"


def blender_cli(blender, env, *args):
    result = subprocess.run(
        [blender, "--online-mode", "--command", "extension", *args],
        env=env, capture_output=True, text=True
    )
    print("   $ blender --command extension", " ".join(args), "->", result.returncode)
    if result.returncode != 0:
        print(result.stdout[-1500:], result.stderr[-1500:])
        raise SystemExit("Blender command failed")
    return result.stdout


def installed_version(profile):
    manifest = pathlib.Path(profile) / "extensions" / REPO_ID / "ac_sync" / "blender_manifest.toml"
    return re.search(r'^version\s*=\s*"([^"]+)"', manifest.read_text("utf-8"), re.MULTILINE).group(1)


def run_script(blender, env, code, extra_env=None):
    script = pathlib.Path(env["BLENDER_USER_RESOURCES"]).parent / "probe.py"
    script.write_text(code, encoding="utf-8")
    result = subprocess.run(
        [blender, "--background", "--python", str(script)],  # the profile holds the test repository
        env=dict(env, **(extra_env or {})), capture_output=True, text=True,
    )
    return result.stdout + result.stderr


SYNC_CHECK = f"""
import os, sys, importlib, bpy, addon_utils
m = importlib.import_module("{MODULE}")  # already enabled by Blender from the profile
addon_utils._is_first_reset()
print("RESULT version:", m.__version__)
print("RESULT policy warnings:", addon_utils._extensions_warnings_get().get("{MODULE}", []) or "none")
s = bpy.context.scene.acsync
s.source = "FILE"; s.ifc_path = os.environ["TEST_IFC"]
print("RESULT sync:", bpy.ops.acsync.sync("EXEC_DEFAULT", blocking=True), s.last_summary)
sys.stdout.flush(); os._exit(0)
"""

IN_SESSION = rf"""
import os, sys, subprocess, importlib, bpy
m = importlib.import_module("{MODULE}")
before = m.__version__
print("RESULT in-session start version:", before)
sys.stdout.flush()
subprocess.run([os.environ["BL_EXE"], "--online-mode", "--command", "extension", "update", "-s"], check=True,
               capture_output=True)  # a second process updates the files on disk
import re
print("RESULT manifest on disk after update:", re.search(r'^version\s*=\s*"([^"]+)"', open(os.environ["MANIFEST"]).read(), re.M).group(1))
bpy.ops.extensions.repo_refresh_all()   # what the Refresh Local button does
mod = sys.modules.get("{MODULE}")
print("RESULT in-session version after refresh:", getattr(mod, "__version__", "?"))
print("RESULT still registered:", hasattr(bpy.types.Scene, "acsync"))
import addon_utils
addon_utils.disable("{MODULE}", default_set=False)
addon_utils.enable("{MODULE}", default_set=False)
mod = sys.modules.get("{MODULE}")
print("RESULT in-session version after disable + enable:", getattr(mod, "__version__", "?"))
print("RESULT add-on works after toggling:", hasattr(bpy.types.Scene, "acsync"))
sys.stdout.flush(); os._exit(0)
"""


def results(output):
    return [line for line in output.splitlines() if line.startswith("RESULT")]


def main():
    blender = build_extension.find_blender()
    tmp = tempfile.mkdtemp(prefix="acsync_update_")
    repo, profile = os.path.join(tmp, "repo"), os.path.join(tmp, "profile")
    os.makedirs(profile)
    ifc = build_synthetic_ifc(os.path.join(tmp, "model.ifc"))
    env = dict(os.environ, BLENDER_USER_RESOURCES=profile, TEST_IFC=ifc, PYTHONIOENCODING="utf-8")
    try:
        print("1. build 0.0.1, add the repository, install")
        build_repository.build_repository(repo, blender, version="0.0.1")
        index_url = pathlib.Path(repo, "index.json").as_uri()
        blender_cli(blender, env, "repo-add", "--url", index_url, "--name", "AC test repo", REPO_ID)
        blender_cli(blender, env, "install", "-s", "-e", "ac_sync")
        assert installed_version(profile) == "0.0.1", installed_version(profile)
        print("   installed:", installed_version(profile))

        print("2. build 0.0.2 into the same repository, run Blender's update")
        build_repository.build_repository(repo, blender, version="0.0.2")
        out = blender_cli(blender, env, "update", "-s")
        print("  ", out.strip().replace("\n", "\n   ")[-300:])
        assert installed_version(profile) == "0.0.2", installed_version(profile)
        print("   installed:", installed_version(profile))

        print("3. the updated add-on works")
        lines = results(run_script(blender, env, SYNC_CHECK))
        print("\n".join("   " + line for line in lines))
        assert any("version: 0.0.2" in line for line in lines), lines
        assert any("policy warnings: none" in line for line in lines), lines
        assert any("FINISHED" in line for line in lines), lines

        print("4. in-session reload (0.0.2 -> 0.0.3 while Blender keeps running)")
        build_repository.build_repository(repo, blender, version="0.0.3")
        manifest = os.path.join(profile, "extensions", REPO_ID, "ac_sync", "blender_manifest.toml")
        out = run_script(blender, env, IN_SESSION, {"BL_EXE": blender, "MANIFEST": manifest})
        print("\n".join("   " + line for line in results(out)) or out[-1500:])
        print("\nOK: repository build, install and update work.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
