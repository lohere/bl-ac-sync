# Architecture (draft)

Goal: an Archicad → Blender sync that an architecture student or office can install and use
without scripting, and that never destroys the user's Blender work.

## Components

```
┌──────────── Archicad 29 ────────────┐        ┌──────────────── Blender ≥ 4.2 ────────────────┐
│ Tapir add-on (JSON on :19723)       │        │ Extension "AC Sync" (N-panel + preferences)    │
└───────────────▲─────────────────────┘        │   operators ──► acsync core (bundled)          │
                │ HTTP/JSON                    │                   ├ tapir client               │
                └──────────────────────────────┼───────────────────┤ ifc reader (ifcopenshell)  │
                         IFC file in temp dir  │                   ├ diff / matching            │
                                               │                   └ material mapping           │
                                               │   bpy layer: apply diff to the scene           │
                                               └────────────────────────────────────────────────┘
```

1. **`src/acsync` (core, no `bpy`)** – everything that can be tested without Blender.
   - `tapir`: tiny HTTP client (`urllib`, no dependencies): identity check, translator list,
     IFC export, error handling (modal-dialog error 4001, version check).
   - `ifc_reader`: IFC → `Element` records `{guid, element_id, type, name, vertices,
     triangles, per-face material names}`; unit conversion; filters (type, skip `IfcSpace`,
     `IfcOpeningElement`).
   - `diff`: compare the elements in the file with the elements the scene already knows
     (by GUID) → `added`, `changed`, `unchanged`, `removed`; change detection by a hash
     of the geometry stored on each Blender object.
   - `materials`: mapping Archicad surface name → Blender material (user table).
2. **`blender_addon/ac_sync`** – thin layer: preferences, panel, operators (`Sync`,
   `Dry run`), and `apply_diff` which writes into Blender. Bundles `ifcopenshell` as a wheel
   and the `acsync` core.
3. **Optional later:** CLI (`python -m acsync`) for headless use; auto-sync (notification
   client or polling).

The GUI lives **inside Blender** because Blender is where the result is used; a separate
desktop window would only add a second place to look. A standalone window can still be
added later on top of the same core.

## Matching and update rules

- Each synced object stores the Archicad **GUID** (and Element ID as a label) in custom
  properties. Matching is by GUID only.
- Everything the tool manages lives in one root collection (default `AC_Sync`) with
  sub-collections by element type. **Nothing outside it is read, changed or deleted.**
- **Changed** element: new vertices/faces are written into the existing mesh datablock, so
  material slots, modifiers, UVs the user added elsewhere, parenting, and names survive.
  Per-face material assignment is re-applied from Archicad; if the face count changed and
  per-face assignment can't be transferred, fall back to the old slots and flag
  "check manually".
- **Added** element: new object in the right sub-collection.
- **Removed** element: reported; deletion is a setting (default: move to a
  `AC_Sync – Removed` collection instead of deleting).
- **Dry run**: compute and show the diff, change nothing.
- No blanket orphan purge; only datablocks the tool itself freed are cleaned up.
- Material mapping table (Archicad surface → Blender material) applied only to slots still
  holding the raw Archicad material, so the user's own choices are never overwritten.

## Decisions taken

| Decision | Why |
|---|---|
| IFC via Tapir as transport | Free, scriptable, carries GUIDs; Tapir has no mesh command (see RESEARCH.md) |
| Translator "IFC4 Reference View-based Export" as default, configurable | One mesh per element + GUID + per-face materials, nothing lost (RESEARCH.md §4) |
| Match on Archicad GUID | Element IDs are duplicated by copy/paste; GUIDs are not |
| Core without `bpy` | Testable, reusable for a CLI |
| Blender extension with bundled wheels | Easy install for non-programmers (ifcopenshell cp313 win wheel is 41 MB) |

## Open questions

- Origin/units alignment (IFC survey point vs. project origin) and a setting for it.
- Trigger: button first; notification-driven auto-sync later.
- How to handle stairs, railings, morphs, hotlinked modules (untested).
- macOS/Linux support (Tapir has a macOS build; ifcopenshell wheels exist) – untested.
- Name and license.
