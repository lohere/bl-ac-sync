"""AC Sync: update Archicad geometry in Blender without losing your work."""

from . import _bootstrap

_bootstrap.ensure_core_importable()

from . import operators, settings, ui

_MODULES = (settings, operators, ui)


def register():
    for module in _MODULES:
        module.register()


def unregister():
    for module in reversed(_MODULES):
        module.unregister()
