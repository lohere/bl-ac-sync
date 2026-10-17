"""Minimal client for the Archicad JSON API and the Tapir add-on.

Archicad listens on ``http://127.0.0.1:<port>`` (default 19723). Official commands are posted
as ``{"command": "API.X", "parameters": {...}}``. Tapir commands are wrapped in the official
``API.ExecuteAddOnCommand`` envelope with the ``TapirCommand`` namespace. Only the standard
library is used so the client also runs inside Blender.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from .errors import (
    CommandNotAvailableError,
    ModalDialogOpenError,
    TapirCommandError,
    TapirConnectionError,
    TapirVersionError,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 19723
PORT_RANGE = range(19723, 19744)  # Archicad tries consecutive ports for multiple instances
MIN_TAPIR_VERSION = (1, 7, 1)  # first version with named IFC translators
DEFAULT_TRANSLATOR = "IFC4 Reference View-based Export"
DEFAULT_SCOPE = "VisibleElementsOnAllStories"
EXPORT_SCOPES = (
    "EntireProject",
    "VisibleElementsOnAllStories",
    "AllElementsOnCurrentStory",
    "VisibleElementsOnCurrentStory",
    "SelectedElementsOnly",
)

_ERR_MODAL_DIALOG = 4001
_ERR_COMMAND_NOT_FOUND = (2002, 4010)


def parse_version(text: str) -> tuple[int, ...]:
    """'1.7.1' -> (1, 7, 1). Non-numeric parts are ignored."""
    parts = []
    for chunk in text.strip().split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        if digits:
            parts.append(int(digits))
    return tuple(parts)


@dataclass(frozen=True)
class ArchicadInfo:
    port: int
    archicad_version: int
    build_number: int
    tapir_version: str
    project_name: str
    project_path: str
    is_untitled: bool

    @property
    def tapir_ok(self) -> bool:
        return parse_version(self.tapir_version) >= MIN_TAPIR_VERSION


class TapirClient:
    def __init__(self, port: int = DEFAULT_PORT, host: str = DEFAULT_HOST, timeout: float = 15.0):
        self.port = port
        self.host = host
        self.timeout = timeout

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    # -- transport ---------------------------------------------------------------------------

    def request(
        self, command: str, parameters: dict | None = None, timeout: float | None = None
    ) -> dict:
        """Post an official API command and return its ``result`` dict."""
        payload: dict[str, Any] = {"command": command}
        if parameters is not None:
            payload["parameters"] = parameters
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                body = resp.read().decode("utf-8")
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            raise TapirConnectionError(
                f"Cannot reach Archicad on {self.url} ({exc}). Is Archicad running with a "
                "project open and the Tapir add-on installed?"
            ) from exc
        try:
            answer = json.loads(body)
        except json.JSONDecodeError as exc:
            raise TapirCommandError(f"Archicad returned invalid JSON: {body[:200]!r}") from exc
        if not answer.get("succeeded", False):
            self._raise(answer.get("error") or {}, command)
        return answer.get("result") or {}

    def addon(
        self,
        name: str,
        parameters: dict | None = None,
        *,
        namespace: str = "TapirCommand",
        timeout: float | None = None,
    ) -> dict:
        """Run a Tapir add-on command and return its ``addOnCommandResponse``."""
        try:
            result = self.request(
                "API.ExecuteAddOnCommand",
                {
                    "addOnCommandId": {"commandNamespace": namespace, "commandName": name},
                    "addOnCommandParameters": parameters or {},
                },
                timeout=timeout,
            )
        except CommandNotAvailableError as exc:
            self._raise({"code": exc.code, "message": str(exc)}, name)  # name the real command
            raise
        response = result.get("addOnCommandResponse") or {}
        # Some add-on commands report their own failure inside the response.
        if set(response) == {"error"} and isinstance(response["error"], dict):
            self._raise(response["error"], name)
        return response

    @staticmethod
    def _raise(error: dict, command: str) -> None:
        code = error.get("code")
        message = error.get("message") or "unknown Archicad error"
        if code == _ERR_MODAL_DIALOG:
            raise ModalDialogOpenError(
                "A dialog is open in Archicad. Close it and try again.", code, command
            )
        if code in _ERR_COMMAND_NOT_FOUND:
            raise CommandNotAvailableError(
                f"Command '{command}' is not available. Update the Tapir add-on "
                f"(>= {'.'.join(map(str, MIN_TAPIR_VERSION))}).",
                code,
                command,
            )
        raise TapirCommandError(f"{command}: {message}", code, command)

    # -- high-level commands -----------------------------------------------------------------

    def info(self) -> ArchicadInfo:
        product = self.request("API.GetProductInfo")
        version = self.addon("GetAddOnVersion").get("version", "0")
        project = self.addon("GetProjectInfo")
        return ArchicadInfo(
            port=self.port,
            archicad_version=int(product.get("version", 0)),
            build_number=int(product.get("buildNumber", 0)),
            tapir_version=str(version),
            project_name=str(project.get("projectName", "")),
            project_path=str(project.get("projectPath", "")),
            is_untitled=bool(project.get("isUntitled", False)),
        )

    def require_tapir_version(self) -> ArchicadInfo:
        info = self.info()
        if not info.tapir_ok:
            raise TapirVersionError(
                f"Tapir {info.tapir_version} is installed, but "
                f"{'.'.join(map(str, MIN_TAPIR_VERSION))} or newer is required. "
                "Download the latest release from "
                "https://github.com/ENZYME-APD/tapir-archicad-automation/releases"
            )
        return info

    def get_selection(self) -> list[str]:
        """GUIDs of the elements currently selected in Archicad."""
        response = self.addon("GetSelectedElements")
        return [e["elementId"]["guid"] for e in response.get("elements", [])]

    def select_elements(self, guids: Iterable[str], replace: bool = True) -> int:
        """Select elements in Archicad (a UI selection, the model is not changed).

        With ``replace`` the current selection is cleared first. Returns how many of the
        requested elements are selected afterwards.
        """
        wanted = list(dict.fromkeys(guids))
        if replace:
            current = self.get_selection()
            if current:
                self.addon(
                    "ChangeSelectionOfElements",
                    {"removeElementsFromSelection": [{"elementId": {"guid": g}} for g in current]},
                )
        if wanted:
            self.addon(
                "ChangeSelectionOfElements",
                {"addElementsToSelection": [{"elementId": {"guid": g}} for g in wanted]},
            )
        selected = {g.upper() for g in self.get_selection()}
        return sum(1 for g in wanted if g.upper() in selected)

    def list_ifc_translators(self) -> list[str]:
        response = self.addon("GetIFCExportTranslators")
        return [t["name"] for t in response.get("translators", [])]

    def export_ifc(
        self,
        path: str | os.PathLike,
        translator: str = DEFAULT_TRANSLATOR,
        scope: str = DEFAULT_SCOPE,
        timeout: float = 1800.0,
    ) -> str:
        """Save the open project as IFC. Returns the path. Blocks until Archicad is done."""
        if scope not in EXPORT_SCOPES:
            raise ValueError(f"scope must be one of {EXPORT_SCOPES}, got {scope!r}")
        path = os.fspath(path)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        if os.path.exists(path):
            os.remove(path)  # so a stale file can never be mistaken for a new export
        translators = self.list_ifc_translators()
        if translator not in translators:
            raise TapirCommandError(
                f"IFC translator {translator!r} not found in this project. Available: "
                + ", ".join(sorted(translators))
            )
        self.addon(
            "IFCFileOperation",
            {
                "method": "save",
                "ifcFilePath": path,
                "fileType": "ifc",
                "translatorName": translator,
                "elementsToExport": scope,
            },
            timeout=timeout,
        )
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            raise TapirCommandError(f"Archicad reported success but wrote no file at {path}")
        return path


def probe_instance(
    port: int = DEFAULT_PORT, host: str = DEFAULT_HOST, timeout: float = 0.6
) -> ArchicadInfo | None:
    """Ask one port whether an Archicad with Tapir answers there. ``None`` if not."""
    try:
        TapirClient(port, host, timeout=timeout).request("API.GetProductInfo")
    except (TapirConnectionError, TapirCommandError):
        return None
    try:
        return TapirClient(port, host, timeout=max(timeout, 5.0)).info()
    except (TapirConnectionError, TapirCommandError):
        return None  # Archicad is up but Tapir is missing, or a dialog is open


def find_instances(
    host: str = DEFAULT_HOST, ports: Iterable[int] = PORT_RANGE, timeout: float = 0.6
) -> list[ArchicadInfo]:
    """Scan the Archicad port range for running instances that answer to Tapir.

    Closed ports on Windows do not refuse the connection, they time out, so the ports are
    probed in parallel: the whole scan costs one timeout (~0.6 s) instead of one per port.
    For the usual single instance, ``probe_instance()`` on the default port is instant.
    """
    port_list = list(ports)
    if not port_list:
        return []
    with ThreadPoolExecutor(max_workers=len(port_list)) as pool:
        results = list(pool.map(lambda p: probe_instance(p, host, timeout), port_list))
    return [info for info in results if info is not None]
