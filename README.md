# bl-ac sync

**Update your Archicad model in Blender with one click.**

A free, open-source Blender add-on that brings your Archicad model into Blender and keeps it up to date. Built for architects and students who render, present or animate their Archicad projects in Blender. Works with Archicad 29 through the free Tapir add-on. New in 0.2.0: an experimental panel that sends shapes modelled in Blender back to Archicad as Morphs ([see below](#morphs-send-shapes-back-to-archicad-experimental)).

---

# Usage

After you open the sidebar (N) in Blender and find the bl-ac tool, press **Sync**. The tool finds an open Archicad instance for the current 3D model, then updates the geometry in Blender that has changed. Your materials, modifiers, names and collections stay as they are.

- New elements appear, changed elements are updated, deleted elements are moved aside (deleted or hidden, configurable)
- A **Dry run** button shows what would change before anything is touched
- A report tells you if something in Archicad did not make it across, and why

The tool is free and open source.

### Why not just export an IFC or FBX file and import it?

You can, but every design change means exporting and importing again, and the new import replaces everything you set up in Blender: materials, modifiers, names, collections. bl-ac sync matches each object to its Archicad element, so a re-sync changes only the geometry that changed and keeps your Blender work.

---

## What you need

| You need | Notes |
| --- | --- |
| **Archicad 29** | Other versions are untested. The project must be open while you sync. |
| **Tapir** (free Archicad add-on) | Lets other programs talk to Archicad. Version **1.7.1 or newer**, **1.8.0 or newer** for the morph panel. See Install. |
| **Blender 5.0 or newer** | Developed and tested on Blender 5.2 LTS. Older versions are not tested. |
| **Windows** | macOS has not been tested yet. |

---

## Install

### 1. Tapir

1. Download the Tapir installer from its official [releases page](https://github.com/ENZYME-APD/tapir-archicad-automation/releases) (version 1.7.1 or newer).
2. Follow the Tapir installation steps. Windows may warn that the installer is not signed. That is normal for this tool.
3. Restart Archicad.

### 2. bl-ac sync

**Recommended: install from the extension repository, so Blender can offer updates.**

1. In Blender: **Edit > Preferences > Get Extensions**.
2. Open the small arrow menu next to *Repositories* (top right) and choose **Add Remote Repository**.
3. Paste this address and confirm (Blender may ask you to allow online access):

   ```
   https://github.com/lohere/bl-ac-sync/releases/latest/download/index.json
   ```

4. Find **bl-ac sync** in the list and click **Install**.

**Or install from a file:** download `ac_sync-<version>.zip` from the [latest release](https://github.com/lohere/bl-ac-sync/releases/latest) (do **not** unzip it). In **Preferences > Get Extensions** open the small menu (top right), choose **Install from Disk…** and pick the zip. Updates then have to be installed by hand the same way.

You will now find a **bl-ac** tab in the 3D viewport sidebar (press **N**).

### Updating

If you installed from the repository: open **Edit > Preferences > Get Extensions**. When a new version is available, an **Update** button appears next to bl-ac sync (Blender can also check on startup). Click it and the new version is loaded, no restart needed. The version number is shown at the bottom of the **Advanced** panel; if it still shows the old one, restart Blender.

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

## Morphs: send shapes back to Archicad (experimental)

New in 0.2.0. A second panel, **bl-ac morphs**, works the other way round. Model a shape in Blender, send it to Archicad as a **Morph** (the element that is painful to model in Archicad), keep working in either program, and bring Archicad's morphs back into Blender.

1. Click the refresh icon at the top of the panel. It needs **Tapir 1.8.0 or newer**.
2. Put your meshes in a Blender collection and choose it in **Collection**.
3. Press **Sync collection**. New and changed meshes are sent to Archicad, and changes you made to those morphs in Archicad come back. Do it again after every change.

Or use the buttons for one direction: **Push** sends the selected objects (or the whole collection), **Pull** brings in the morphs selected in Archicad (or all of them). The **Dry run** checkbox makes every button only show what it would do.

- An object keeps its morph: sending it again changes the same morph, so its layer, ID and properties in Archicad stay.
- If the same morph changed in both programs since the last sync, it is skipped and listed. Nothing is overwritten (unless you choose *Overwrite*), and nothing is ever deleted.
- Materials are matched by name: name a Blender material like an Archicad surface (for example `Brick - Red`) and its faces get that surface. Pulled morphs bring their surfaces as Blender materials.
- A copy of a sent object (Shift+D) must be detached first (**Options > Detach from Archicad**), otherwise it is skipped.

**Good to know:**

- Archicad faces must be flat, so **warped faces become triangles** when you push. Flat quads and n-gons stay as they are, but a curved or sculpted mesh arrives as a triangle mesh, and there is no rounding in Archicad's morphs.
- Very dense meshes work but are slow (about 8 seconds for 50,000 faces).
- It is experimental. Try it on a copy of your project first.
- The sync from Archicad (above) also brings morphs in, as generic objects, so using both panels on the same project duplicates them in Blender.

Details, limits and measurements: [`docs/MORPHS.md`](docs/MORPHS.md).

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
| "Morph sync needs Tapir 1.8.0 or newer" | Install the newest Tapir and restart Archicad. |
| "Archicad refused the morph" | Most likely an edge that is shared by more than two faces. The report names the object; **Select > Select All by Trait > Non Manifold** in Blender shows the edges. |

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
