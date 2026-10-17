import importlib.util
import os
import sys


def load_bootstrap():
    path = os.path.join(os.path.dirname(__file__), "..", "blender_addon", "ac_sync", "_bootstrap.py")
    spec = importlib.util.spec_from_file_location("ac_sync_bootstrap_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_core(root, marker):
    package = root / "acsync"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(f"MARKER = {marker!r}\n")
    (package / "sub.py").write_text("X = 1\n")
    return str(root)


def forget_acsync(monkeypatch=None):
    for name in [n for n in sys.modules if n == "acsync" or n.startswith("acsync.")]:
        if monkeypatch is not None:
            monkeypatch.delitem(sys.modules, name)
        else:
            del sys.modules[name]


def test_stale_vendored_core_is_dropped_and_reimported_fresh(tmp_path, monkeypatch):
    bootstrap = load_bootstrap()
    vendor = tmp_path / "ext" / "_vendor"
    make_core(vendor, "old")
    monkeypatch.syspath_prepend(str(vendor))
    forget_acsync(monkeypatch)
    import acsync.sub

    import acsync  # the old vendored core, as loaded earlier in a Blender session

    assert acsync.MARKER == "old"

    (vendor / "acsync" / "__init__.py").write_text("MARKER = 'new'\n")  # the reinstall
    assert bootstrap.purge_stale_core(str(vendor)) is True
    assert "acsync" not in sys.modules and "acsync.sub" not in sys.modules
    import acsync as fresh

    assert fresh.MARKER == "new"
    forget_acsync()


def test_core_from_elsewhere_is_left_alone(tmp_path, monkeypatch):
    bootstrap = load_bootstrap()
    elsewhere = make_core(tmp_path / "checkout" / "src", "dev")
    monkeypatch.syspath_prepend(elsewhere)
    forget_acsync(monkeypatch)
    import acsync

    assert bootstrap.purge_stale_core(str(tmp_path / "ext" / "_vendor")) is False
    assert sys.modules["acsync"] is acsync
    forget_acsync()
