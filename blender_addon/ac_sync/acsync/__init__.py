"""AC-Blender Sync core: Archicad (Tapir) -> IFC -> mesh data, independent of Blender."""

from .completeness import Completeness, MissingElement, check_completeness
from .diff import Change, Diff, SceneRecord, compute_diff
from .errors import (
    AcSyncError,
    CommandNotAvailableError,
    IfcReadError,
    ModalDialogOpenError,
    TapirCommandError,
    TapirConnectionError,
    TapirNotInstalledError,
    TapirVersionError,
)
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
    ArchicadInfo,
    PortStatus,
    TapirClient,
    discovery_error,
    find_instances,
    inspect_port,
    scan_ports,
)
from .uv import box_projection_uvs

__version__ = "0.0.1"

__all__ = [
    "DEFAULT_EXCLUDED_TYPES",
    "DEFAULT_SCOPE",
    "DEFAULT_TRANSLATOR",
    "AcSyncError",
    "ArchicadInfo",
    "Change",
    "CommandNotAvailableError",
    "Completeness",
    "Diff",
    "Element",
    "IfcModel",
    "IfcReadError",
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
    "box_projection_uvs",
    "check_completeness",
    "compute_diff",
    "discovery_error",
    "find_instances",
    "inspect_port",
    "read_ifc",
    "scan_ports",
]
