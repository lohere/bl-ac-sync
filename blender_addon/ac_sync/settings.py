"""Per-scene settings. They are saved in the .blend, so every project keeps its own."""

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatVectorProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import PropertyGroup

from acsync import DEFAULT_SCOPE, DEFAULT_TRANSLATOR, MaterialMap

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

    name: StringProperty(name="Archicad surface")
    blender_material: StringProperty(
        name="Blender material", description="Leave empty to create a material from the surface"
    )


class ACSyncSettings(PropertyGroup):
    source: EnumProperty(
        name="Source",
        items=[
            ("ARCHICAD", "Archicad", "Export from a running Archicad automatically"),
            ("FILE", "IFC file", "Read an IFC file you exported yourself"),
        ],
        default="ARCHICAD",
    )
    ifc_path: StringProperty(name="IFC file", subtype="FILE_PATH")
    port: IntProperty(
        name="Port",
        description="Tapir port of the Archicad to use. 0 picks the first Archicad found",
        default=0, min=0, max=65535,
    )
    last_port: IntProperty(
        name="Last port", description="Port where Archicad was found last time (tried first)",
        default=0, min=0, max=65535, options={"HIDDEN"},
    )
    translator: StringProperty(name="IFC translator", default=DEFAULT_TRANSLATOR)
    translators: CollectionProperty(type=ACSyncNameItem)
    scope: EnumProperty(name="Export", items=SCOPES, default=DEFAULT_SCOPE)

    root_collection: StringProperty(
        name="Collection",
        description="Everything the tool manages lives here; nothing outside is touched",
        default="AC_Sync",
    )
    removed_policy: EnumProperty(name="Removed elements", items=REMOVED_POLICIES, default="MOVE")
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
    skip_spaces: BoolProperty(name="Skip zones", default=True)
    skip_openings: BoolProperty(name="Skip opening voids", default=True)
    skip_site: BoolProperty(
        name="Skip terrain",
        description="Archicad Mesh elements arrive merged as one 'Site' object",
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
    surfaces: CollectionProperty(type=ACSyncSurfaceRow)
    surfaces_index: IntProperty()

    last_summary: StringProperty(default="")

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
