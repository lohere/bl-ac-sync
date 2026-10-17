import os

import pytest

from acsync import (
    CommandNotAvailableError,
    ModalDialogOpenError,
    TapirClient,
    TapirCommandError,
    TapirConnectionError,
    TapirVersionError,
    find_instances,
)
from acsync.tapir import parse_version


def client_for(server, **kw):
    return TapirClient(server.port, timeout=5, **kw)


def test_parse_version():
    assert parse_version("1.7.1") == (1, 7, 1)
    assert parse_version("1.10.0") > parse_version("1.7.1")
    assert parse_version("1.5.9") < (1, 7, 1)


def test_info(fake_archicad):
    info = client_for(fake_archicad).info()
    assert info.archicad_version == 29
    assert info.tapir_version == "1.7.1" and info.tapir_ok
    assert info.project_name == "test"
    assert info.port == fake_archicad.port


def test_old_tapir_is_rejected(fake_archicad):
    fake_archicad.handlers["GetAddOnVersion"] = lambda p: fake_archicad.ok({"version": "1.5.9"})
    with pytest.raises(TapirVersionError, match="1.5.9"):
        client_for(fake_archicad).require_tapir_version()


def test_modal_dialog_error(fake_archicad):
    fake_archicad.handlers["GetProjectInfo"] = lambda p: fake_archicad.error(
        4001, "Invalid program status (there is an open modal dialog: IFC Translators)"
    )
    with pytest.raises(ModalDialogOpenError):
        client_for(fake_archicad).info()


def test_missing_command_maps_to_command_not_available(fake_archicad):
    with pytest.raises(CommandNotAvailableError) as exc:
        client_for(fake_archicad).addon("DoesNotExist")
    assert exc.value.code == 4010


def test_addon_level_error_inside_response(fake_archicad):
    fake_archicad.handlers["GetProjectInfo"] = lambda p: fake_archicad.ok(
        {"error": {"code": 4001, "message": "dialog"}}
    )
    with pytest.raises(ModalDialogOpenError):
        client_for(fake_archicad).addon("GetProjectInfo")


def test_connection_refused_is_a_friendly_error():
    with pytest.raises(TapirConnectionError, match="Is Archicad running"):
        TapirClient(port=1, timeout=0.5).request("API.GetProductInfo")


def test_list_translators(fake_archicad):
    assert client_for(fake_archicad).list_ifc_translators() == [
        "IFC4 Reference View-based Export",
        "Exact Geometry Export",
    ]


def test_export_ifc_sends_expected_parameters(fake_archicad, tmp_path):
    target = tmp_path / "sub" / "out.ifc"

    def fake_save(params):
        with open(params["ifcFilePath"], "w") as fh:
            fh.write("ISO-10303-21;")
        return fake_archicad.ok({})

    fake_archicad.handlers["IFCFileOperation"] = fake_save
    result = client_for(fake_archicad).export_ifc(target)
    assert os.path.samefile(result, target)
    command, params = fake_archicad.calls[-1]
    assert command == "IFCFileOperation"
    assert params["method"] == "save"
    assert params["translatorName"] == "IFC4 Reference View-based Export"
    assert params["elementsToExport"] == "VisibleElementsOnAllStories"
    assert params["fileType"] == "ifc"


def test_export_ifc_removes_stale_file_and_detects_missing_output(fake_archicad, tmp_path):
    target = tmp_path / "out.ifc"
    target.write_text("stale")
    fake_archicad.handlers["IFCFileOperation"] = lambda p: fake_archicad.ok({})  # writes nothing
    with pytest.raises(TapirCommandError, match="wrote no file"):
        client_for(fake_archicad).export_ifc(target)
    assert not target.exists()


def test_export_ifc_unknown_translator_lists_alternatives(fake_archicad, tmp_path):
    with pytest.raises(TapirCommandError, match="Exact Geometry Export"):
        client_for(fake_archicad).export_ifc(tmp_path / "x.ifc", translator="Nope")
    assert not [c for c in fake_archicad.calls if c[0] == "IFCFileOperation"]


def test_export_ifc_rejects_bad_scope(fake_archicad, tmp_path):
    with pytest.raises(ValueError):
        client_for(fake_archicad).export_ifc(tmp_path / "x.ifc", scope="Everything")


def test_find_instances(fake_archicad):
    found = find_instances(ports=[1, fake_archicad.port], timeout=0.5)
    assert [i.port for i in found] == [fake_archicad.port]


@pytest.mark.skipif(not os.environ.get("ACSYNC_LIVE_ARCHICAD"), reason="needs a running Archicad")
def test_live_archicad():
    instances = find_instances()
    assert instances, "no Archicad with Tapir found"
    client = TapirClient(instances[0].port)
    assert client.require_tapir_version().tapir_ok
    assert "IFC4 Reference View-based Export" in client.list_ifc_translators()
