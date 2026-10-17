"""Operators: sync (threaded), connection refresh, material map, report."""

from __future__ import annotations

import os
import tempfile
import threading
import time
from dataclasses import dataclass, field

import bpy
from bpy.props import BoolProperty
from bpy.types import Operator

from acsync import (
    DEFAULT_EXCLUDED_TYPES,
    AcSyncError,
    IfcModel,
    TapirClient,
    find_instances,
    read_ifc,
)

from . import engine

REPORT_TEXT_NAME = "AC Sync Report"


# -- shared UI state (not saved) --------------------------------------------------------------


@dataclass
class ConnectionState:
    checked: bool = False
    ok: bool = False
    message: str = "Not checked yet"
    instances: list[str] = field(default_factory=list)


connection = ConnectionState()
_busy = threading.Event()
_stage = {"text": ""}


def is_busy() -> bool:
    return _busy.is_set()


# -- helpers -----------------------------------------------------------------------------------


def _pick_client(port: int) -> tuple[TapirClient, str]:
    if port:
        return TapirClient(port), ""
    found = find_instances()
    if not found:
        raise AcSyncError(
            "No Archicad found. Start Archicad, open the project and make sure the Tapir "
            "add-on is installed (or switch the source to an IFC file)."
        )
    note = ""
    if len(found) > 1:
        names = ", ".join(f"{i.project_name} (port {i.port})" for i in found)
        note = f"Several Archicads are running ({names}); using the first. Set a port to choose."
    return TapirClient(found[0].port), note


def _excluded_types(params: dict) -> set[str]:
    excluded = set(DEFAULT_EXCLUDED_TYPES) - {"IfcSpace", "IfcOpeningElement"}
    if params["skip_spaces"]:
        excluded.add("IfcSpace")
    if params["skip_openings"]:
        excluded.add("IfcOpeningElement")
    return excluded


@dataclass
class Job:
    params: dict
    model: IfcModel | None = None
    error: Exception | None = None
    note: str = ""
    project: str = ""
    done: bool = False


def _run_job(job: Job) -> None:
    """Runs on a worker thread: must not touch bpy."""
    p = job.params
    temp_path = None
    try:
        if p["source"] == "ARCHICAD":
            _stage["text"] = "Looking for Archicad..."
            client, job.note = _pick_client(p["port"])
            info = client.require_tapir_version()
            job.project = info.project_name
            _stage["text"] = f"Archicad is exporting '{info.project_name}'..."
            stamp = time.strftime("%Y%m%d-%H%M%S")
            temp_path = os.path.join(tempfile.gettempdir(), "acsync", f"export-{stamp}.ifc")
            client.export_ifc(temp_path, p["translator"], p["scope"])
            ifc_path = temp_path
        else:
            ifc_path = bpy.path.abspath(p["ifc_path"]) if p["ifc_path"] else ""
            if not ifc_path:
                raise AcSyncError("Choose an IFC file first.")
            job.project = os.path.basename(ifc_path)
        _stage["text"] = "Reading geometry..."
        job.model = read_ifc(ifc_path, excluded_types=_excluded_types(p))
    except Exception as exc:  # reported to the user on the main thread
        job.error = exc
    finally:
        if temp_path and not p["keep_ifc"] and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        job.done = True


def _snapshot(settings) -> dict:
    return {
        "source": settings.source,
        "ifc_path": settings.ifc_path,
        "port": settings.port,
        "translator": settings.translator,
        "scope": settings.scope,
        "skip_spaces": settings.skip_spaces,
        "skip_openings": settings.skip_openings,
        "keep_ifc": settings.keep_ifc,
    }


def _store_report(settings, report: engine.Report, project: str) -> None:
    text = report.text()
    block = bpy.data.texts.get(REPORT_TEXT_NAME) or bpy.data.texts.new(REPORT_TEXT_NAME)
    block.clear()
    block.write(f"Source: {project}\n{time.strftime('%Y-%m-%d %H:%M:%S')}\n\n{text}\n")
    prefix = "Dry run: " if report.dry_run else ""
    settings.last_summary = f"{prefix}{report.diff_summary} ({project})"


# -- operators ---------------------------------------------------------------------------------


class ACSYNC_OT_sync(Operator):
    bl_idname = "acsync.sync"
    bl_label = "Sync"
    bl_description = "Bring the Archicad model into this scene, keeping your Blender work"
    bl_options = {"REGISTER", "UNDO"}

    dry_run: BoolProperty(
        name="Dry run", description="Only show what would change", default=False,
        options={"SKIP_SAVE"},
    )
    blocking: BoolProperty(
        name="Wait", description="Run without a background thread (scripting, tests)",
        default=False, options={"SKIP_SAVE", "HIDDEN"},
    )

    _timer = None
    _job: Job | None = None

    @classmethod
    def poll(cls, context):
        return not _busy.is_set()

    def execute(self, context):
        job = Job(_snapshot(context.scene.acsync))
        _busy.set()
        try:
            _run_job(job)
        finally:
            _busy.clear()
        return self._finish(context, job)

    def invoke(self, context, event):
        if self.blocking:
            return self.execute(context)
        self._job = Job(_snapshot(context.scene.acsync))
        _busy.set()
        _stage["text"] = "Starting..."
        threading.Thread(target=_run_job, args=(self._job,), daemon=True).start()
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.2, window=context.window)
        wm.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == "ESC":
            return self._cancel(context)
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        context.workspace.status_text_set(f"AC Sync: {_stage['text']}   (Esc to cancel)")
        if not self._job.done:
            return {"RUNNING_MODAL"}
        self._cleanup(context)
        return self._finish(context, self._job)

    def _cancel(self, context):
        # The worker cannot be interrupted; its result is simply ignored.
        self._cleanup(context)
        self.report({"WARNING"}, "AC Sync cancelled (Archicad may still finish its export).")
        return {"CANCELLED"}

    def _cleanup(self, context):
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        context.workspace.status_text_set(None)
        _busy.clear()

    def _finish(self, context, job: Job):
        if job.error is not None:
            message = str(job.error)
            if not isinstance(job.error, AcSyncError):
                message = f"{type(job.error).__name__}: {job.error}"
            self.report({"ERROR"}, message)
            connection.ok = False
            connection.message = message
            return {"CANCELLED"}
        scene = context.scene
        settings = scene.acsync
        try:
            report = engine.apply_model(scene, settings, job.model, dry_run=self.dry_run)
        except Exception as exc:
            self.report({"ERROR"}, f"Applying the model failed: {type(exc).__name__}: {exc}")
            return {"CANCELLED"}
        if job.note:
            report.warnings.insert(0, job.note)
        _store_report(settings, report, job.project)
        self.report({"INFO"}, ("Dry run: " if self.dry_run else "Sync done: ") + report.diff_summary)
        return {"FINISHED"}


class ACSYNC_OT_refresh(Operator):
    bl_idname = "acsync.refresh"
    bl_label = "Check Archicad"
    bl_description = "Look for a running Archicad and load its IFC translator list"

    def execute(self, context):
        settings = context.scene.acsync
        connection.checked = True
        try:
            client, note = _pick_client(settings.port)
            info = client.require_tapir_version()
            translators = client.list_ifc_translators()
        except Exception as exc:
            connection.ok = False
            connection.message = str(exc)
            connection.instances = []
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        settings.translators.clear()
        for name in sorted(translators):
            settings.translators.add().name = name
        connection.ok = True
        connection.message = (
            f"{info.project_name or 'untitled'} - Archicad {info.archicad_version}, "
            f"Tapir {info.tapir_version}"
        )
        connection.instances = [note] if note else []
        if settings.translator not in translators:
            self.report({"WARNING"}, f"Translator '{settings.translator}' not found in this project.")
        return {"FINISHED"}


class ACSYNC_OT_apply_material_map(Operator):
    bl_idname = "acsync.apply_material_map"
    bl_label = "Apply material mapping"
    bl_description = (
        "Switch materials the tool created to the Blender materials in the table. "
        "Materials you assigned yourself are never changed"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        changed, missing = engine.apply_material_map(context.scene.acsync)
        if missing:
            self.report({"WARNING"}, "Materials not found: " + ", ".join(missing))
        self.report({"INFO"}, f"{changed} material slot(s) updated")
        return {"FINISHED"}


class ACSYNC_OT_copy_report(Operator):
    bl_idname = "acsync.copy_report"
    bl_label = "Copy report"
    bl_description = "Copy the last sync report to the clipboard"

    def execute(self, context):
        block = bpy.data.texts.get(REPORT_TEXT_NAME)
        if block is None:
            self.report({"INFO"}, "No report yet")
            return {"CANCELLED"}
        context.window_manager.clipboard = block.as_string()
        self.report({"INFO"}, "Report copied")
        return {"FINISHED"}


CLASSES = (
    ACSYNC_OT_sync,
    ACSYNC_OT_refresh,
    ACSYNC_OT_apply_material_map,
    ACSYNC_OT_copy_report,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
