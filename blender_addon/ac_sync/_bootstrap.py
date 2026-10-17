"""Make the ``acsync`` core importable.

Installed extension: the core is vendored in ``_vendor/`` next to this file.
Development from a git checkout: fall back to ``<repo>/src``. For headless testing, extra
``sys.path`` entries (e.g. an unpacked ifcopenshell wheel) can be given with the
``ACSYNC_EXTRA_PATHS`` environment variable (os.pathsep separated).
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def purge_stale_core(vendor_dir=None):
    """Forget a previously imported ``acsync`` that came from this extension's ``_vendor``.

    Blender keeps imported modules for the whole session. After a reinstall or update the new
    add-on code is loaded, but ``sys.modules`` would still hold the *old* vendored core, giving
    errors like "cannot import name ...". Only a core loaded from our own ``_vendor`` folder is
    dropped, so a development checkout on ``sys.path`` is left alone.
    """
    vendor = os.path.abspath(vendor_dir or os.path.join(_HERE, "_vendor"))
    loaded = sys.modules.get("acsync")
    path = os.path.abspath(getattr(loaded, "__file__", None) or "")
    if loaded is None or not path.startswith(vendor + os.sep):
        return False
    for name in [n for n in sys.modules if n == "acsync" or n.startswith("acsync.")]:
        del sys.modules[name]
    return True


def ensure_core_importable():
    purge_stale_core()
    for extra in filter(None, os.environ.get("ACSYNC_EXTRA_PATHS", "").split(os.pathsep)):
        if extra not in sys.path:
            sys.path.insert(0, extra)
    try:
        import acsync

        return
    except ImportError:
        pass
    candidates = [
        os.path.join(_HERE, "_vendor"),
        os.path.abspath(os.path.join(_HERE, "..", "..", "src")),
    ]
    for path in candidates:
        if os.path.isdir(os.path.join(path, "acsync")) and path not in sys.path:
            sys.path.insert(0, path)
            break
    import acsync  # noqa: F401  (raises a clear ImportError if still missing)
