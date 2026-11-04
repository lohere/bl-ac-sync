# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-scene settings. They are saved in the .blend, so every project keeps its own."""

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import PropertyGroup

from .acsync import DEFAULT_SCOPE, DEFAULT_TRANSLATOR, MaterialMap

REMOVED_POLICIES = [
    ("MOVE", "Move to 'Removed' collection", "Keep the object, but file it away (safest)"),
    ("HIDE", "Hide", "Keep the object where it is and hide it"),
    ("REPORT", "Only report", "Leave the object untouched and list it in the report"),
    ("DELETE", "Delete", "Delete the object and its mesh"),
]

SCOPES = [
    ("VisibleElementsOnAllStories", "Visible, all stories", "What you see in 3D, every story"),
    ("EntireProject", "Entire project", "Everything, including hidden layers"),
    ("SelectedElementsOnly", "Selection only", "Only elements selected in Archicad"),
    ("AllElementsOnCurrentStory", "Current story (all)", "All elements of the active story"),
    ("VisibleElementsOnCurrentStory", "Current story (visible)", "Visible elements, active story"),
]


class ACSyncNameItem(PropertyGroup):
    """A bare name, used for the dynamic translator list."""


class ACSyncSurfaceRow(PropertyGroup):
    """One Archicad surface and the Blender material it should use."""

    name: StringProperty(name="Archicad surface", description="Name of the surface in Archicad")
    blender_material: StringProperty(
        name="Blender material", description="Leave empty to create a material from the surface"
    )


class ACSyncSettings(PropertyGroup):
    source: EnumProperty(
        name="Source",
        description="Where the model comes from",
        items=[
            ("ARCHICAD", "Archicad", "Export from a running Archicad automatically"),
            ("FILE", "IFC file", "Read an IFC file you exported yourself"),
        ],
        default="ARCHICAD",
    )
    ifc_path: StringProperty(
        name="IFC file", subtype="FILE_PATH",
        description="The IFC file to import (Archicad: File > Save As > IFC)",
    )
    port: IntProperty(
        name="Port",
        description="Tapir port of the Archicad to use. 0 picks the first Archicad found",
        default=0, min=0, max=65535,
    )
    last_port: IntProperty(
        name="Last port", description="Port where Archicad was found last time (tried first)",
        default=0, min=0, max=65535, options={"HIDDEN"},
    )
    translator: StringProperty(
        name="IFC translator", default=DEFAULT_TRANSLATOR,
        description="The Archicad IFC export settings to use. The default keeps one mesh per "
        "element with its GUID and surfaces",
    )
    translators: CollectionProperty(type=ACSyncNameItem, options={"HIDDEN"})
    scope: EnumProperty(
        name="Export", items=SCOPES, default=DEFAULT_SCOPE,
        description="Which elements Archicad exports",
    )

    root_collection: StringProperty(
        name="Collection",
        description="Everything the tool manages lives here; nothing outside is touched",
        default="bl-ac sync",
    )
    removed_policy: EnumProperty(
        name="Removed elements", items=REMOVED_POLICIES, default="MOVE",
        description="What happens to objects whose element was deleted in Archicad",
    )
    offset: FloatVectorProperty(
        name="Offset", description="Shift the whole model (metres)", subtype="TRANSLATION",
        unit="LENGTH", default=(0.0, 0.0, 0.0),
    )
    archicad_coordinates: BoolProperty(
        name="Archicad coordinates",
        description=(
            "Undo the Project North rotation Archicad bakes into IFC, so the model sits where it "
            "does in Archicad. Turn off to keep IFC world coordinates"
        ),
        default=True,
    )
    inherit_end_surfaces: BoolProperty(
        name="Match wall end finishes",
        description=(
            "Archicad's IFC export ignores a wall's 'inherit end surface' setting and leaves the "
            "wall ends striped with the wall's own layers. Give those ends the finish of the "
            "wall they join, as Archicad shows them. Needs 'Archicad coordinates'"
        ),
        default=True,
    )
    generate_uvs: BoolProperty(
        name="Generate UVs",
        description=(
            "IFC has no texture coordinates. Create box-style UVs in real-world scale so image "
            "textures work. Replaced on every geometry update"
        ),
        default=True,
    )
    uv_scale: FloatProperty(
        name="Metres per UV unit",
        description="1 = a texture repeats once per metre; larger values make textures bigger",
        default=1.0, min=0.01, max=1000.0, precision=3,
    )
    skip_spaces: BoolProperty(
        name="Skip zones", default=True,
        description="Do not import Archicad zones (rooms)",
    )
    skip_openings: BoolProperty(
        name="Skip opening voids", default=True,
        description="Do not import the cut-out volumes of windows, doors and openings",
    )
    skip_site: BoolProperty(
        name="Skip terrain",
        description="Archicad Mesh elements arrive merged as one 'Site' object",
        default=False,
    )
    check_completeness: BoolProperty(
        name="Check completeness",
        description="After an Archicad sync, list elements that exist in Archicad but did not "
        "arrive in the IFC, and why",
        default=True,
    )
    include_excluded_materials: BoolProperty(
        name="Include excluded materials",
        description=(
            "Archicad skips elements whose Building Material does not 'Participate in Collision "
            "Detection' (the ground often does). When that happens, switch the option on for "
            "just those materials, export again, and put it back as it was. Changes the open "
            "Archicad project for the length of the export and costs a second export. "
            "Needs 'Check completeness'"
        ),
        default=False,
    )
    select_changed: BoolProperty(
        name="Select changed objects",
        description="After a sync or dry run, select the objects that were added or changed "
        "(only the selection changes, a dry run still leaves the scene as it is)",
        default=False,
    )
    keep_ifc: BoolProperty(
        name="Keep IFC file", description="Do not delete the temporary IFC export", default=False
    )

    material_prefix: StringProperty(
        name="New material prefix",
        description="Added to the name of materials the tool creates",
        default="",
    )
    surfaces: CollectionProperty(type=ACSyncSurfaceRow, options={"HIDDEN"})
    surfaces_index: IntProperty(options={"HIDDEN"})

    last_summary: StringProperty(default="", options={"HIDDEN"})
    last_check: StringProperty(default="", options={"HIDDEN"})  # completeness summary line
    last_check_ok: BoolProperty(default=True, options={"HIDDEN"})

    def material_map(self) -> MaterialMap:
        mapping = {row.name: row.blender_material for row in self.surfaces if row.blender_material}
        return MaterialMap(mapping=mapping, prefix=self.material_prefix)

    def sync_surfaces(self, names):
        """Add rows for newly seen surfaces; keep existing mappings."""
        known = {row.name for row in self.surfaces}
        for name in sorted(names):
            if name not in known:
                self.surfaces.add().name = name


CLASSES = (ACSyncNameItem, ACSyncSurfaceRow, ACSyncSettings)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.acsync = bpy.props.PointerProperty(type=ACSyncSettings)


def unregister():
    del bpy.types.Scene.acsync
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
