"""Run the Blender add-on tests: ``python tests/blender/run.py`` (from the dev venv).

1. builds the fixture IFC files with the dev environment's ifcopenshell,
2. makes sure an ifcopenshell build for Blender's Python exists in ``.dev/site`` (downloaded
   once with pip; Blender's own Python version is asked from Blender itself),
3. runs Blender headless on ``run_addon_tests.py``.

Environment: ACSYNC_BLENDER or .dev/blender.txt (path to blender.exe; see tools/build_extension.py),
ACSYNC_TEST_IFC (optional real Archicad export for the real-model test).
"""

import os
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import build_extension
from ifc_fixture import build_synthetic_ifc

SITE = os.path.join(ROOT, ".dev", "site")
FIXTURES = os.path.join(ROOT, ".dev", "fixtures")
OTHER_GUID = "EEEEEEEE-0000-4000-8000-000000000005"


def blender_exe():
    return build_extension.find_blender()


def blender_python_tag(exe):
    out = subprocess.run(
        [exe, "--background", "--factory-startup", "--python-expr",
         "import sys; print('PYTAG', sys.version_info.major, sys.version_info.minor)"],
        capture_output=True, text=True, check=True,
    ).stdout
    major, minor = next(line for line in out.splitlines() if line.startswith("PYTAG")).split()[1:]
    return f"{major}.{minor}"


def ensure_site(exe):
    if os.path.isdir(os.path.join(SITE, "ifcopenshell")):
        return
    version = blender_python_tag(exe)
    print(f"Fetching ifcopenshell for Blender's Python {version} ...")
    wheel_dir = os.path.join(ROOT, ".dev", "wheels")
    os.makedirs(wheel_dir, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "pip", "download", "ifcopenshell", "--only-binary=:all:",
         "--python-version", version, "--platform", "win_amd64", "--no-deps", "-d", wheel_dir],
        check=True,
    )
    wheel = next(f for f in os.listdir(wheel_dir) if f.startswith("ifcopenshell"))
    with zipfile.ZipFile(os.path.join(wheel_dir, wheel)) as z:
        z.extractall(SITE)


def build_fixtures():
    os.makedirs(FIXTURES, exist_ok=True)
    build_synthetic_ifc(os.path.join(FIXTURES, "a.ifc"))
    build_synthetic_ifc(os.path.join(FIXTURES, "b.ifc"), wall_height_mm=3500.0)
    build_synthetic_ifc(os.path.join(FIXTURES, "nowin.ifc"), include_window=False)
    build_synthetic_ifc(os.path.join(FIXTURES, "dup.ifc"), second_wall_guid=OTHER_GUID)
    build_synthetic_ifc(os.path.join(FIXTURES, "terrain.ifc"), terrain=True)
    build_synthetic_ifc(os.path.join(FIXTURES, "rot.ifc"), site_rotation_deg=23.0)


def main():
    exe = blender_exe()
    ensure_site(exe)
    build_fixtures()
    env = dict(os.environ, ACSYNC_EXTRA_PATHS=SITE, ACSYNC_FIXTURE_DIR=FIXTURES,
               PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [exe, "--background", "--factory-startup", "--python",
         os.path.join(HERE, "run_addon_tests.py")],
        env=env,
    )
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
