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

## 6. Reproducing

`tools/research/*.py` are the scripts used (run with a Python that has `ifcopenshell`
installed, pass IFC paths as arguments). Exports were made with
`ifc_ifc_file_operation {method: save, translatorName: ..., elementsToExport: EntireProject}`.
