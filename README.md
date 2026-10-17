# AC–Blender Sync

> Working title. Archicad → Blender geometry sync that **keeps your Blender work**.

Update an Archicad model in Blender with one click, without exporting files by hand and
without losing the materials, collections and hand edits you made in Blender.

**Status: working prototype.** The core, the Blender add-on (N-panel: sync, dry run, material
mapping) and the tests exist; it is not released yet. See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the design and
[`docs/RESEARCH.md`](docs/RESEARCH.md) for the measurements behind it.

## Idea

```
Archicad ──(Tapir JSON API, localhost)──► IFC export (automatic, temp file)
                                              │
                                   ifcopenshell reads meshes
                                              │
                      match by Archicad element GUID (not by Element ID)
                                              │
        Blender: swap geometry into existing objects, keep materials/collections,
                 add new elements, report removed ones, optional dry run
```

- **No paid tools, no manual export.** Uses the free, open-source
  [Tapir](https://github.com/ENZYME-APD/tapir-archicad-automation) add-on for Archicad and
  [ifcopenshell](https://ifcopenshell.org/).
- **Stable matching.** Archicad's element GUID survives copy/paste; Element IDs do not.
- **Non-destructive.** Only objects inside a dedicated collection are touched; everything
  else in the .blend is left alone.
- **GUI inside Blender.** A side-panel with the settings (translator, scope, material
  mapping, dry run) and a report of what changed.

## Requirements (planned)

> **New to this?** Read [`docs/ARCHICAD_SETUP.md`](docs/ARCHICAD_SETUP.md) first: it explains the Archicad-side settings that decide what arrives in Blender.


- Archicad 29 (earlier versions untested) with Tapir ≥ 1.7.1 installed
- Blender ≥ 4.2 (developed on 5.2 LTS)
- Windows (macOS untested)

## Repository layout

| Path | Purpose |
|---|---|
| `blender_addon/ac_sync/` | The Blender extension: operators, panel, settings |
| `blender_addon/ac_sync/acsync/` | Its Blender-independent core (no `bpy`): Tapir client, IFC reader, diff/matching, material mapping, completeness check. Exposed to pip/pytest as the package `acsync` |
| `tests/` | Unit tests for the core |
| `docs/` | Architecture, research notes |
| `tools/research/` | Throwaway scripts used for the research measurements |
| `reference/` | The original FBX-based scripts this project replaces (kept for reference only) |

## Development

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"
.venv/Scripts/python -m pytest                      # core tests (no Blender needed)
.venv/Scripts/python tests/blender/run.py           # add-on tests inside headless Blender
.venv/Scripts/python tools/build_extension.py       # builds dist/ac_sync-<version>.zip
```

Optional environment variables: `ACSYNC_TEST_IFC` (a real Archicad IFC4 Reference View export,
enables the real-model tests), `ACSYNC_LIVE_ARCHICAD=1` (enables the live-Archicad test),
`ACSYNC_BLENDER` (path to `blender.exe`). Install the zip in Blender via
Preferences > Get Extensions > Install from Disk.

## License

Not decided yet.
