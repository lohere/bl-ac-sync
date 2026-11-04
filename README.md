# bl-ac sync

**Update your Archicad model in Blender with one click.**

---

# Usage

After you open the sidebar (N) in Blender and find the bl-ac tool, press **Sync**. The tool finds an open Archicad instance for the current 3D model, then updates the geometry in Blender that has changed. Your materials, modifiers, names and collections stay as they are.

- New elements appear, changed elements are updated, deleted elements are moved aside (deleted or hidden, configurable)
- A **Dry run** button shows what would change before anything is touched
- A report tells you if something in Archicad did not make it across, and why

The tool is free and open source.

---

## What you need

|  |  |
| --- | --- |
| **Archicad 29** | Other versions are untested. The project must be open while you sync. |
| **Tapir** (free Archicad add-on) | Lets other programs talk to Archicad. Version **1.7.1 or newer**. See below. |
| **Blender 5.0 or newer** | Developed and tested on Blender 5.2 LTS. Older versions are not tested. |
| **Windows** | macOS has not been tested yet. |

---

## Install

### 1. Tapir

1. Download the Tapir installer from its official [releases page](https://github.com/ENZYME-APD/tapir-archicad-automation/releases) (version 1.7.1 or newer).
2. Follow the Tapir installation steps. Windows may warn that the installer is not signed. That is normal for this tool.
3. Restart Archicad.

### 2. bl-ac sync

1. Download `ac_sync-<version>.zip` (do **not** unzip it).
2. In Blender: **Edit > Preferences > Get Extensions**, open the small menu (top right) and choose **Install from Disk…**, then pick the zip.
3. Restart Blender if it asks you to.

You will now find a **bl-ac** tab in the 3D viewport sidebar (press **N**).

---

## Your first sync

1. Have your project opened in Archicad. **Close any open dialog** (an open dialog blocks Archicad from answering).
2. In Blender, open the sidebar (**N**) and the **bl-ac** tab.
3. Click the small **refresh icon** next to "Archicad". You should see your project name.
4. Click **Dry run** to preview, or **Sync** to import. Tick **Select changed objects** to have the added and changed objects selected afterwards (or press **Select changed** under Last result).
5. Look at **Last result** in the panel. If the tool says elements did not arrive, press **Copy report** to see which ones and why.

Everything the tool creates goes into one collection called **bl-ac sync** (with sub-collections for walls, slabs, windows and so on). The first sync takes a few seconds for a mid-sized model.

After a design change in Archicad, just press **Sync** again.

---

## Things to look out for

- New materials are added at sync if they do not exist yet, they can be overwritten with your own materials. Changing the imported material properties works too (if the name isn't changed).
- Applied modifiers, custom names, parenting are kept.
- Nothing is touched outside the **bl-ac sync** collection. This means that if you change an object's geometry in Blender, then change the same object in Archicad, it will be overwritten. It's recommended to take the modified object out of the collection.
- UV maps are regenerated after each sync.
- Deleted elements are moved to a `bl-ac sync - Removed` collection (you can change this to hide, keep, or delete). Not checked when only part of the model is exported (selection or one story).
- Each object remembers which Archicad element it came from, so renaming or moving it in Blender is fine.

---

## Materials and textures

Archicad's surfaces become Blender materials with the same name. In **Materials** of the **bl-ac sync** panel you can point each Archicad surface to **your own** Blender material. Then press **Apply material mapping**. Materials you assigned by hand are not changed.

Image textures: Archicad does not pass texture coordinates (UVs) on, so the tool creates its own, in real-world scale (by default one texture repeat per metre). Change **Metres per UV unit** in **Output** to make textures bigger or smaller. In your material, connect a **Texture Coordinate > UV** node to the image texture.

---

## Good to know

- **Archicad leaves some things out of an export.** The most common cases are explained in [`docs/ARCHICAD_SETUP.md`](docs/ARCHICAD_SETUP.md): hidden layers, materials without "Participates in Collision Detection", and elements that have no geometry. The report tells you what is missing after every sync. **Select in Archicad** (a button next to the result) selects the missing elements in Archicad so you can look at them.
- **Ground or context missing?** Archicad's default IFC export skips everything whose Building Material does not have *Participates in Collision Detection* ticked, and some materials (ground, air gaps) come with it off. The tool detects this and shows a red box naming the materials. Tick the option for the material in Archicad, or turn on **Include excluded materials** (Advanced): the tool then switches it on for just those materials during the export and restores it right after. Air gaps are only touched if an element is missing because of them.
- **Terrain arrives as one object** called "Site", even if you drew several Mesh elements.
- **The model is placed exactly where it is in Archicad.** (Archicad rotates its exported file by your Project North angle. The tool undoes that. You can turn this off in **Output**.)
- **Zones and opening voids** are not imported by default (you can change this in **Output**).
- **It is a button, not a live link.** Press Sync when you want an update.

---

## Disclaimer

I'm an architecture student, this tool was made primarily for myself, but sharing it as it may be useful to others. I'm not a coder, so the project was mostly written by Claude Code. Feedback and bug reports are appreciated.

---

## Something not working?

| What you see | Try this |
| --- | --- |
| "Archicad is running, but the Tapir add-on is not installed or not loaded" | Install Tapir 1.7.1 or newer (link above) and **restart Archicad**. |
| "A dialog is open in Archicad" | Close it and try again. |
| "Tapir ... is installed, but 1.7.1 or newer is required" | Install the newest Tapir and restart Archicad. |
| Elements missing | Read the report, then see [`docs/ARCHICAD_SETUP.md`](docs/ARCHICAD_SETUP.md). |
| Textures look stretched | The material needs a **Texture Coordinate > UV** node, and **Generate UVs** must be on. |
| Model sits in the wrong place | Check **Archicad coordinates** and **Offset** in **Output**. |

---

## For developers

Tapir makes Archicad export an IFC file, ifcopenshell reads it, and the add-on updates the Blender scene by matching Archicad's element GUIDs.

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
| --- | --- |
| `blender_addon/ac_sync/` | The Blender extension: operators, panel, settings |
| `blender_addon/ac_sync/acsync/` | Its Blender-independent core (no `bpy`), exposed to pip/pytest as `acsync` |
| `tests/` | Unit tests and headless Blender tests |
| `tools/` | Build and release scripts |

Optional environment variables for the tests: `ACSYNC_TEST_IFC` (a real Archicad IFC4 Reference View export), `ACSYNC_LIVE_ARCHICAD=1` (tests against a running Archicad), `ACSYNC_BLENDER` (path to `blender.exe`).

---

## License

**GNU General Public License v3.0 or later** (GPL-3.0-or-later), see [`LICENSE`](LICENSE). Copyright (C) 2026 Drotár Bence.

Third-party software: [Tapir](https://github.com/ENZYME-APD/tapir-archicad-automation) (MIT, a separate program, not included) and [IfcOpenShell](https://ifcopenshell.org/) (LGPL-3.0, included as a library).
