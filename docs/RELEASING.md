# Releasing and updating

How a new version gets to users, and what users do to get it. Everything marked **tested** was run
in a throwaway Blender profile (`tests/blender/update_flow.py`); items marked **untested** have not
been tried over the internet yet.

## Build

1. Bump `version` in `blender_addon/ac_sync/blender_manifest.toml`.
2. Run the tests (`pytest`, `tests/blender/run.py`).
3. Build:

   ```bash
   python tools/build_extension.py        # dist/ac_sync-<version>.zip, for manual installation
   python tools/build_repository.py       # dist/repository/: the zip + index.json for updates
   ```

The build downloads the ifcopenshell wheel for Blender's Python and lists it in the manifest, so
Blender installs it together with the add-on. The zip is about 43 MB.

### Constraints

- **Blender 5.0 or newer, Windows x64.** The bundled wheel is for Python 3.13 (Blender 5.x). Blender
  4.2-4.5 use Python 3.11 and would need a second wheel plus testing; macOS and Linux would need their own
  wheels (extend `PLATFORMS` in `tools/build_extension.py`).
- **One version per repository folder.** With two versions that target the same Blender versions,
  `server-generate` warns about duplicates and Blender may keep offering the old one (tested).
  `build_repository.py` therefore moves older zips to `<repo-dir>_archive` (nothing is deleted).

## Hosting the repository

A repository is static files: `index.json` and the zip next to it (`archive_url` in the index is relative).

- **GitHub release assets (recommended, untested):** attach `index.json` and the zip to a release. Users
  add `https://github.com/<user>/<repo>/releases/latest/download/index.json`. This keeps the 43 MB zips
  out of the git history.
- **GitHub Pages (untested):** serve `dist/repository/` from a `gh-pages` branch. Simple, but every new
  zip stays in that branch's history forever.

Blender only fetches `index.json` over `https://` or `file://`.

## What users do

- **First time:** Preferences > Get Extensions > Repositories (the small arrow menu) > *Add Remote
  Repository* > paste the `index.json` URL. Install **AC Sync** from the list. (Blender may ask to allow
  online access.)
- **Updates:** Blender shows an **Update** button for the extension (it can also check on startup).
- **After an update, restart Blender.** Tested in a running session after the files had been updated:
  - *Refresh* alone: the running Blender keeps the **old** code.
  - *Disable and enable the add-on:* picks up the **new** code and works.
  A restart is the simple instruction. (A new ifcopenshell version always needs a restart, because compiled
  libraries cannot be reloaded.)

## Test an update locally

```bash
python tests/blender/update_flow.py
```

Builds 0.0.1, 0.0.2 and 0.0.3 into a temporary repository, installs 0.0.1 into a throwaway Blender
profile with Blender's own command line, updates to 0.0.2, checks that the updated add-on works (no
policy warnings, a sync runs), then updates the files under a running Blender and reports what a
refresh and a disable/enable do. Your own Blender settings are not touched.

## Before the first public release

Done:

- [x] License: GPL-3.0-or-later (`LICENSE`, shipped inside the zip, SPDX line in every source file,
  `license` / `copyright` in the manifest).
- [x] `CHANGELOG.md`, version bumped in the manifest, `pyproject.toml` and `acsync/__init__.py`.
- [x] Tested against a real Archicad project, including a stair and a railing.

Open, in this order:

1. **Final name.** Decide it first: the manifest `id` is the extension's identity, and changing it
   after people installed it breaks their updates. A rename touches the `ac_sync` / `acsync` package
   names, operator ids, the scene property, the manifest `id` and `name`, the docs and the GitHub
   repository. Keep "Archicad" out of the name (Graphisoft trademark).
2. **Hosting.** Publish the repository (see above) and test an update over the internet; only
   `file://` has been tried.
3. **Platforms.** Windows x64 and Blender 5.0+ only. macOS needs its own ifcopenshell wheel and
   someone to test it.
4. **Version number.** Choose the number of the first public release (for example 0.1.0), and move
   the changelog entries under it.
5. **Live-test the optional material switch once more** on the release build, and check Tapir's
   current release for changes to the commands the add-on uses.
