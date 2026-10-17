"""AC Sync: update Archicad geometry in Blender without losing your work."""

from . import _bootstrap

_bootstrap.add_extra_paths()
_bootstrap.purge_submodules(__name__)  # reinstall/reload in a running Blender: no stale code

from . import operators, settings, ui

_MODULES = (settings, operators, ui)


def register():
    for module in _MODULES:
        module.register()


def unregister():
    for module in reversed(_MODULES):
        module.unregister()
