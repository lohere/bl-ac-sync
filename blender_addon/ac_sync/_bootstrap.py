"""Make the ``acsync`` core importable.

Installed extension: the core is vendored in ``_vendor/`` next to this file.
Development from a git checkout: fall back to ``<repo>/src``. For headless testing, extra
``sys.path`` entries (e.g. an unpacked ifcopenshell wheel) can be given with the
``ACSYNC_EXTRA_PATHS`` environment variable (os.pathsep separated).
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def ensure_core_importable():
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
