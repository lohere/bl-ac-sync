import importlib.util
import os
import sys
import types


def load_bootstrap():
    path = os.path.join(os.path.dirname(__file__), "..", "blender_addon", "ac_sync", "_bootstrap.py")
    spec = importlib.util.spec_from_file_location("ac_sync_bootstrap_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_modules(monkeypatch, names):
    for name in names:
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))


def test_purge_drops_stale_submodules_and_keeps_the_rest(monkeypatch):
    bootstrap = load_bootstrap()
    pkg = "bl_ext.test.ac_sync"
    fake_modules(monkeypatch, [
        pkg, f"{pkg}._bootstrap", f"{pkg}.operators", f"{pkg}.acsync",
        f"{pkg}.acsync.tapir", "bl_ext.test.other_ext.operators", "acsync",
    ])
    dropped = bootstrap.purge_submodules(pkg)
    assert dropped == 3
    assert pkg in sys.modules and f"{pkg}._bootstrap" in sys.modules
    assert f"{pkg}.operators" not in sys.modules
    assert f"{pkg}.acsync" not in sys.modules and f"{pkg}.acsync.tapir" not in sys.modules
    assert "bl_ext.test.other_ext.operators" in sys.modules and "acsync" in sys.modules


def test_purge_is_a_noop_on_first_import(monkeypatch):
    bootstrap = load_bootstrap()
    fake_modules(monkeypatch, ["bl_ext.fresh.ac_sync"])
    assert bootstrap.purge_submodules("bl_ext.fresh.ac_sync") == 0


def test_extra_paths(monkeypatch, tmp_path):
    bootstrap = load_bootstrap()
    monkeypatch.setenv("ACSYNC_EXTRA_PATHS", os.pathsep.join([str(tmp_path), ""]))
    monkeypatch.setattr(sys, "path", list(sys.path))
    bootstrap.add_extra_paths()
    assert sys.path[0] == str(tmp_path)
