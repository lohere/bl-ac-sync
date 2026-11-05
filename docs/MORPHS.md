# Morph sync (Blender <-> Archicad), experimental

A second tool next to the IFC sync, with its own panel (**bl-ac morphs**, same sidebar tab). It moves
simple geometry the other way: model a mesh in Blender, **Push** it to Archicad as a Morph, keep
editing in either program, and **Pull** Archicad's morphs into Blender. Morphs are the Archicad element
that is painful to model by hand, which is why they come first.

Needs **Tapir 1.8.0 or newer** (the sync from Archicad does not). Status: proof of concept on the
`feature/two-way-morphs` branch, tested on Archicad 29 with Tapir 1.8.0.

## What it does

| Button | Does |
| --- | --- |
| **Push** | Sends the selected mesh objects (or every mesh in the morph collection) to Archicad. A new object becomes a new morph; an object that was sent before updates the same morph (same GUID, so its layer, ID and properties stay). |
| **Pull** | Brings Archicad morphs into Blender as mesh objects in the `bl-ac morphs` collection, or updates the ones already there. Can be limited to the morphs selected in Archicad. |
| **Dry run** | Both directions can first show what would change. |

Per face it carries the **material** both ways (see below), keeps **n-gons**, concave faces and
**open meshes** (sent as Archicad *Surface* bodies), and applies **modifiers** by default so an array
or bevel arrives as plain faces. Faces that are not flat are split into triangles (see *Limits*);
the report says how many. A mesh with an edge on more than two faces is warned about, since
Archicad cannot build it. Faces with holes that come from Archicad are split into triangles in Blender.

## Not losing work

Each object remembers a fingerprint of both sides as of the last sync. On the next run it can tell
which side changed:

* only Blender changed: Push updates Archicad; Pull leaves the object alone,
* only Archicad changed: Pull updates the object (in place, so vertex groups and shape keys survive
  when the faces stayed the same); Push says so and sends nothing,
* both changed: a **conflict**. The default is *Skip and report*; *Overwrite* lets the side you are
  syncing to win.

A morph deleted in Archicad is flagged on its Blender object (`acmorph_removed`), never deleted.
Pushing such an object makes a new morph.

**Copies.** Duplicating a pushed object (Shift+D) copies the link to its morph as well. Pushing or
pulling objects that share one morph would be a guess, so they are skipped with a warning. Select the
copy and press **Detach from Archicad** (Options): it forgets the link, and the next push makes the
copy a morph of its own. Detaching never touches Archicad.

## Coordinates and materials

* Blender world coordinates = Archicad project coordinates + the model **Offset** of the IFC sync
  (shared setting). Both programs are Z-up and use metres; no axis swap.
* A pulled object's pivot is the morph's own origin. A pushed morph's origin is the object's origin.
* Material -> surface when pushing, first match wins: the surface the IFC sync recorded on the
  material (`acsync_surface`), the **Materials** table of the IFC sync (reversed), the material's name,
  the name without Blender's `.001` or the configured prefix. No match: the face gets Archicad's
  default surface, and the report names those materials.
* Surface -> material when pulling uses the same mapping and naming as the IFC sync, with the colour
  and transparency of the Archicad surface for new materials.

## How it talks to Archicad

| Purpose | Tapir command |
| --- | --- |
| list morphs | `GetElementsByType` (`Morph`) |
| read faces | `Get3DGeometryOfElements` (world coordinates, a surface on every face; new in 1.8) |
| origin, Element ID | `GetDetailsOfElements` |
| surfaces | `GetAttributesByType` (`Surface`), `GetSurfaces` |
| create | `CreateMorphs` with a `body` |
| change | `ModifyMorphs` with a `body` (replaces the geometry, keeps the element) |

No IFC is involved. The core is in `acsync/morphs.py` (no Blender), the Blender side in
`morph_engine.py`, the buttons and settings in `morph_ops.py`, the panel in `morph_ui.py`.

## Measured against Archicad 29 / Tapir 1.8.0

* `CreateMorphs` accepts an arbitrary `body`; n-gons, a concave L-shaped cap, faces with holes and
  per-face surfaces all arrive intact.
* After **create** Archicad renumbers the vertices and faces. After **modify** it keeps the order.
  So the fingerprint of the Archicad side is always taken from a read-back, never from what was sent.
* The "axes" of a morph placement behave as matrix *rows* (`world = origin + (local . xAxis,
  local . yAxis, local . zAxis)`), which looks like a rotation in the wrong direction. Every write
  therefore sends identity axes and `local = world - origin`.
* A face without a surface does **not** take the morph's surface; it comes back as Archicad's default
  (*Paint - Light Gray*). Every face gets an explicit surface.
* `bodyType: "Surface"` works: an open mesh (a single quad) is created and reads back as an open
  Surface. A Solid with fewer than 4 faces is refused, so the body type follows whether the mesh is
  watertight.
* **Flatness:** a face with more than three corners is refused when it is warped by more than about
  **0.35 % of its longest diagonal** (the same ratio at 20 cm and at 20 m; tiny faces of a few cm are
  accepted whatever their warp). The tool splits faces beyond 0.1 % by default (setting *Flatness
  tolerance*). An absolute millimetre limit, which was the first idea, is wrong at both ends.
* **Shared edges:** a body with an edge on three or more faces is refused. Splitting a quad adds a
  diagonal, and that diagonal can land on an edge the mesh already has (Blender's own *Triangulate*
  does this too), so the tool picks the other diagonal when that happens.
* Several separate pieces, touching or overlapping ones included, are fine in one body.
* Every refusal reads only "Failed to create morph." Both causes above were found with Blender's
  Suzanne, by bisecting the face list against the live Archicad.
* A refused morph is reported inside the answer (`elements[i].error`), not as a failed request.
* An unsaved Archicad project has no project name, only `isUntitled`.
* Speed, against a real Archicad: 512 faces push in 0.3 s, 4,608 faces in 0.8 s, 8,192 in 1.5 s,
  49,928 in 9 s (an update of the same mesh: 11 s). Pulling 12 morphs, two of them spheres, takes 1.2 s.
  The time is mostly Archicad building the body; the Blender side needs about 2 s for 50,000 faces.
  The wait for Archicad grows with the mesh (30 s plus 1 s per 1,000 faces).
* The IFC export of the same project lists morphs too, as generic objects (`IfcBuildingElementProxy`)
  with the same GUID. Open-surface morphs (a single quad, a sheet) are *not* in the IFC at all.

## Limits

* **Warped faces become triangles.** An Archicad face has to be flat (within about 0.35 % of its size).
  Every face with more than three corners that is more warped than the *Flatness tolerance* (0.1 % by
  default) is therefore split into triangles when it is pushed. Flat quads and n-gons (walls, slabs, a
  box, an L-shaped cap) stay as they are, but curved or organic meshes (sculpts, subdivided shapes,
  Blender's Suzanne) arrive in Archicad as triangle meshes. That is a property of Archicad's morph,
  not something the tool can avoid. Consequences:
  * more faces than in Blender (Suzanne: 500 faces became 934), and the extra diagonal edges may show
    in Archicad's 3D view (not checked visually); the API cannot hide single edges (see below),
  * pulling the morph back gives the triangulated mesh, so the quads of the original are gone. The link
    to the Blender object stays, so later pushes still update the same morph,
  * editing such a morph in Archicad works on triangles.
* The Archicad API has no rounding: curved Blender geometry arrives as flat facets. Per-edge
  smooth/hidden display cannot be set (an Archicad SDK bug); only the default for all edges can, and
  the tool does not use that yet.
* Very dense meshes work but are slow: about 9 s for 50,000 faces. The report mentions meshes above
  20,000 faces.
* Edges without faces are not sent. UVs are not sent (Archicad's texture mapping is its own).
* Changing *Apply modifiers* changes what is sent, so the next sync sees the object as changed.
* Pushing creates one Archicad undo step per request (a batch is one request). Not yet checked in the
  Archicad UI.
* The IFC sync (the other panel) also imports morphs, as generic objects. Using both tools on the same
  project duplicates them in Blender, one copy per tool. Excluding morphs from the IFC sync is still to
  be decided.
* The calls run on the main thread; Blender is unresponsive while a very large mesh is sent.
* Objects (library parts) and Archicad Meshes (terrain) are not covered. `SetLibraryPartScripts` exists
  in Tapir 1.8.0, which makes generated objects possible later.

## Tests

* `tests/test_morphs.py`: geometry conversion, fingerprints and the exact Tapir requests, against a
  stand-in server.
* `tests/blender/run_morph_tests.py` (started by `tests/blender/run.py`): 26 tests inside headless
  Blender against an in-memory stand-in for Archicad: both directions, conflicts, copies, offsets,
  materials, mirrored objects, modifiers, warped faces and diagonals, deleted morphs, error messages,
  tooltips.
* `tests/blender/live_morph_check.py`: drives the real operators against a running Archicad. It
  creates morphs, so it refuses to run unless the open project is unsaved ("untitled") or named by
  `ACSYNC_LIVE_PROJECT`.
