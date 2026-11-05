# SPDX-License-Identifier: GPL-3.0-or-later
"""Settings and operators of the morph tool (Blender <-> Archicad Morphs).

A separate tool next to the IFC sync: it has its own settings (``scene.acmorph``) and its own
panel, and shares only the connection settings (port) and the model offset / material table of
the main tool, so both place and colour things the same way.
"""

from __future__ import annotations

import time

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty
from bpy.types import Operator, PropertyGroup

from . import engine, morph_engine, operators
from .acsync import (
    MORPH_MIN_TAPIR_VERSION,
    AcSyncError,
    MorphApi,
    TapirVersionError,
)
from .acsync.tapir import parse_version

REPORT_TEXT_NAME = "bl-ac morphs Report"


class ACMorphSettings(PropertyGroup):
    collection: StringProperty(
        name="Collection",
        description="Morphs pulled from Archicad are put here. Objects you push can be anywhere",
        default="bl-ac morphs",
    )
    pull_scope: EnumProperty(
        name="Pull",
        description="Which Archicad morphs to bring into Blender",
        items=[
            ("ALL", "All morphs", "Every morph in the Archicad project"),
            ("SELECTED", "Selected in Archicad", "Only the morphs currently selected in Archicad"),
        ],
        default="ALL",
    )
    push_scope: EnumProperty(
        name="Push",
        description="Which Blender objects to send to Archicad",
        items=[
            ("SELECTED", "Selected objects", "The selected mesh objects"),
            ("COLLECTION", "Morph collection", "Every mesh object in the morph collection"),
        ],
        default="SELECTED",
    )
    apply_modifiers: BoolProperty(
        name="Apply modifiers",
        description="Send the result of the modifiers (subdivision, mirror, bevel ...) instead "
        "of the bare mesh. Archicad gets plain faces either way",
        default=True,
    )
    conflict: EnumProperty(
        name="If both changed",
        description="What to do with a morph that changed in Blender and in Archicad since the "
        "last sync",
        items=[
            ("SKIP", "Skip and report", "Leave both versions as they are and list the morph"),
            ("OVERWRITE", "Overwrite", "The side you are syncing to is replaced"),
        ],
        default="SKIP",
    )
    flatness: FloatProperty(
        name="Flatness tolerance",
        description=(
            "Faces with more than three corners that are warped by more than this share of their "
            "size are split into triangles. Archicad refuses faces that are not flat; its own "
            "limit is about 0.35 %, so keep this below that"
        ),
        default=0.1, min=0.0, max=0.3, precision=3, subtype="PERCENTAGE",
    )
    last_summary: StringProperty(default="", options={"HIDDEN"})

    def flatness_ratio(self) -> float:
        return self.flatness / 100.0


def _connect(context) -> tuple[MorphApi, str]:
    settings = context.scene.acsync
    client, note = operators._pick_client(settings.port, settings.last_port)
    info = client.require_tapir_version()
    if parse_version(info.tapir_version) < MORPH_MIN_TAPIR_VERSION:
        needed = ".".join(map(str, MORPH_MIN_TAPIR_VERSION))
        raise TapirVersionError(
            f"Morph sync needs Tapir {needed} or newer (found {info.tapir_version}). "
            "Download it from https://github.com/ENZYME-APD/tapir-archicad-automation/releases "
            "and restart Archicad."
        )
    settings.last_port = client.port
    return MorphApi(client), note


def _store_report(msettings, report: engine.Report, title: str) -> None:
    block = bpy.data.texts.get(REPORT_TEXT_NAME) or bpy.data.texts.new(REPORT_TEXT_NAME)
    block.clear()
    block.write(f"{title}\n{time.strftime('%Y-%m-%d %H:%M:%S')}\n\n{report.text()}\n")
    prefix = "Dry run: " if report.dry_run else ""
    msettings.last_summary = f"{prefix}{title}: {report.diff_summary}"


class _MorphOperator(Operator):
    dry_run: BoolProperty(
        name="Dry run", description="Only show what would change", default=False,
        options={"SKIP_SAVE"},
    )

    @classmethod
    def poll(cls, context):
        return not operators.is_busy()

    @classmethod
    def description(cls, context, properties):
        if properties.dry_run:
            return f"Preview what {cls.bl_label.lower()} would do. Nothing is changed"
        return cls.bl_description

    def execute(self, context):
        try:
            api, note = _connect(context)
            report = self.run(context, api)
        except AcSyncError as exc:
            operators.connection.ok = False
            operators.connection.message = str(exc)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if note:
            report.warnings.insert(0, note)
        _store_report(context.scene.acmorph, report, self.bl_label)
        self.report({"INFO"}, f"{self.bl_label}: {report.diff_summary}")
        return {"FINISHED"}


class ACMORPH_OT_pull(_MorphOperator):
    bl_idname = "acmorph.pull"
    bl_label = "Pull morphs"
    bl_description = "Bring Archicad morphs into Blender as mesh objects, updating those already there"
    bl_options = {"REGISTER", "UNDO"}

    def run(self, context, api):
        scene = context.scene
        return morph_engine.pull(scene, scene.acsync, scene.acmorph, api, self.dry_run)


class ACMORPH_OT_push(_MorphOperator):
    bl_idname = "acmorph.push"
    bl_label = "Push to Archicad"
    bl_description = (
        "Send mesh objects to Archicad as morphs. Objects sent before update the same morph. "
        "Archicad changes are one undo step each"
    )
    bl_options = {"REGISTER", "UNDO"}

    def run(self, context, api):
        scene = context.scene
        m = scene.acmorph
        if m.push_scope == "COLLECTION":
            coll = bpy.data.collections.get(m.collection)
            candidates = list(coll.all_objects) if coll else []
        else:
            candidates = list(context.selected_objects)
        objects = [o for o in candidates if morph_engine.can_push(o)]
        if not objects:
            raise AcSyncError(
                "Nothing to push: select mesh objects"
                if m.push_scope == "SELECTED"
                else f"Nothing to push: no mesh objects in the collection '{m.collection}'"
            )
        return morph_engine.push(scene, scene.acsync, m, api, objects, self.dry_run)


class ACMORPH_OT_copy_report(Operator):
    bl_idname = "acmorph.copy_report"
    bl_label = "Copy report"
    bl_description = "Copy the last morph report to the clipboard"

    def execute(self, context):
        block = bpy.data.texts.get(REPORT_TEXT_NAME)
        if block is None:
            self.report({"INFO"}, "No report yet")
            return {"CANCELLED"}
        context.window_manager.clipboard = block.as_string()
        self.report({"INFO"}, "Report copied")
        return {"FINISHED"}


CLASSES = (ACMorphSettings, ACMORPH_OT_pull, ACMORPH_OT_push, ACMORPH_OT_copy_report)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.acmorph = bpy.props.PointerProperty(type=ACMorphSettings)


def unregister():
    del bpy.types.Scene.acmorph
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
