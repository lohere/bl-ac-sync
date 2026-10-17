"""Build the installable Blender extension (a .zip) into ``dist/``.

    python tools/build_extension.py [--blender PATH] [--python-version 3.13]

Steps: stage ``blender_addon/ac_sync`` in ``build/``, vendor the ``acsync`` core into
``_vendor/``, download the ifcopenshell wheel for the target Blender Python into ``wheels/``
(listed in the manifest, so Blender installs it with the extension), then let Blender
validate and pack it (``blender --command extension build``).

Windows x64 only for now (the manifest lists the platform; extend ``PLATFORMS`` for more).
"""

import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ADDON = os.path.join(ROOT, "blender_addon", "ac_sync")
CORE = os.path.join(ROOT, "src", "acsync")
BUILD = os.path.join(ROOT, "build", "ac_sync")
DIST = os.path.join(ROOT, "dist")
DEFAULT_BLENDER = r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
PLATFORMS = {"windows-x64": "win_amd64"}


def stage():
    if os.path.isdir(BUILD):
        shutil.rmtree(BUILD)
    shutil.copytree(ADDON, BUILD, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "wheels"))
    shutil.copytree(CORE, os.path.join(BUILD, "_vendor", "acsync"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def fetch_wheels(python_version):
    wheels_dir = os.path.join(BUILD, "wheels")
    os.makedirs(wheels_dir, exist_ok=True)
    for platform_tag in PLATFORMS.values():
        subprocess.run(
            [sys.executable, "-m", "pip", "download", "ifcopenshell", "--only-binary=:all:",
             "--python-version", python_version, "--platform", platform_tag, "--no-deps",
             "-d", wheels_dir],
            check=True,
        )
    names = sorted(f for f in os.listdir(wheels_dir) if f.endswith(".whl"))
    manifest = os.path.join(BUILD, "blender_manifest.toml")
    with open(manifest, encoding="utf-8") as fh:
        text = fh.read()
    wheels = "wheels = [\n" + "".join(f'  "./wheels/{n}",\n' for n in names) + "]\n\n"
    # Top-level keys must come before the first [table], or TOML files them under it.
    head, sep, tail = text.partition("[permissions]")
    with open(manifest, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(head + wheels + sep + tail)
    return names


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender", default=os.environ.get("ACSYNC_BLENDER") or (
        DEFAULT_BLENDER if os.path.isfile(DEFAULT_BLENDER) else shutil.which("blender")))
    parser.add_argument("--python-version", default="3.13",
                        help="Python version of the target Blender (5.x ships 3.13)")
    args = parser.parse_args()
    if not args.blender:
        sys.exit("Blender not found: pass --blender PATH or set ACSYNC_BLENDER.")

    stage()
    wheels = fetch_wheels(args.python_version)
    print("wheels:", ", ".join(wheels))
    os.makedirs(DIST, exist_ok=True)
    subprocess.run(
        [args.blender, "--command", "extension", "build",
         "--source-dir", BUILD, "--output-dir", DIST],
        check=True,
    )
    print("built:", ", ".join(os.listdir(DIST)))


if __name__ == "__main__":
    main()
