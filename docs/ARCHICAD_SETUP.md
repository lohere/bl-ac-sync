# Archicad setup (read this once)

AC Sync gets geometry out of Archicad through an IFC export that it starts itself. For that to
work, and to be complete, Archicad needs the following. Everything here was learned on a real
model (Archicad 29); items marked *unverified* come from Graphisoft's documentation only.

## 1. Install Tapir (required)

Tapir is a free, open-source Archicad add-on that lets other programs talk to Archicad.
Install **version 1.7.1 or newer** from
<https://github.com/ENZYME-APD/tapir-archicad-automation/releases> and restart Archicad. Older
versions cannot choose the IFC translator by name; AC Sync will tell you if yours is too old.
The Windows installer is not code-signed, so Windows may warn you.

## 2. Keep Archicad ready

- Archicad must be running with the project open.
- **Close every dialog** (IFC Translators, Attribute Manager, ...). An open dialog blocks all
  communication and AC Sync reports "A dialog is open in Archicad".
- Archicad is busy while it exports; the export of a mid-sized model takes a few seconds.

## 3. The IFC translator

Default: **IFC4 Reference View-based Export**. It gives one mesh per Archicad element, the
element's GUID, and per-surface materials. Other translators work but split walls into layer
parts (Exact Geometry) or keep curtain walls as many parts (Design Transfer).

You can pick any translator in the panel. If you make your own (File > Interoperability > IFC >
IFC Translators), start from Reference View and keep the schema on IFC4.

## 4. Things Archicad does to your model on the way out

| What you see in Blender | Why | What to do |
|---|---|---|
| Terrain is **one object "Site"** | Archicad merges all Mesh elements into a single IfcSite | Nothing; it is imported as one object. A custom translator that maps Mesh to another IFC class would keep them separate (untested) |
| Some elements are **missing** | Archicad's IFC export left them out | Read the report after a sync: it lists them, their layers, and whether a hidden layer / Renovation filter explains it |
| Large context meshes missing | The translator option "Export geometries that participate in Collision Detection only" (Geometry Conversion preset) skips elements whose **Building Material** does not have *Participates in Collision Detection* | Tick that flag on the material (Options > Element Attributes > Building Materials), or use a translator with the option off. Turning it off globally also exports air-gap layers of composites as solid bodies |
| Hidden furniture / MEP not imported | Elements on hidden layers are not exported, even with "Entire project" | Show the layer in Archicad before syncing |
| Model sits **rotated** against Archicad | Archicad writes Project North into the IFC | AC Sync undoes this by default ("Archicad coordinates" in the Output panel). Turn it off only if you want IFC world coordinates |
| No texture mapping from Archicad | IFC carries no UV coordinates, so Archicad's texture orientation and scale are lost | AC Sync generates its own UVs in real-world scale (Output panel > Generate UVs, "Metres per UV unit"). Set the texture's real size through that scale or the material's mapping node |

## 5. What the completeness check does and does not tell you

After each Archicad sync, AC Sync compares the GUIDs of all 3D elements in Archicad with the
GUIDs in the IFC. It names what is missing and, for hidden ones, which filter hides them. For
elements that are visible but still missing it does **not** guess a cause; use **Select in
Archicad** (panel button) to see them. On the test model, 9 such walls/doors turned out to have no
geometry in Archicad's 3D window at all, so skipping them was correct (the collision flag was ruled
out). Meshes (merged), zones and openings are not compared.
