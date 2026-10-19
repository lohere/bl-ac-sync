# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the installable Blender extension (a .zip).

    python tools/build_extension.py [--blender PATH] [--python-version 3.13]
                                    [--version X.Y.Z] [--output-dir DIR]

Steps: stage ``blender_addon/ac_sync`` (which contains the ``acsync`` core as a sub-package)
in ``build/``, download the ifcopenshell wheel for the target Blender Python into ``wheels/``
(listed in the manifest, so Blender installs it with the extension), then let Blender
validate and pack it (``blender --command extension build``).

``--version`` overrides the version in the *staged* manifest only (handy to test updates);
the source manifest is never touched.

Windows x64 only for now (the manifest lists the platform; extend ``PLATFORMS`` for more).
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ADDON = os.path.join(ROOT, "blender_addon", "ac_sync")
BUILD = os.path.join(ROOT, "build", "ac_sync")
DIST = os.path.join(ROOT, "dist")
PLATFORMS = {"windows-x64": "win_amd64"}
_VERSION_LINE = re.compile(r'^version\s*=\s*"[^"]*"\s*$', re.MULTILINE)


LOCAL_BLENDER_FILE = os.path.join(ROOT, ".dev", "blender.txt")  # git-ignored, one line: a path


def _blender_candidates(explicit):
    yield explicit
    yield os.environ.get("ACSYNC_BLENDER")
    if os.path.isfile(LOCAL_BLENDER_FILE):
        with open(LOCAL_BLENDER_FILE, encoding="utf-8") as fh:
            yield fh.readline().strip()
    yield shutil.which("blender")
    # the usual Windows install locations, newest version first
    for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env_name)
        if not base:
            continue
        foundation = os.path.join(base, "Blender Foundation")
        if os.path.isdir(foundation):
            for name in sorted(os.listdir(foundation), reverse=True):
                yield os.path.join(foundation, name, "blender.exe")
        yield os.path.join(base, "Steam", "steamapps", "common", "Blender", "blender.exe")


def find_blender(explicit=None):
    """Path of blender.exe: --blender, $ACSYNC_BLENDER, .dev/blender.txt, PATH, usual installs."""
    for candidate in _blender_candidates(explicit):
        if candidate and os.path.isfile(candidate):
            return candidate
    sys.exit(
        "Blender not found. Pass --blender PATH, set the ACSYNC_BLENDER environment variable, "
        "or write the path of blender.exe into .dev/blender.txt"
    )


def stage(version=None):
    if os.path.isdir(BUILD):
        shutil.rmtree(BUILD)
    shutil.copytree(ADDON, BUILD, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "wheels"))
    if version:
        manifest = os.path.join(BUILD, "blender_manifest.toml")
        with open(manifest, encoding="utf-8") as fh:
            text = fh.read()
        text, count = _VERSION_LINE.subn(f'version = "{version}"', text, count=1)
        if count != 1:
            sys.exit("Could not find the version line in blender_manifest.toml")
        with open(manifest, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    write_build_info()


def write_build_info():
    """Stamp the manifest version into the package so the running add-on can show it."""
    manifest = os.path.join(BUILD, "blender_manifest.toml")
    with open(manifest, encoding="utf-8") as fh:
        found = _VERSION_LINE.search(fh.read())
    version = re.search(r'"([^"]*)"', found.group(0)).group(1)
    with open(os.path.join(BUILD, "_build_info.py"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f'"""Written by tools/build_extension.py."""\n\nVERSION = "{version}"\n')


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


def build(blender=None, python_version="3.13", version=None, output_dir=DIST):
    """Build the extension zip and return its path."""
    blender = find_blender(blender)
    stage(version)
    wheels = fetch_wheels(python_version)
    print("wheels:", ", ".join(wheels))
    os.makedirs(output_dir, exist_ok=True)
    before = set(os.listdir(output_dir))
    subprocess.run(
        [blender, "--command", "extension", "build",
         "--source-dir", BUILD, "--output-dir", output_dir],
        check=True,
    )
    created = sorted(set(os.listdir(output_dir)) - before) or sorted(
        f for f in os.listdir(output_dir) if f.endswith(".zip"))
    path = os.path.join(output_dir, created[-1])
    print("built:", path)
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender")
    parser.add_argument("--python-version", default="3.13",
                        help="Python version of the target Blender (5.x ships 3.13)")
    parser.add_argument("--version", help="override the version in the staged manifest")
    parser.add_argument("--output-dir", default=DIST)
    args = parser.parse_args()
    build(args.blender, args.python_version, args.version, args.output_dir)


if __name__ == "__main__":
    main()
