# SPDX-License-Identifier: GPL-3.0-or-later
"""bl-ac sync: update Archicad geometry in Blender without losing your work."""

from . import _bootstrap

_bootstrap.add_extra_paths()
_bootstrap.purge_submodules(__name__)  # reinstall/reload in a running Blender: no stale code

try:
    from ._build_info import VERSION as __version__  # written by tools/build_extension.py
except ImportError:  # running from a git checkout
    __version__ = "dev"

from . import operators, settings, ui

_MODULES = (settings, operators, ui)


def register():
    for module in _MODULES:
        module.register()


def unregister():
    for module in reversed(_MODULES):
        module.unregister()
