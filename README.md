# AC Sync

**Update your Archicad model in Blender with one click, and keep everything you did in Blender.**

> Working title. Status: working prototype, not released yet.

---

## What is this?

If you visualise your Archicad models in Blender, you know the routine: export the model, import
it, re-assign every material, rebuild the lighting and cameras... and do it all again after the next
design change.

**AC Sync removes the repeat.** You press **Sync** in Blender. The tool asks Archicad for the
current model, then updates the geometry that changed *inside the objects you already have*. Your
materials, modifiers, names, cameras, lights and collections stay as they are.

- No manual export or import
- Walls, slabs, roofs, windows, doors, columns, beams, curtain walls and terrain
- New elements appear, changed elements are updated, deleted elements are moved aside
- A **Dry run** button shows what would change before anything is touched
- A report tells you if something in Archicad did not make it across, and why

It is free and open source.

---

## What you need

| | |
|---|---|
| **Archicad 29** | Older versions are untested. The project must be open while you sync. |
| **Tapir** (free Archicad add-on) | Lets other programs talk to Archicad. Version **1.7.1 or newer**. See below. |
| **Blender 5.0 or newer** | Developed and tested on Blender 5.2 LTS. Older versions are not supported. |
| **Windows** | macOS has not been tested yet. |

Everything else (the IFC reader) is included in the add-on. You do not install anything else.

---

## Install

### 1. Tapir (once, in Archicad)

1. Download the Tapir installer from its
   [releases page](https://github.com/ENZYME-APD/tapir-archicad-automation/releases) (version 1.7.1 or
   newer). Tapir is free and open source (MIT license).
2. Run it. Windows may warn that the installer is not signed. That is normal for this tool.
3. Restart Archicad.

### 2. AC Sync (once, in Blender)

1. Download `ac_sync-<version>.zip` (do **not** unzip it).
2. In Blender: **Edit > Preferences > Get Extensions**, open the small menu (top right) and choose
   **Install from Disk…**, then pick the zip.
3. Restart Blender if it asks you to.

You will now find an **AC Sync** tab in the 3D viewport sidebar (press **N**).

---

## Updating

When a new version is published, Blender shows an **Update** button next to AC Sync in
**Preferences > Get Extensions** (once you have added the project's extension repository there; see
[`docs/RELEASING.md`](docs/RELEASING.md)). After updating, **restart Blender**.

---

## Your first sync

1. Open your project in Archicad. **Close any open dialog** (an open dialog blocks Archicad from
   answering).
2. In Blender, open the sidebar (**N**) and the **AC Sync** tab.
3. Click the small **refresh icon** next to "Archicad". You should see your project name.
4. Click **Dry run** to preview, or **Sync** to import.
5. Look at **Last result** in the panel. If the tool says elements did not arrive, press
   **Copy report** to see which ones and why.

Everything the tool creates goes into one collection called **AC_Sync** (with sub-collections for
walls, slabs, windows and so on). The first sync takes a few seconds for a mid-sized model.

After a design change in Archicad, just press **Sync** again.

---

## What happens to my Blender work?

| Your work | After a Sync |
|---|---|
| Materials you assigned | **Kept.** The tool never overwrites a material you chose. |
| Modifiers (Bevel, ...) | **Kept.** |
| Names, parenting, custom properties | **Kept.** |
| Cameras, lights, anything outside the AC_Sync collection | **Never touched.** |
| Geometry of an element that changed in Archicad | **Replaced** with the new shape. |
| UV maps on a changed element | **Replaced** (the tool makes its own). |
| Edits you made in Edit Mode on a synced object | **Lost** when that element changes in Archicad. Do detail work with modifiers or on a copy. |
| Elements deleted in Archicad | Moved to an `AC_Sync - Removed` collection (you can change this to hide, keep, or delete). |

Each object remembers which Archicad element it came from, so renaming or moving it in Blender is
fine.

---

## Materials and textures

Archicad's surfaces become Blender materials with the same name and colour (glass is transparent).
In **Materials** in the panel you can point each Archicad surface to **your own** Blender material.
Then press **Apply material mapping**. Materials you assigned by hand are not changed.

Image textures: Archicad does not pass texture coordinates (UVs) on, so the tool creates its own, in
real-world scale (by default one texture repeat per metre). Change **Metres per UV unit** in
**Output** to make textures bigger or smaller. In your material, connect a **Texture Coordinate >
UV** node to the image texture.

---

## Good to know

- **Archicad leaves some things out of an export.** The most common cases are explained in
  [`docs/ARCHICAD_SETUP.md`](docs/ARCHICAD_SETUP.md): hidden layers, materials without
  "Participates in Collision Detection", and elements that have no geometry. The report tells you
  what is missing after every sync. **Select in Archicad** (a button next to the result) selects the
  missing elements in Archicad so you can look at them.
- **Terrain arrives as one object** called "Site", even if you drew several Mesh elements.
- **The model is placed exactly where it is in Archicad.** (Archicad rotates its exported file by
  your Project North angle. The tool undoes that. You can turn this off in **Output**.)
- **Zones and opening voids** are not imported by default (you can change this in **Output**).
- **It is a button, not a live link.** Press Sync when you want an update.

---

## Limitations (so far)

- Tested on Windows with Archicad 29 and Blender 5.2 only.
- **Stairs and railings are untested.**
- macOS is untested.
- Image texture orientation from Archicad's own surface settings is not carried over (see above).
- No automatic updates when you save in Archicad.

This is a student project in active development. Feedback and bug reports are very welcome.

---

## Something not working?

| What you see | Try this |
|---|---|
| "No Archicad found" | Start Archicad and open your project. |
| "Archicad is running, but the Tapir add-on is not installed or not loaded" | Install Tapir 1.7.1 or newer (link above) and **restart Archicad**. |
| "A dialog is open in Archicad" | Close it (Cancel/OK) and try again. |
| "Tapir ... is installed, but 1.7.1 or newer is required" | Install the newest Tapir and restart Archicad. |
| Elements missing | Read the report, then see [`docs/ARCHICAD_SETUP.md`](docs/ARCHICAD_SETUP.md). |
| Textures look stretched | The material needs a **Texture Coordinate > UV** node, and **Generate UVs** must be on. |
| Model sits in the wrong place | Check **Archicad coordinates** and **Offset** in **Output**. |

---

## For developers

How it works, in one line: Tapir makes Archicad export an IFC file, ifcopenshell reads it, and the
add-on updates the Blender scene by matching Archicad's element GUIDs.

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): design and decisions
- [`docs/RESEARCH.md`](docs/RESEARCH.md): measurements behind the decisions
- [`docs/ARCHICAD_SETUP.md`](docs/ARCHICAD_SETUP.md): Archicad-side behaviour

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"
.venv/Scripts/python -m pytest                      # core tests (no Blender needed)
.venv/Scripts/python tests/blender/run.py           # add-on tests inside headless Blender
.venv/Scripts/python tools/build_extension.py       # builds dist/ac_sync-<version>.zip
```

| Path | Purpose |
|---|---|
| `blender_addon/ac_sync/` | The Blender extension: operators, panel, settings |
| `blender_addon/ac_sync/acsync/` | Its Blender-independent core (no `bpy`), exposed to pip/pytest as `acsync` |
| `tests/` | Unit tests and headless Blender tests |
| `tools/` | Build and release scripts |

Optional environment variables for the tests: `ACSYNC_TEST_IFC` (a real Archicad IFC4 Reference View
export), `ACSYNC_LIVE_ARCHICAD=1` (tests against a running Archicad), `ACSYNC_BLENDER` (path to
`blender.exe`).

---

## License

Planned: **GNU General Public License v3.0 or later** (GPL-3.0-or-later). The license file is added
when the project is published.

Third-party software: [Tapir](https://github.com/ENZYME-APD/tapir-archicad-automation) (MIT, a separate
program, not included) and [IfcOpenShell](https://ifcopenshell.org/) (LGPL-3.0, included as a library).
