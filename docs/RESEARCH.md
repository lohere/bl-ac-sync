# Research notes (2026-10-17)

How to get Archicad geometry into Blender without a manual export each time, using only
free tools. Everything marked **measured** was tested on a real model (Archicad 29,
Tapir 1.7.1, ifcopenshell 0.9.0, Blender 5.2.2). Everything marked **unverified** comes
from documentation or forum posts only.

## 1. Existing options

| Option | Cost | Result |
|---|---|---|
| Bimdots "Blender Connection" (Archicad add-on) | paid | Does button-triggered incremental sends and keeps Blender material edits. Needs Blender ≥ 5. Matching key not documented. (unverified, manual page only) |
| CAD Bridger | beta, student-free | Claims "update in place". Few details public. (unverified) |
| Speckle | free tier | Receiving in Blender behaves like a fresh import (does not update existing objects), per a Speckle developer. 2026 status of v3 connectors unclear. (unverified) |
| Bonsai / BlenderBIM (IFC) | free | The IFC file is the source of truth; Blender-only material edits and textures are lost on save. Re-import matching untested. (unverified) |
| Datasmith / Twinmotion DirectLink | free | Live, but targets Unreal/Twinmotion, not Blender. |
| Custom C++ Archicad add-on | free, large effort | The only way to read raw mesh data through Archicad's API (`ACAPI_Element_Get3DInfo`, `ACAPI_3D_GetComponent`). Not needed, see below. |

## 2. What Tapir can and cannot do (measured: command list read from the live add-on)

- **No mesh/geometry command and no FBX export.** Nearest: 3D bounding boxes, parametric
  element details.
- `ifc_ifc_file_operation` saves the project as IFC headlessly, optionally with a named
  export translator and an element scope (`EntireProject`, `VisibleElementsOnAllStories`,
  `AllElementsOnCurrentStory`, `VisibleElementsOnCurrentStory`, `SelectedElementsOnly`).
  `translatorName` needs Tapir ≥ 1.7.1 (1.5.9 rejects it).
- `ifc_get_ifc_export_translators` lists translator names (≥ 1.7.1).
- `elements_set_element_notification_client` exists (change notifications). Not yet used.
- A modal dialog open in Archicad blocks every call (error 4001).
- Tapir listens on `localhost:19723` (same port as the official JSON API).

**Conclusion:** IFC export driven by Tapir is the free, scriptable way to get triangles out.

## 3. The IFC contents (measured)

- **`Name` = Element ID** (e.g. `WALL-0001`), **`Tag` = Archicad element GUID.** The GUID
  is unique and survives copy/paste, unlike the Element ID.
- In the test model **82 Element IDs were shared by more than one element** (after
  copy/paste) and 7 elements were unnumbered (`-0000`). Matching on the GUID makes this
  harmless.
- `IfcGlobalId` is unique too, but `Tag` is the same GUID Tapir returns, so it is the key
  to use (assumption: `Tag` is the Archicad GUID; confirmed by uniqueness, not yet by a
  direct comparison with a Tapir-returned GUID).
- Surface style names equal Archicad surface names (`Brick - Aged Light`, …), so material
  mapping by name works. Per-face materials are available from ifcopenshell
  (`geometry.material_ids`). The geometry's material objects are named
  `IfcSurfaceStyleRendering-<id>`; resolving them to the style name is still to do.
- Units are millimetres.

## 4. Translator comparison (measured, same model, `EntireProject`)

| | Default (last used) | Exact Geometry | **IFC4 Reference View** | IFC4 Design Transfer |
|---|---|---|---|---|
| Schema | IFC2X3 | IFC2X3 | IFC4 | IFC4 |
| File size | 7.1 MB | 7.6 MB | **4.4 MB** | 3.8 MB |
| Geometry products | 1177 | 1208 | **551** | 633 |
| With GUID tag | 489 | 500 | **530** | 612 |
| Composite walls | 687 layer parts | 687 layer parts | **one mesh, per-face materials** | one mesh |
| Curtain walls | 23–34 parts | 23–34 parts | **one mesh each** | 22–33 parts |
| Triangles | 78.6k | 101.6k | **101.6k** | 94.7k |
| Geometry read | 5.3 s | 6.2 s | 6.0 s | 7.8 s |

**Decision: IFC4 Reference View-based Export.** One mesh per Archicad element, GUID on every
element, per-face materials, and the same triangle count as the exact-BREP export (nothing
lost). It maps directly onto the "one object per element" model of the old FBX workflow.
The default export skipped hidden elements; Exact Geometry and Design Transfer split
composite elements into parts with no GUID of their own.

Open points: how `EntireProject` vs. visible-only affects results; `IfcSpace` (zones) and
`IfcOpeningElement` (voids) should probably not become Blender objects; stairs and railings
were not present in sufficient numbers to judge; model origin / "IFC Model position"
setting still to check against Blender.

## 5. Mesh format and the core pipeline (measured, 2026-10-17)

- ifcopenshell's default triangulates everything (101.6k triangles for the test model).
  `triangulation-type = POLYHEDRON_WITHOUT_HOLES` keeps quads and n-gons: **49.9k faces**
  (31.9k quads, ~14k triangles where a face has a hole, the rest n-gons). The core uses this
  mode so Blender gets clean wireframes. `POLYHEDRON_WITH_HOLES` would need hole support
  that Blender meshes do not have.
- With `use-world-coords`, vertices are already in **metres** whatever the project unit
  (mm here). No conversion needed.
- `geometry.material_ids` gives one material per face. `material.instance_id()` resolves to the
  `IfcSurfaceStyle`, whose `Name` is the Archicad surface name. `material.diffuse` and
  `.transparency` give colour/alpha (glass: 0.69).
- Tapir wire format: official commands as `{"command": "API.X"}`, Tapir commands through
  `API.ExecuteAddOnCommand` with namespace `TapirCommand`. `TapirCommand.GetProjectInfo`
  returns the project name and path. Responses are UTF-8.
- End-to-end timing on the sandbox model (visible elements, IFC4 Reference View): export 2.0 s
  (4.1 MB), read 7.3 s, 429 elements, 37.7k faces, 18 surfaces.
- IFC4 `IfcSpace` has no `Tag`; Archicad zones come out untagged and are excluded anyway.

## 6. Terrain, coordinates and what gets exported (measured, 2026-10-17)

- **Archicad Mesh elements become one `IfcSite`.** All meshes are merged into a single
  `IfcSite` entity (one face set per mesh), which has no `Tag`, so per-mesh GUIDs are lost.
  The reader imports it as one "Site" object keyed by `ifc:<IfcGlobalId>` (identical in all four
  test exports). A custom translator that maps Mesh to e.g. `IfcGeographicElement` would give
  one tagged element per mesh; the reader already handles that case.
- **The IFC model is rotated.** Archicad bakes Project North into the `IfcSite` placement
  (here exactly 23.0 degrees about the origin, no translation); raw geometry stays in Archicad
  model coordinates. After undoing the placement, all 45 slabs and 16 roofs match Archicad's own
  3D bounding boxes to 0.0000 m (beams/columns within 6-8 cm). The reader does this by default
  (`archicad_coordinates=True`).
- **Open: walls, windows and doors deviate from Tapir's `Get3DBoundingBoxes`** by 0.15-0.9 m
  (e.g. walls longer by 0.15 m at each end, a roof-trimmed wall shorter than Archicad's
  untrimmed box, windows at different heights). Not understood: the IFC may be right (trims,
  junctions) and the bounding boxes may be untrimmed or defined differently. Needs a visual
  comparison before relying on opening positions.
- **"Export geometries that participate in Collision Detection only"** is **On** in the IFC4
  Reference View, Exact Geometry and default translators and **Off** in IFC4 Design Transfer.
  With it On, the two large context meshes (about 300 m, layer 45, storey -2) are not exported:
  the Site holds 4 face sets. With it Off (Design Transfer) the Site holds 13 items spanning
  about 365 x 340 m. Fix: duplicate the "IFC4 Reference View-based Export" translator in Archicad,
  edit its Model Filter and turn that option off. Elements that are not flagged for collision
  detection (Tags and Categories panel) are silently missing otherwise.
- **Correction (same day):** the collision-detection flag explains the two context meshes but
  not every missing element. A completeness check on the sandbox model found 16 elements
  missing at "Entire project": 7 on a hidden layer (hidden furniture) and 9 visible ones (8 Basic
  walls on the structure layers plus a door). Their building materials all have
  `collisionDetection: true` (read via Tapir), and a translator with the option Off
  (Design Transfer) still misses WALL-0224, WALL-0086 and DOOR-0017. Cause unknown (candidates:
  tiny or degenerate geometry such as the 0.17 m stub WALL-0086, elements trimmed away by Solid
  Element Operations, door hosted in a missing wall). The tool reports them but does not guess.
- **Resolved (user check in Archicad):** those 9 elements show **no geometry even when isolated**
  with "show selection only". Archicad's IFC export is right to skip them; the API's visibility
  filters (layer, renovation, structure display) all say "visible" for them, so a missing element
  can be geometry-less without any filter explaining it. Likely (unverified): walls fully covered
  by overlapping walls of higher priority, or removed by Solid Element Operations.
- **Do not use `IsVisibleIn3D`:** it reflects the 3D window's current display state. With "show
  selection only" on, it returned exactly the selected elements (9 of 233 walls). The check
  uses layer / renovation / structure-display filters only.
- Built: completeness check (`acsync.completeness`) comparing Archicad's element GUIDs per type
  with the IFC; it also asks Archicad which visibility filter hides each missing element.
- Earlier idea: a completeness check comparing Archicad's element counts per type (Tapir
  `GetElementsByType`) with what the IFC contained, to report silent omissions.

## 7. Reproducing

`tools/research/*.py` are the scripts used (run with a Python that has `ifcopenshell`
installed, pass IFC paths as arguments). Exports were made with
`ifc_ifc_file_operation {method: save, translatorName: ..., elementsToExport: EntireProject}`.
