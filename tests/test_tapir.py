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


def test_find_instances_scans_all_ports_and_keeps_order(fake_archicad):
    from conftest import FakeArchicad

    second = FakeArchicad()
    try:
        second.handlers.update(fake_archicad.handlers)
        ports = sorted([fake_archicad.port, second.port])
        found = find_instances(ports=[1, *reversed(ports), 2], timeout=0.5)
        assert [i.port for i in found] == list(reversed(ports))  # order of the ports given
    finally:
        second.close()


def test_probe_instance(fake_archicad):
    from acsync.tapir import probe_instance

    assert probe_instance(fake_archicad.port).project_name == "test"
    assert probe_instance(1, timeout=0.3) is None


@pytest.mark.skipif(not os.environ.get("ACSYNC_LIVE_ARCHICAD"), reason="needs a running Archicad")
def test_live_archicad():
    instances = find_instances()
    assert instances, "no Archicad with Tapir found"
    client = TapirClient(instances[0].port)
    assert client.require_tapir_version().tapir_ok
    assert "IFC4 Reference View-based Export" in client.list_ifc_translators()


def test_select_elements_replaces_selection_and_reports_result(fake_archicad):
    state = {"selected": ["OLD1", "OLD2"], "calls": []}

    def get_selected(params):
        return fake_archicad.ok({"elements": [{"elementId": {"guid": g}} for g in state["selected"]]})

    def change(params):
        state["calls"].append(params)
        for item in params.get("removeElementsFromSelection") or []:
            state["selected"].remove(item["elementId"]["guid"])
        for item in params.get("addElementsToSelection") or []:
            state["selected"].append(item["elementId"]["guid"])
        return fake_archicad.ok({})

    fake_archicad.handlers["GetSelectedElements"] = get_selected
    fake_archicad.handlers["ChangeSelectionOfElements"] = change
    client = client_for(fake_archicad)
    assert client.select_elements(["a", "b", "a"]) == 2  # duplicates ignored
    assert state["selected"] == ["a", "b"]
    assert "removeElementsFromSelection" in state["calls"][0]
    assert client.select_elements(["c"], replace=False) == 1
    assert state["selected"] == ["a", "b", "c"]


# -- telling the failure cases apart ------------------------------------------------------------


def test_archicad_without_tapir_is_reported_as_missing_tapir(fake_archicad):
    from acsync import TapirNotInstalledError, discovery_error, inspect_port, scan_ports

    fake_archicad.handlers.pop("GetAddOnVersion")  # Archicad answers, Tapir is not loaded
    status = inspect_port(fake_archicad.port)
    assert status.state == "no_tapir" and status.info is None
    error = discovery_error(scan_ports(ports=[1, fake_archicad.port], timeout=0.4))
    assert isinstance(error, TapirNotInstalledError)
    text = str(error)
    assert "not installed" in text and "restart Archicad" in text and "1.7.1" in text
    assert str(fake_archicad.port) in text and "github.com" in text


def test_require_tapir_version_reports_missing_tapir_clearly(fake_archicad):
    from acsync import TapirNotInstalledError

    fake_archicad.handlers.pop("GetAddOnVersion")
    with pytest.raises(TapirNotInstalledError, match="not installed"):
        client_for(fake_archicad).require_tapir_version()


def test_open_dialog_is_reported_as_dialog(fake_archicad):
    from acsync import discovery_error, inspect_port, scan_ports

    fake_archicad.handlers["GetAddOnVersion"] = lambda p: fake_archicad.error(4001, "dialog")
    assert inspect_port(fake_archicad.port).state == "dialog_open"
    error = discovery_error(scan_ports(ports=[fake_archicad.port], timeout=0.4))
    assert isinstance(error, ModalDialogOpenError)


def test_nothing_running_says_no_archicad(fake_archicad):
    from acsync import TapirNotInstalledError, discovery_error, scan_ports

    statuses = scan_ports(ports=[1, 2], timeout=0.3)
    assert [s.state for s in statuses] == ["unreachable", "unreachable"]
    error = discovery_error(statuses)
    assert not isinstance(error, TapirNotInstalledError)
    assert "No Archicad found" in str(error)


def test_old_tapir_still_reports_version_problem(fake_archicad):
    from acsync import discovery_error, scan_ports

    fake_archicad.handlers["GetAddOnVersion"] = lambda p: fake_archicad.ok({"version": "1.5.9"})
    statuses = scan_ports(ports=[fake_archicad.port], timeout=0.4)
    assert statuses[0].state == "ok" and not statuses[0].info.tapir_ok  # found, but too old
    assert statuses[0].info.tapir_version == "1.5.9"
    assert discovery_error([]) is not None
