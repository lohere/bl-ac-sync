# SPDX-License-Identifier: GPL-3.0-or-later
"""Operators: sync (threaded), connection refresh, material map, report."""

from __future__ import annotations

import contextlib
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, StringProperty
from bpy.types import Operator

from . import engine
from .acsync import (
    DEFAULT_EXCLUDED_TYPES,
    MORPH_MIN_TAPIR_VERSION,
    PARTIAL_SCOPES,
    UNEXPECTED_ANSWER,
    AcSyncError,
    Completeness,
    IfcModel,
    MorphApi,
    TapirClient,
    apply_inherited_end_surfaces,
    check_completeness,
    discovery_error,
    read_ifc,
    reexport_including,
    restore_pending,
    scan_ports,
)
from .acsync.tapir import DEFAULT_PORT, parse_version

REPORT_TEXT_NAME = "bl-ac sync Report"


# -- shared UI state (not saved) --------------------------------------------------------------


@dataclass
class ConnectionState:
    checked: bool = False
    ok: bool = False
    message: str = "Not checked yet"  # error text when not ok
    project: str = ""
    details: str = ""  # "Archicad 29, Tapir 1.7.1"
    instances: list[str] = field(default_factory=list)


connection = ConnectionState()
last_completeness: Completeness | None = None  # result of the last Archicad sync (not saved)
last_changed: list[str] = []  # GUIDs of the objects the last sync/dry run added or changed
_busy = threading.Event()
_stage = {"text": ""}
pending = None  # the last dry run (a Job), which "Apply" can carry out without asking Archicad again


def is_busy() -> bool:
    return _busy.is_set()


def _redraw(context) -> None:
    """Repaint the sidebar so status text and button states update."""
    screen = getattr(context, "screen", None)
    for area in screen.areas if screen else ():
        if area.type == "VIEW_3D":
            area.tag_redraw()


# -- helpers -----------------------------------------------------------------------------------


def _pick_client(port: int, hint: int = 0) -> tuple[TapirClient, str]:
    """Find Archicad. ``port`` is the user's explicit choice (0 = automatic); ``hint`` is the
    port that worked last time. Tries hint and default port first (both at once), then scans."""
    if port:
        return TapirClient(port), ""
    quick = [p for p in dict.fromkeys([hint, DEFAULT_PORT]) if p]
    statuses = scan_ports(ports=quick)  # known ports: ~0.03 s when Archicad is there
    found = [s.info for s in statuses if s.info is not None]
    if not found:
        statuses = scan_ports()  # parallel scan of the whole range (~0.6 s)
        found = [s.info for s in statuses if s.info is not None]
    if not found:
        raise discovery_error(statuses)  # says whether Archicad, Tapir or a dialog is the problem
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
    if params["skip_site"]:
        excluded.add("IfcSite")
    return excluded


def _find_morphs(job, client, info) -> None:
    """Ask Archicad which elements are Morphs. The morph tool owns those (two-way), so the IFC
    read leaves them out; without this they would come in twice, once per tool."""
    if not job.params["morphs"]:
        return
    needed = ".".join(map(str, MORPH_MIN_TAPIR_VERSION))
    if parse_version(info.tapir_version) < MORPH_MIN_TAPIR_VERSION:
        job.morph_note = (
            f"Morphs come in as plain objects: syncing them both ways needs Tapir {needed} or newer "
            f"(found {info.tapir_version})."
        )
        return
    try:
        job.params["morph_guids"] = set(MorphApi(client).list_guids())
        job.morph_sync = True
    except AcSyncError as exc:
        job.morph_note = f"Morphs come in as plain objects: could not list them ({exc})."


@dataclass
class Job:
    params: dict
    model: IfcModel | None = None
    error: Exception | None = None
    note: str = ""
    project: str = ""
    port: int = 0  # where Archicad was found, remembered in the scene settings
    completeness: Completeness | None = None
    restored: list[str] = field(default_factory=list)  # leftovers of an interrupted sync, undone
    wall_end_lines: list[str] = field(default_factory=list)  # what matching wall ends did
    timings: dict[str, float] = field(default_factory=dict)  # stage -> seconds, in order
    info: object | None = None  # ArchicadInfo of the project that was synced
    translators: list[str] = field(default_factory=list)
    morph_sync: bool = False  # the morph tool takes over the morphs: sync them after the IFC
    morph_note: str = ""
    done: bool = False

    @contextlib.contextmanager
    def stage(self, label: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.timings[label] = self.timings.get(label, 0.0) + time.perf_counter() - start

    def timing_text(self) -> str:
        total = sum(self.timings.values())
        parts = [f"{label} {sec:.1f} s" for label, sec in self.timings.items() if sec >= 0.05]
        return f"{total:.1f} s" + (f" ({', '.join(parts)})" if parts else "")


def _read(ifc_path: str, p: dict) -> IfcModel:
    return read_ifc(
        ifc_path,
        excluded_types=_excluded_types(p),
        archicad_coordinates=p["archicad_coordinates"],
        uv_scale=p["uv_scale"],
        skip_guids=p.get("morph_guids") or (),
    )


def _match_wall_ends(client, model: IfcModel, p: dict) -> list[str]:
    """Give wall ends the finish Archicad shows (the IFC lacks it). Returns report lines; a
    problem here must never stop the sync."""
    if not (p["inherit_end_surfaces"] and p["archicad_coordinates"]):
        return []
    try:
        return apply_inherited_end_surfaces(client, model).lines
    except UNEXPECTED_ANSWER as exc:
        return [f"Wall ends: could not match the finishes ({exc})."]


def _check(client, model: IfcModel, p: dict) -> Completeness:
    # Terrain can only be compared with the IFC when it is in Archicad coordinates and present.
    meshes = p["archicad_coordinates"] and not p["skip_site"]
    return check_completeness(client, model, p["scope"], check_meshes=meshes)


def _include_excluded_materials(job: Job, client, info, ifc_path: str, p: dict) -> None:
    """Export again with collision detection switched on for the materials that cost elements,
    then put the original settings back. On any Archicad-side problem the first export stays."""
    first = job.completeness
    wall_end_lines: list[str] = []

    def read() -> IfcModel:
        model = _read(ifc_path, p)
        wall_end_lines[:] = _match_wall_ends(client, model, p)
        return model

    try:
        model, check, warnings = reexport_including(
            client,
            first.excluded_materials,
            info.project_path,
            export=lambda: client.export_ifc(ifc_path, p["translator"], p["scope"]),
            read=read,
            check=lambda m: _check(client, m, p),
            progress=lambda text: _stage.update(text=text),
        )
    except AcSyncError as exc:
        first.notes.append(f"Could not include the excluded materials ({exc}).")
        return
    check.included_materials = [m.name for m in first.excluded_materials]
    check.notes = warnings + check.notes
    job.model, job.completeness, job.wall_end_lines = model, check, wall_end_lines


def _run_job(job: Job) -> None:
    """Runs on a worker thread: must not touch bpy."""
    p = job.params
    temp_path = None
    try:
        if p["source"] == "ARCHICAD":
            _stage["text"] = "Looking for Archicad..."
            client, job.note = _pick_client(p["port"], p["last_port"])
            job.port = client.port
            info = client.require_tapir_version()
            job.info = info
            job.project = info.project_name
            job.restored = restore_pending(client, info.project_path)
            job.translators = client.list_ifc_translators()
            _find_morphs(job, client, info)
            _stage["text"] = f"Archicad is exporting '{info.project_name}'..."
            stamp = time.strftime("%Y%m%d-%H%M%S")
            temp_path = os.path.join(tempfile.gettempdir(), "acsync", f"export-{stamp}.ifc")
            with job.stage("Archicad export"):
                client.export_ifc(temp_path, p["translator"], p["scope"])
            ifc_path = temp_path
        else:
            ifc_path = bpy.path.abspath(p["ifc_path"]) if p["ifc_path"] else ""
            if not ifc_path:
                raise AcSyncError("Choose an IFC file first.")
            job.project = os.path.basename(ifc_path)
        _stage["text"] = "Reading geometry..."
        with job.stage("reading geometry"):
            job.model = _read(ifc_path, p)
        if p["source"] == "ARCHICAD":
            with job.stage("wall ends"):
                job.wall_end_lines = _match_wall_ends(client, job.model, p)
        if p["source"] == "ARCHICAD" and p["check_completeness"]:
            _stage["text"] = "Checking completeness..."
            with job.stage("completeness check"):
                job.completeness = _check(client, job.model, p)
            if p["include_excluded"] and job.completeness.excluded_materials:
                with job.stage("second export (excluded materials)"):
                    _include_excluded_materials(job, client, info, temp_path, p)
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
        "last_port": settings.last_port,
        "translator": settings.translator,
        "scope": settings.scope,
        "partial": settings.source == "ARCHICAD" and settings.scope in PARTIAL_SCOPES,
        "skip_spaces": settings.skip_spaces,
        "skip_openings": settings.skip_openings,
        "skip_site": settings.skip_site,
        "archicad_coordinates": settings.archicad_coordinates,
        "uv_scale": settings.uv_scale if settings.generate_uvs else None,
        "inherit_end_surfaces": settings.inherit_end_surfaces,
        "check_completeness": settings.check_completeness,
        "include_excluded": settings.include_excluded_materials,
        "keep_ifc": settings.keep_ifc,
        "morphs": settings.morphs and settings.source == "ARCHICAD",
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
    def description(cls, context, properties):
        if properties.dry_run:
            return "Preview what a sync would add, change or remove. The scene is not changed"
        return cls.bl_description

    @classmethod
    def poll(cls, context):
        return not _busy.is_set()

    def execute(self, context):
        job = Job(_snapshot(context.scene.acsync))
        _forget_pending()
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
        _forget_pending()
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
        context.workspace.status_text_set(f"bl-ac sync: {_stage['text']}   (Esc to cancel)")
        _redraw(context)
        if not self._job.done:
            return {"RUNNING_MODAL"}
        self._cleanup(context)
        return self._finish(context, self._job)

    def _cancel(self, context):
        # The worker cannot be interrupted; its result is simply ignored.
        self._cleanup(context)
        self.report({"WARNING"}, "bl-ac sync cancelled (Archicad may still finish its export).")
        return {"CANCELLED"}

    def _cleanup(self, context):
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        context.workspace.status_text_set(None)
        _busy.clear()
        _redraw(context)

    def _finish(self, context, job: Job):
        return _complete(self, context, job, self.dry_run)


def _forget_pending() -> None:
    """A new sync replaces whatever dry run was waiting to be applied."""
    global pending
    pending = None


def _complete(op, context, job: Job, dry_run: bool):
    """Main-thread half of a sync (and of "Apply"): put the model in the scene, sync the morphs,
    write the report. ``op`` only supplies ``report()``."""
    global pending, last_completeness
    if job.error is not None:
        message = str(job.error)
        if not isinstance(job.error, AcSyncError):
            message = f"{type(job.error).__name__}: {job.error}"
        op.report({"ERROR"}, message)
        connection.ok = False
        connection.message = message
        return {"CANCELLED"}
    scene = context.scene
    settings = scene.acsync
    if job.info is not None:
        _show_connected(settings, job.info, job.port, job.translators, job.note, job.restored)
    elif job.port:
        settings.last_port = job.port  # tried first next time
    try:
        with job.stage("applying in Blender"):
            report = engine.apply_model(
                scene, settings, job.model, dry_run=dry_run,
                detect_removed=not job.params["partial"],
            )
    except Exception as exc:
        op.report({"ERROR"}, f"Applying the model failed: {type(exc).__name__}: {exc}")
        return {"CANCELLED"}
    if job.morph_sync:
        with job.stage("morphs"):
            _sync_morphs(context, report, dry_run)
    report.timing = job.timing_text()
    if job.note:
        report.warnings.insert(0, job.note)
    if job.morph_note:
        report.warnings.append(job.morph_note)
    report.warnings[:0] = job.restored
    report.lines[:0] = job.wall_end_lines
    check = job.completeness
    last_completeness = check
    settings.last_check = check.summary() if check and check.checked else ""
    settings.last_check_ok = bool(check is None or not check.checked or check.ok)
    if check is not None and check.checked and check.has_findings:
        # Unexpected gaps are warnings; hidden-in-Archicad ones are only information.
        target = report.warnings if not check.ok else report.lines
        target.extend(check.lines())
    _store_report(settings, report, job.project)
    last_changed[:] = report.changed_guids
    pending = job if dry_run and job.model is not None else None
    op.report({"INFO"}, ("Dry run: " if dry_run else "Sync done: ") + report.diff_summary)
    if settings.select_changed and last_changed:
        _select_last_changed(op, context)
    return {"FINISHED"}


def _sync_morphs(context, report: engine.Report, dry_run: bool) -> None:
    """Two-way sync of the morph collection, as part of the main Sync. A problem here is a
    warning: the IFC half already succeeded."""
    from . import morph_ops  # morph_ops imports this module

    try:
        morphs = morph_ops.sync_everything(context, dry_run)
    except AcSyncError as exc:
        report.warnings.append(f"Morphs: {exc}")
        return
    report.diff_summary += f". Morphs - {morphs.diff_summary}"
    report.lines += [f"Morph: {line}" for line in morphs.lines]
    report.warnings += [w if w.startswith("Morph") else f"Morph {w}" for w in morphs.warnings]
    report.created_materials += [m for m in morphs.created_materials if m not in report.created_materials]
    report.missing_materials += [m for m in morphs.missing_materials if m not in report.missing_materials]


class ACSYNC_OT_apply(Operator):
    bl_idname = "acsync.apply"
    bl_label = "Apply"
    bl_description = (
        "Carry out what the last dry run showed, without exporting from Archicad again. Changes "
        "made in Archicad since the dry run arrive with the next sync"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return pending is not None and not _busy.is_set()

    def execute(self, context):
        return _complete(self, context, pending, dry_run=False)


@dataclass
class Check:
    port: int
    hint: int = 0
    found_port: int = 0
    info: object | None = None
    translators: list[str] = field(default_factory=list)
    note: str = ""
    restored: list[str] = field(default_factory=list)
    restore: bool = True  # undo leftovers of an interrupted sync (not on the automatic check)
    error: Exception | None = None
    done: bool = False


def _show_connected(settings, info, port, translators, note="", restored=()) -> None:
    """Record that Archicad answered, so the panel shows it (also after a plain sync)."""
    if port:
        settings.last_port = port
    if translators:
        settings.translators.clear()
        for name in sorted(translators):
            settings.translators.add().name = name
    connection.checked = True
    connection.ok = True
    connection.project = info.project_name or "untitled"
    connection.details = f"Archicad {info.archicad_version}, Tapir {info.tapir_version}"
    connection.message = f"{connection.project} - {connection.details}"
    connection.instances = ([note] if note else []) + list(restored)


def startup_check() -> None:
    """Look for Archicad once, when Blender starts or a file is opened, so the panel says what
    is going on instead of "not checked". Quiet: no error, nothing is changed in Archicad."""
    try:
        settings = bpy.context.scene.acsync
        check = Check(settings.port, settings.last_port, restore=False)
        _run_check(check)
        connection.checked = True
        if check.error is None:
            _show_connected(
                settings, check.info, check.found_port, check.translators, check.note
            )
        else:
            connection.ok = False
            connection.message = str(check.error)
    except Exception:
        pass


def _run_check(check: Check) -> None:
    """Runs on a worker thread: must not touch bpy."""
    try:
        _stage["text"] = "Looking for Archicad..."
        client, check.note = _pick_client(check.port, check.hint)
        check.found_port = client.port
        check.info = client.require_tapir_version()
        if check.restore:
            check.restored = restore_pending(client, check.info.project_path)
        check.translators = client.list_ifc_translators()
    except Exception as exc:
        check.error = exc
    finally:
        check.done = True


class ACSYNC_OT_refresh(Operator):
    bl_idname = "acsync.refresh"
    bl_label = "Check Archicad"
    bl_description = "Look for a running Archicad and load its IFC translator list"

    blocking: BoolProperty(
        name="Wait", description="Run without a background thread (scripting, tests)",
        default=False, options={"SKIP_SAVE", "HIDDEN"},
    )

    _timer = None
    _check: Check | None = None

    @classmethod
    def poll(cls, context):
        return not _busy.is_set()

    def execute(self, context):
        check = Check(context.scene.acsync.port, context.scene.acsync.last_port)
        _run_check(check)
        return self._finish(context, check)

    def invoke(self, context, event):
        if self.blocking:
            return self.execute(context)
        self._check = Check(context.scene.acsync.port, context.scene.acsync.last_port)
        _busy.set()
        threading.Thread(target=_run_check, args=(self._check,), daemon=True).start()
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.1, window=context.window)
        wm.modal_handler_add(self)
        _redraw(context)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        if not self._check.done:
            return {"RUNNING_MODAL"}
        context.window_manager.event_timer_remove(self._timer)
        self._timer = None
        _busy.clear()
        return self._finish(context, self._check)

    def _finish(self, context, check: Check):
        settings = context.scene.acsync
        connection.checked = True
        _redraw(context)
        if check.error is not None:
            connection.ok = False
            connection.message = str(check.error)
            connection.instances = []
            self.report({"ERROR"}, str(check.error))
            return {"CANCELLED"}
        _show_connected(
            settings, check.info, check.found_port, check.translators, check.note, check.restored
        )
        if settings.translator not in check.translators:
            self.report(
                {"WARNING"}, f"Translator '{settings.translator}' not found in this project."
            )
        return {"FINISHED"}


class ACSYNC_OT_select_missing(Operator):
    bl_idname = "acsync.select_missing"
    bl_label = "Select in Archicad"
    bl_description = (
        "Select, in Archicad, the elements that are visible there but were not exported, "
        "so you can see which they are (only the selection changes, not the model)"
    )

    @classmethod
    def poll(cls, context):
        return last_completeness is not None and bool(last_completeness.unexplained)

    def execute(self, context):
        settings = context.scene.acsync
        guids = [m.guid for m in last_completeness.unexplained]
        try:
            client = TapirClient(settings.port or settings.last_port or DEFAULT_PORT, timeout=15)
            selected = client.select_elements(guids)
        except AcSyncError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report(
            {"INFO"}, f"Selected {selected} of {len(guids)} elements in Archicad (F5 shows 3D)."
        )
        return {"FINISHED"}


def _select_last_changed(op, context) -> bool:
    settings = context.scene.acsync
    selected, skipped = engine.select_objects(
        context.view_layer, settings.root_collection, last_changed
    )
    text = f"Selected {selected} changed object(s)"
    if skipped:
        text += f"; {skipped} not selectable (hidden, locked or gone)"
    op.report({"INFO"} if selected else {"WARNING"}, text + ".")
    return selected > 0


class ACSYNC_OT_select_changed(Operator):
    bl_idname = "acsync.select_changed"
    bl_label = "Select changed"
    bl_description = (
        "Select the objects the last sync or dry run added or changed. "
        "Only the selection changes, not the scene"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(last_changed)

    def execute(self, context):
        return {"FINISHED"} if _select_last_changed(self, context) else {"CANCELLED"}


class ACSYNC_OT_set_translator(Operator):
    bl_idname = "acsync.set_translator"
    bl_label = "Use translator"
    bl_description = "Use this IFC translator for the Archicad export"
    bl_options = {"INTERNAL"}

    name: StringProperty(name="Translator", description="Name of the IFC translator")

    def execute(self, context):
        context.scene.acsync.translator = self.name
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
    ACSYNC_OT_apply,
    ACSYNC_OT_refresh,
    ACSYNC_OT_apply_material_map,
    ACSYNC_OT_copy_report,
    ACSYNC_OT_select_missing,
    ACSYNC_OT_select_changed,
    ACSYNC_OT_set_translator,
)


@persistent
def _on_file_loaded(*_args):
    bpy.app.timers.register(_once, first_interval=1.0)


def _once():
    startup_check()
    # returns None: a timer that does not repeat


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    if _on_file_loaded not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_file_loaded)
    bpy.app.timers.register(_once, first_interval=1.0)  # Blender just started


def unregister():
    if _on_file_loaded in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_file_loaded)
    if bpy.app.timers.is_registered(_once):
        bpy.app.timers.unregister(_once)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
