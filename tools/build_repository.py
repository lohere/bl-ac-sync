# SPDX-License-Identifier: GPL-3.0-or-later
"""Build a Blender *extension repository* (a folder of zips plus ``index.json``).

    python tools/build_repository.py [--repo-dir dist/repository] [--version X.Y.Z]

A repository is static files, so any web host works (GitHub Pages or a release download URL).
Users add the URL of ``index.json`` once under Preferences > Get Extensions > Repositories and
Blender then offers updates.

A repository folder may hold only the *newest* zip of the add-on: with two versions that target
the same Blender versions, ``server-generate`` warns about duplicates and Blender may keep
offering the old one. Older zips found in ``--repo-dir`` are therefore moved to a sibling
``<repo-dir>_archive`` folder (never deleted).

Test locally before hosting: ``tests/blender/update_flow.py``.
"""

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build_extension

DEFAULT_REPO_DIR = os.path.join(build_extension.DIST, "repository")


def generate_listing(repo_dir, blender=None):
    """(Re)write ``index.json`` for the zips in ``repo_dir``."""
    blender = build_extension.find_blender(blender)
    subprocess.run(
        [blender, "--command", "extension", "server-generate", "--repo-dir", repo_dir],
        check=True,
    )
    return os.path.join(repo_dir, "index.json")


def archive_old_zips(repo_dir):
    """Move every zip in ``repo_dir`` to ``<repo_dir>_archive`` (overwriting a same-named one)."""
    archive = repo_dir.rstrip(r"\/") + "_archive"
    moved = []
    if os.path.isdir(repo_dir):
        for name in sorted(os.listdir(repo_dir)):
            if name.endswith(".zip"):
                os.makedirs(archive, exist_ok=True)
                target = os.path.join(archive, name)
                if os.path.exists(target):
                    os.remove(target)
                os.replace(os.path.join(repo_dir, name), target)
                moved.append(name)
    if moved:
        print(f"moved older package(s) to {archive}: {', '.join(moved)}")
    return moved


def build_repository(repo_dir=DEFAULT_REPO_DIR, blender=None, python_version="3.13", version=None):
    archive_old_zips(repo_dir)
    zip_path = build_extension.build(blender, python_version, version, output_dir=repo_dir)
    index = generate_listing(repo_dir, blender)
    return zip_path, index


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", default=DEFAULT_REPO_DIR)
    parser.add_argument("--blender")
    parser.add_argument("--python-version", default="3.13")
    parser.add_argument("--version", help="override the version in the staged manifest")
    args = parser.parse_args()
    zip_path, index = build_repository(
        os.path.abspath(args.repo_dir), args.blender, args.python_version, args.version
    )
    print("\nRepository ready:", os.path.dirname(index))
    print("  zip:  ", os.path.basename(zip_path))
    print("  list: ", index)
    print("Host that folder and give users the URL of index.json.")


if __name__ == "__main__":
    main()
