"""Small helpers that run before the rest of the add-on is imported.

The core library is a plain sub-package of this extension (``ac_sync.acsync``), so nothing
needs to be put on ``sys.path`` (Blender's extension policy forbids that).
"""

import os
import sys


def add_extra_paths():
    """Development/testing only: ``ACSYNC_EXTRA_PATHS`` (os.pathsep separated) is added to
    ``sys.path``, e.g. to make an unpacked ifcopenshell wheel importable in headless Blender.
    Installed extensions get ifcopenshell from the wheel listed in the manifest instead."""
    for extra in filter(None, os.environ.get("ACSYNC_EXTRA_PATHS", "").split(os.pathsep)):
        if extra not in sys.path:
            sys.path.insert(0, extra)


def purge_submodules(package, keep=("_bootstrap",)):
    """Forget already imported sub-modules of ``package``.

    Blender keeps imported modules for the whole session. After a reinstall, update or reload
    the new ``__init__`` runs but ``sys.modules`` would still hold the *old* sub-modules, giving
    errors like "cannot import name ...". Dropping them forces a fresh import. Returns the
    number of modules dropped.
    """
    prefix = package + "."
    stale = [
        name for name in sys.modules
        if name.startswith(prefix) and name[len(prefix):].split(".")[0] not in keep
    ]
    for name in stale:
        del sys.modules[name]
    return len(stale)
