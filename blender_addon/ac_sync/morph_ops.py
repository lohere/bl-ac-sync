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
        description=(
            "The Blender collection the morph tool works with. Push collection and Sync "
            "collection use every mesh object in it (sub-collections included), and morphs "
            "pulled from Archicad are put in it"
        ),
        default="bl-ac morphs",
    )
    apply_modifiers: BoolProperty(
        name="Apply modifiers",
        description="Send the result of the modifiers (subdivision, mirror, bevel ...) instead "
        "of the bare mesh. Archicad gets plain faces either way",
        default=True,
    )
    conflict: EnumProperty(
        name="If both changed",
        description="What Push and Pull do with a morph that changed in Blender and in Archicad "
        "since the last sync. Sync collection always skips and reports them",
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
    dry_run: BoolProperty(
        name="Dry run",
        description="Make the buttons only show what they would do. Nothing is changed",
        default=False,
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
    text = f"{prefix}{title}: {report.diff_summary}"
    if report.warnings:  # the panel only shows this line, so the first warning goes with it
        more = len(report.warnings) - 1
        text += f". Warning: {report.warnings[0]}" + (f" (+{more} more in the report)" if more else "")
    msettings.last_summary = text


def _collection_objects(m: ACMorphSettings) -> list[bpy.types.Object]:
    objects = morph_engine.collection_objects(m.collection)
    if not objects:
        raise AcSyncError(
            f"Nothing to push: no mesh objects in the collection '{m.collection}'. "
            "Choose the collection in the panel and put your meshes in it."
        )
    return objects


class _MorphOperator(Operator):
    dry_run: BoolProperty(
        name="Dry run", description="Only show what would change", default=False,
        options={"SKIP_SAVE"},
    )
    scope_tips = {}  # scope -> tooltip; subclasses fill it in

    @classmethod
    def poll(cls, context):
        return not operators.is_busy()

    @classmethod
    def description(cls, context, properties):
        text = cls.scope_tips.get(getattr(properties, "scope", ""), cls.bl_description)
        if properties.dry_run:
            return f"Preview only: {text[0].lower()}{text[1:]} Nothing is changed"
        return text

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
    bl_description = "Bring Archicad morphs into Blender as mesh objects, updating those already there."
    bl_options = {"REGISTER", "UNDO"}
    scope: EnumProperty(
        name="Pull",
        description="Which Archicad morphs to bring into Blender",
        items=[
            ("SELECTED", "Selected in Archicad", "Only the morphs currently selected in Archicad"),
            ("ALL", "All morphs", "Every morph in the Archicad project"),
        ],
        default="ALL",
        options={"SKIP_SAVE"},
    )
    scope_tips = {
        "SELECTED": "Bring only the morphs that are selected in Archicad into Blender, "
        "updating those already there.",
        "ALL": "Bring every Archicad morph into Blender as a mesh object in the morph "
        "collection, updating those already there.",
    }

    def run(self, context, api):
        scene = context.scene
        return morph_engine.pull(
            scene, scene.acsync, scene.acmorph, api, self.dry_run, scope=self.scope
        )


class ACMORPH_OT_push(_MorphOperator):
    bl_idname = "acmorph.push"
    bl_label = "Push to Archicad"
    bl_description = "Send mesh objects to Archicad as morphs."
    bl_options = {"REGISTER", "UNDO"}
    scope: EnumProperty(
        name="Push",
        description="Which Blender objects to send to Archicad",
        items=[
            ("SELECTED", "Selected objects", "The selected mesh objects"),
            ("COLLECTION", "Collection", "Every mesh object in the morph collection"),
        ],
        default="SELECTED",
        options={"SKIP_SAVE"},
    )
    scope_tips = {
        "SELECTED": "Send the selected mesh objects to Archicad as morphs. Objects sent before "
        "update the same morph.",
        "COLLECTION": "Send every mesh object in the morph collection (sub-collections "
        "included) to Archicad as morphs. Objects sent before update the same morph.",
    }

    def run(self, context, api):
        scene = context.scene
        m = scene.acmorph
        if self.scope == "COLLECTION":
            objects = _collection_objects(m)
        else:
            objects = [o for o in context.selected_objects if morph_engine.can_push(o)]
            if not objects:
                raise AcSyncError("Nothing to push: select mesh objects")
        return morph_engine.push(scene, scene.acsync, m, api, objects, self.dry_run)


class ACMORPH_OT_sync(_MorphOperator):
    bl_idname = "acmorph.sync"
    bl_label = "Sync collection"
    bl_description = (
        "Two-way sync of the morph collection in one click: bring the Archicad changes into the "
        "morphs that are already in it, then send everything new or changed in Blender. Morphs "
        "that changed on both sides are skipped and listed. New Archicad morphs are not imported "
        "(use Pull for that)."
    )
    bl_options = {"REGISTER", "UNDO"}

    def run(self, context, api):
        scene = context.scene
        m = scene.acmorph
        objects = _collection_objects(m)
        linked = {str(o[morph_engine.PROP_GUID]).upper() for o in objects if morph_engine.PROP_GUID in o}
        pulled = morph_engine.pull(
            scene, scene.acsync, m, api, self.dry_run, only=linked, conflict="SKIP"
        )
        pushed = morph_engine.push(scene, scene.acsync, m, api, objects, self.dry_run, "SKIP")
        return morph_engine.merge_reports(pulled, pushed)


class ACMORPH_OT_detach(Operator):
    bl_idname = "acmorph.detach"
    bl_label = "Detach from Archicad"
    bl_description = (
        "Forget which Archicad morph the selected objects belong to, so the next push makes "
        "new morphs. Use it on copies of pushed objects (a copy keeps the link). The existing "
        "morph in Archicad is not touched"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects)

    def execute(self, context):
        count = morph_engine.detach(context.selected_objects)
        self.report({"INFO"}, f"Detached {count} object(s)")
        return {"FINISHED"}


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


CLASSES = (
    ACMorphSettings,
    ACMORPH_OT_pull,
    ACMORPH_OT_push,
    ACMORPH_OT_sync,
    ACMORPH_OT_detach,
    ACMORPH_OT_copy_report,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.acmorph = bpy.props.PointerProperty(type=ACMorphSettings)


def unregister():
    del bpy.types.Scene.acmorph
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
