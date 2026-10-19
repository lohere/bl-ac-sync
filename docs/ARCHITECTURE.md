# Architecture (draft)

Goal: an Archicad → Blender sync that an architecture student or office can install and use
without scripting, and that never destroys the user's Blender work.

## Components

```
┌──────────── Archicad 29 ────────────┐        ┌──────────────── Blender ≥ 5.0 ────────────────┐
│ Tapir add-on (JSON on :19723)       │        │ Extension "AC Sync" (N-panel + preferences)    │
└───────────────▲─────────────────────┘        │   operators ──► acsync core (bundled)          │
                │ HTTP/JSON                    │                   ├ tapir client               │
                └──────────────────────────────┼───────────────────┤ ifc reader (ifcopenshell)  │
                         IFC file in temp dir  │                   ├ diff / matching            │
                                               │                   └ material mapping           │
                                               │   bpy layer: apply diff to the scene           │
                                               └────────────────────────────────────────────────┘
```

1. **`blender_addon/ac_sync/acsync` (core, no `bpy`)** – everything that can be tested without
   Blender. It is a sub-package of the add-on (Blender's extension policy only allows modules
   inside the extension's own package) and is exposed to pip/pytest as the top-level `acsync`.
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

## Implementation notes (prototype)

- Sync runs in a background thread (Tapir export + IFC read); the scene is changed on the
  main thread in one undo step. `blocking=True` on the operator runs it synchronously
  (scripts, tests). Esc cancels, but Archicad may still finish an export that was started.
- Each object remembers which material slot stands for which Archicad surface
  (`acsync_slots`). New surfaces append a slot; slots the user re-assigned are never
  overwritten. "Apply material mapping" only switches materials the tool created itself.
- A changed model offset is treated as a geometry change for every object.
- Removed elements: move to `<root> - Removed` (default), hide, report only, or delete.
  A removed element that comes back is restored. Partial exports (selection, one story) cannot
  prove an element is gone, so they skip removal detection (`detect_removed=False`).
- The core is a sub-package of the extension and ifcopenshell ships as a wheel listed in the
  manifest, so Blender installs everything itself. Verified by installing the built zip into a clean
  Blender profile; Blender's extension checker reports no policy violations.

## Known gaps

- **UVs are generated, not Archicad's.** IFC has none, so each face is projected onto its own plane
  in world metres (walls: horizontal along the face / height; floors and roofs: world X projected
  onto the plane), which keeps textures at real-world scale and continuous across neighbouring
  faces and elements. Archicad's per-surface texture orientation is not recoverable. A geometry
  update replaces the UV map (also any UV map the user added).
- Face-level user edits (sculpting, loop cuts) on a synced mesh are replaced on update; only
  object-level work (modifiers, materials, parenting, properties) survives.
- Archicad's IFC export can silently leave elements out (hidden layers, geometry-less elements,
  materials without the collision-detection flag). The completeness check lists them after every
  Archicad sync; it cannot say *why* for elements that are visible in Archicad.
- Wall/opening geometry has not been compared visually with Archicad (see RESEARCH.md section 6).
- Archicad Mesh elements arrive merged as a single "Site" object.

## Open questions

- Trigger: button first; notification-driven auto-sync later.
- How to handle stairs, railings, morphs, hotlinked modules (untested).
- macOS/Linux support (Tapir has a macOS build; ifcopenshell wheels exist) – untested.
- Final name (AC Sync is a working title).
