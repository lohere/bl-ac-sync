# SPDX-License-Identifier: GPL-3.0-or-later
"""Switch "Participates in Collision Detection" on for the length of one export.

``MaterialFlagSwitch`` turns the flag on for the given Building Materials and puts the original
settings back afterwards, also when the block fails. Before anything is changed the originals
are written to a recovery file in the temp folder; it is deleted once everything is verified,
and ``restore_pending`` finishes the job if the tool died in between (Blender closed during an
export, a crash). Which materials to switch is collision.py's business.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Self

from .collision import ExcludedMaterial
from .errors import UNEXPECTED_ANSWER, TapirCommandError
from .tapir import TapirClient


def recovery_path() -> str:
    return os.path.join(tempfile.gettempdir(), "acsync", "pending-restore.json")


def _delete_recovery() -> None:
    try:
        os.remove(recovery_path())
    except OSError:
        pass


def _read_materials(client: TapirClient, guids: list[str]) -> list[dict]:
    response = client.addon(
        "GetBuildingMaterials", {"attributeIds": [{"attributeId": {"guid": g}} for g in guids]}
    )
    return response["buildingMaterials"]


def _write_collision_flag(client: TapirClient, material: dict, value: bool) -> None:
    """Overwrite a Building Material with its own full settings and one changed flag (an
    overwrite may reset the fields that are left out)."""
    item = {k: v for k, v in material.items() if v is not None and k != "index"}
    item["collisionDetection"] = value
    response = client.addon(
        "CreateBuildingMaterials", {"overwriteExisting": True, "buildingMaterialDataArray": [item]}
    )
    for entry in response.get("attributeIds", []):
        if isinstance(entry, dict) and "error" in entry:
            raise TapirCommandError(
                f"Archicad refused to change '{material.get('name')}': {entry['error']}"
            )


def _differences(before: dict, after: dict) -> list[str]:
    keys = set(before) | set(after)
    return sorted(k for k in keys if k != "index" and before.get(k) != after.get(k))


def _restore(client: TapirClient, originals: list[dict]) -> list[str]:
    """Put ``originals`` back and check them. Returns problems (empty when all is as before)."""
    problems = []
    for original in originals:
        name = original.get("name", "?")
        guid = original["attributeId"]["guid"]
        try:
            current = _read_materials(client, [guid])[0]
            if _differences(original, current):
                _write_collision_flag(client, original, original.get("collisionDetection", True))
                current = _read_materials(client, [guid])[0]
            diff = _differences(original, current)
            if diff:
                problems.append(f"'{name}' differs from before in: {', '.join(diff)}")
        except UNEXPECTED_ANSWER as exc:
            problems.append(f"'{name}' could not be restored ({exc})")
    return problems


class MaterialFlagSwitch:
    """Context manager: collision detection is on for ``materials`` inside the block, and the
    original settings are back afterwards. ``warnings`` collects what went wrong while
    restoring."""

    def __init__(
        self, client: TapirClient, materials: list[ExcludedMaterial], project_path: str = ""
    ):
        self.client = client
        self.materials = materials
        self.project_path = project_path
        self.originals: list[dict] = []
        self.warnings: list[str] = []

    def __enter__(self) -> Self:
        found = _read_materials(self.client, [m.guid for m in self.materials])
        self.originals = [m for m in found if m.get("collisionDetection") is False]
        if not self.originals:
            return self
        self._save_recovery()
        try:
            for original in self.originals:
                _write_collision_flag(self.client, original, True)
            now = _read_materials(self.client, [m["attributeId"]["guid"] for m in self.originals])
            if not all(m.get("collisionDetection") is True for m in now):
                raise TapirCommandError("Archicad did not accept the change of the material flag.")
        except Exception:
            self._finish()
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._finish()
        return False

    def _finish(self) -> None:
        if not self.originals:
            return
        problems = _restore(self.client, self.originals)
        if problems:  # keep the recovery file: the next run tries again
            self.warnings.append(
                "Could not fully restore the Building Materials changed for the export: "
                + "; ".join(problems)
                + ". Check 'Participates in Collision Detection' in Archicad."
            )
        else:
            _delete_recovery()

    def _save_recovery(self) -> None:
        path = recovery_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {
            "project_path": self.project_path,
            "saved": time.strftime("%Y-%m-%d %H:%M:%S"),
            "materials": self.originals,
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)


def reexport_including(
    client: TapirClient,
    materials: list[ExcludedMaterial],
    project_path: str,
    export,
    read,
    check,
    progress=lambda text: None,
):
    """Export again with collision detection on for ``materials``, then restore them.

    ``export()`` writes the IFC, ``read()`` loads it and ``check(model)`` runs the completeness
    check; all three run while the flag is on. Returns (model, check result, restore warnings)."""
    progress("Switching on collision detection for: " + ", ".join(m.name for m in materials))
    with MaterialFlagSwitch(client, materials, project_path) as switch:
        progress("Archicad is exporting again (including those materials)...")
        export()
        progress("Reading geometry...")
        model = read()
        progress("Checking completeness...")
        result = check(model)
    return model, result, switch.warnings


def restore_pending(client: TapirClient, project_path: str) -> list[str]:
    """Undo a switch that was interrupted. Only acts when the recovery file belongs to the
    project that is open now. Returns messages for the user."""
    path = recovery_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    if data.get("project_path") != project_path:
        return []
    originals = data.get("materials") or []
    problems = _restore(client, originals)
    if problems:
        return [
            "An earlier sync was interrupted and some Building Materials could not be put back: "
            + "; ".join(problems)
        ]
    _delete_recovery()
    names = ", ".join(f"'{m.get('name')}'" for m in originals)
    return [
        f"An earlier sync was interrupted; restored the Collision Detection setting of {names}."
    ]
