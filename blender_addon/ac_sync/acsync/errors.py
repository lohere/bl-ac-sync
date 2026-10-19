# SPDX-License-Identifier: GPL-3.0-or-later
"""Exception types raised by the acsync core."""


class AcSyncError(Exception):
    """Base class for all acsync errors."""


class TapirConnectionError(AcSyncError):
    """Archicad / Tapir could not be reached (not running, wrong port, timeout)."""


class TapirCommandError(AcSyncError):
    """Archicad or Tapir answered with an error."""

    def __init__(self, message: str, code: int | None = None, command: str | None = None):
        super().__init__(message)
        self.code = code
        self.command = command


class ModalDialogOpenError(TapirCommandError):
    """A modal dialog is open in Archicad; it blocks every command until it is closed."""


class CommandNotAvailableError(TapirCommandError):
    """The command does not exist in the installed Tapir version (Tapir is too old)."""


class TapirVersionError(AcSyncError):
    """The installed Tapir add-on is older than the version this tool needs."""


class IfcReadError(AcSyncError):
    """The IFC file could not be read or contained no usable geometry."""


class TapirNotInstalledError(AcSyncError):
    """Archicad answers, but the Tapir add-on is not installed or not loaded."""


# What a step that digs in Archicad's answers can run into: Archicad refusing, or an answer that
# is not shaped the way the code expects. Steps that must never stop a sync catch exactly this.
UNEXPECTED_ANSWER = (AcSyncError, KeyError, IndexError, TypeError)
