# SPDX-License-Identifier: GPL-3.0-or-later
"""Sidebar panel of the morph tool: its own box under the sync panel, same 'bl-ac' tab."""

import bpy
from bpy.types import Panel

from . import operators, ui


class _Base:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "bl-ac"


class ACMORPH_PT_main(_Base, Panel):
    bl_label = "bl-ac morphs (experimental)"
    bl_idname = "ACMORPH_PT_main"

    def draw(self, context):
        layout = self.layout
        m = context.scene.acmorph
        busy = operators.is_busy()

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
        if state.checked and not state.ok:
            ui._lines(box, state.message)
        box.label(text="Needs Tapir 1.8.0 or newer")

        col = layout.column(align=True)
        col.enabled = not busy
        row = col.row(align=True)
        row.scale_y = 1.4
        row.operator("acmorph.pull", text="Pull", icon="IMPORT").dry_run = False
        row.operator("acmorph.push", text="Push", icon="EXPORT").dry_run = False
        row = col.row(align=True)
        row.operator("acmorph.pull", text="Dry run", icon="VIEWZOOM").dry_run = True
        row.operator("acmorph.push", text="Dry run", icon="VIEWZOOM").dry_run = True
        layout.prop(m, "conflict")

        if m.last_summary:
            box = layout.box()
            box.label(text="Last result", icon="TEXT")
            ui._lines(box, m.last_summary)
            if bpy.data.texts.get("bl-ac morphs Report"):
                box.operator("acmorph.copy_report", icon="COPYDOWN")


class ACMORPH_PT_options(_Base, Panel):
    bl_label = "Options"
    bl_idname = "ACMORPH_PT_options"
    bl_parent_id = "ACMORPH_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        m = context.scene.acmorph
        col = self.layout.column()
        col.prop(m, "pull_scope")
        col.prop(m, "push_scope")
        col.prop(m, "collection")
        col.prop(m, "apply_modifiers")
        col.prop(m, "planar_tolerance")
        col.label(text="Offset, port and materials: see bl-ac sync")


CLASSES = (ACMORPH_PT_main, ACMORPH_PT_options)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
