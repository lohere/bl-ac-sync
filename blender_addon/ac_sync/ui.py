# SPDX-License-Identifier: GPL-3.0-or-later
"""N-panel (3D viewport sidebar, tab 'bl-ac')."""

import bpy
from bpy.types import Menu, Panel, UIList

from . import __version__, operators


class ACSYNC_UL_surfaces(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.label(text=item.name, icon="MATERIAL")
        row.prop_search(item, "blender_material", bpy.data, "materials", text="")


class ACSYNC_MT_translators(Menu):
    bl_label = "IFC translator"
    bl_description = "Choose the Archicad IFC translator used for the export"

    def draw(self, context):
        s = context.scene.acsync
        layout = self.layout
        if not len(s.translators):
            layout.label(text="List not loaded yet")
            layout.operator("acsync.refresh", text="Load from Archicad", icon="FILE_REFRESH")
            return
        for item in s.translators:
            op = layout.operator(
                "acsync.set_translator", text=item.name,
                icon="CHECKMARK" if item.name == s.translator else "BLANK1",
            )
            op.name = item.name


class _Base:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "bl-ac"


class ACSYNC_PT_main(_Base, Panel):
    bl_label = "bl-ac sync"
    bl_idname = "ACSYNC_PT_main"

    def draw(self, context):
        layout = self.layout
        s = context.scene.acsync
        busy = operators.is_busy()

        layout.prop(s, "source", expand=True)

        if s.source == "ARCHICAD":
            box = layout.box()
            state = operators.connection
            row = box.row(align=True)
            if not state.checked:
                row.label(text="Archicad: not checked", icon="QUESTION")
            elif state.ok:
                row.label(text=state.project, icon="CHECKMARK")
            else:
                row.label(text="Archicad not ready", icon="ERROR")
            row.operator("acsync.refresh", text="", icon="FILE_REFRESH")
            if state.checked and state.ok:
                box.label(text=state.details)
            if state.checked and not state.ok:
                _lines(box, state.message)
            for note in state.instances:
                _lines(box, note, icon="INFO")
            box.separator(factor=0.3)
            box.label(text="IFC translator")
            box.menu("ACSYNC_MT_translators", text=s.translator, icon="DOWNARROW_HLT")
            box.label(text="Export")
            box.prop(s, "scope", text="")
        else:
            layout.prop(s, "ifc_path", text="")

        col = layout.column(align=True)
        col.enabled = not busy
        row = col.row(align=True)
        row.scale_y = 1.4
        op = row.operator("acsync.sync", text="Sync", icon="FILE_REFRESH")
        op.dry_run = False
        op = row.operator("acsync.sync", text="Dry run", icon="VIEWZOOM")
        op.dry_run = True
        layout.prop(s, "select_changed")
        check = operators.last_completeness
        if s.source == "ARCHICAD" and check is not None and check.excluded_materials:
            box = layout.box()
            box.alert = True
            box.label(text="Skipped by Archicad", icon="ERROR")
            for line in check.material_lines():
                _lines(box, line)
            if not s.include_excluded_materials:
                box.prop(s, "include_excluded_materials")
        if busy:
            layout.label(text=operators._stage["text"] or "Working...", icon="TIME")

        if s.last_summary:
            box = layout.box()
            box.label(text="Last result", icon="TEXT")
            _lines(box, s.last_summary)
            if operators.last_changed:
                box.operator(
                    "acsync.select_changed",
                    text=f"Select changed ({len(operators.last_changed)})",
                    icon="RESTRICT_SELECT_OFF",
                )
            if s.last_check:
                _lines(box, s.last_check, icon="CHECKMARK" if s.last_check_ok else "ERROR")
                if operators.last_completeness and operators.last_completeness.unexplained:
                    box.operator("acsync.select_missing", icon="RESTRICT_SELECT_OFF")
            box.operator("acsync.copy_report", icon="COPYDOWN")
            if bpy.data.texts.get(operators.REPORT_TEXT_NAME):
                _lines(box, "Full report: Text Editor, 'bl-ac sync Report'")


class ACSYNC_PT_output(_Base, Panel):
    bl_label = "Output"
    bl_idname = "ACSYNC_PT_output"
    bl_parent_id = "ACSYNC_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        s = context.scene.acsync
        col = self.layout.column()
        col.prop(s, "root_collection")
        col.prop(s, "removed_policy", text="Removed")
        col.prop(s, "archicad_coordinates")
        sub = col.column()
        sub.active = s.archicad_coordinates
        sub.prop(s, "inherit_end_surfaces")
        col.prop(s, "offset")
        col.separator()
        col.prop(s, "generate_uvs")
        sub = col.column()
        sub.active = s.generate_uvs
        sub.prop(s, "uv_scale")
        col.separator()
        col.prop(s, "skip_spaces")
        col.prop(s, "skip_openings")
        col.prop(s, "skip_site")


class ACSYNC_PT_materials(_Base, Panel):
    bl_label = "Materials"
    bl_idname = "ACSYNC_PT_materials"
    bl_parent_id = "ACSYNC_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        s = context.scene.acsync
        layout = self.layout
        if not len(s.surfaces):
            layout.label(text="Run a sync to list the surfaces.")
            return
        layout.template_list(
            "ACSYNC_UL_surfaces", "", s, "surfaces", s, "surfaces_index", rows=5
        )
        layout.label(text="Empty = a material is created.")
        layout.operator("acsync.apply_material_map", icon="MATERIAL")
        layout.prop(s, "material_prefix")


class ACSYNC_PT_advanced(_Base, Panel):
    bl_label = "Advanced"
    bl_idname = "ACSYNC_PT_advanced"
    bl_parent_id = "ACSYNC_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        s = context.scene.acsync
        col = self.layout.column()
        col.prop(s, "port")
        col.prop(s, "check_completeness")
        sub = col.column()
        sub.active = s.check_completeness
        sub.prop(s, "include_excluded_materials")
        col.prop(s, "keep_ifc")
        col.separator()
        col.label(text=f"bl-ac sync {__version__}")


def _lines(layout, text: str, icon: str = "NONE") -> None:
    """Wrapped, compact text block (labels do not wrap by themselves)."""
    col = layout.column(align=True)
    col.scale_y = 0.75
    for i, line in enumerate(_wrap(text, 40)):
        col.label(text=line, icon=icon if i == 0 else "NONE")


def _wrap(text: str, width: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        if len(line) + len(word) + 1 > width and line:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        lines.append(line)
    return lines or [""]


CLASSES = (
    ACSYNC_UL_surfaces,
    ACSYNC_MT_translators,
    ACSYNC_PT_main,
    ACSYNC_PT_output,
    ACSYNC_PT_materials,
    ACSYNC_PT_advanced,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
