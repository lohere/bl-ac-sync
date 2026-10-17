# AC–Blender Sync

> Working title. Archicad → Blender geometry sync that **keeps your Blender work**.

Update an Archicad model in Blender with one click, without exporting files by hand and
without losing the materials, collections and hand edits you made in Blender.

**Status: early prototype / research phase.** Nothing here is usable yet. See
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

- Archicad 29 (earlier versions untested) with Tapir ≥ 1.7.1 installed
- Blender ≥ 4.2 (developed on 5.2 LTS)
- Windows (macOS untested)

## Repository layout

| Path | Purpose |
|---|---|
| `src/acsync/` | Blender-independent core: Tapir client, IFC reader, diff/matching, material mapping |
| `blender_addon/ac_sync/` | The Blender extension: operators, panel, preferences |
| `tests/` | Unit tests for the core |
| `docs/` | Architecture, research notes |
| `tools/research/` | Throwaway scripts used for the research measurements |
| `reference/` | The original FBX-based scripts this project replaces (kept for reference only) |

## License

Not decided yet.
