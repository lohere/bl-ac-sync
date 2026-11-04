# SPDX-License-Identifier: GPL-3.0-or-later
"""AC-Blender Sync core: Archicad (Tapir) -> IFC -> mesh data, independent of Blender."""

from .collision import ExcludedMaterial
from .completeness import Completeness, MissingElement, check_completeness
from .diff import Change, Diff, SceneRecord, compute_diff
from .endsurface import EndSurfaceResult, apply_inherited_end_surfaces
from .errors import (
    UNEXPECTED_ANSWER,
    AcSyncError,
    CommandNotAvailableError,
    IfcReadError,
    ModalDialogOpenError,
    TapirCommandError,
    TapirConnectionError,
    TapirNotInstalledError,
    TapirVersionError,
)
from .flagswitch import MaterialFlagSwitch, reexport_including, restore_pending
from .ifc_reader import (
    DEFAULT_EXCLUDED_TYPES,
    Element,
    IfcModel,
    Skipped,
    SurfaceStyle,
    read_ifc,
)
from .materials import MaterialMap
from .tapir import (
    DEFAULT_SCOPE,
    DEFAULT_TRANSLATOR,
    PARTIAL_SCOPES,
    ArchicadInfo,
    PortStatus,
    TapirClient,
    discovery_error,
    find_instances,
    inspect_port,
    scan_ports,
)
from .uv import box_projection_uvs

__version__ = "0.1.1"

__all__ = [
    "DEFAULT_EXCLUDED_TYPES",
    "DEFAULT_SCOPE",
    "DEFAULT_TRANSLATOR",
    "PARTIAL_SCOPES",
    "UNEXPECTED_ANSWER",
    "AcSyncError",
    "ArchicadInfo",
    "Change",
    "CommandNotAvailableError",
    "Completeness",
    "Diff",
    "Element",
    "EndSurfaceResult",
    "ExcludedMaterial",
    "IfcModel",
    "IfcReadError",
    "MaterialFlagSwitch",
    "MaterialMap",
    "MissingElement",
    "ModalDialogOpenError",
    "PortStatus",
    "SceneRecord",
    "Skipped",
    "SurfaceStyle",
    "TapirClient",
    "TapirCommandError",
    "TapirConnectionError",
    "TapirNotInstalledError",
    "TapirVersionError",
    "apply_inherited_end_surfaces",
    "box_projection_uvs",
    "check_completeness",
    "compute_diff",
    "discovery_error",
    "find_instances",
    "inspect_port",
    "read_ifc",
    "reexport_including",
    "restore_pending",
    "scan_ports",
]
