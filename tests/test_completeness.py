import pytest

from acsync import IfcModel, TapirClient, check_completeness
from acsync.ifc_reader import Element

ARCHICAD_GUIDS = {
    "Wall": ["W1", "W2", "W3"],
    "Door": ["D1"],
    "Object": ["O1"],
}
HIDDEN_LAYER = {"W3", "O1"}  # not visible by layer
LAYER_OF = {"W2": 98, "W3": 56, "D1": 33, "O1": 56}


def element(guid):
    return Element(
        guid=guid, element_id="E-" + guid, global_id="g", ifc_type="IfcWall", vertices=(0.0,) * 3,
        face_sizes=(), face_indices=(), face_materials=(), surfaces=(), geometry_hash="h",
    )


def install(fake):
    def by_type(params):
        guids = list(ARCHICAD_GUIDS.get(params["elementType"], []))
        for f in params.get("filters", []):
            if f == "IsVisibleByLayer":
                guids = [g for g in guids if g not in HIDDEN_LAYER]
        return fake.ok({"elements": [{"elementId": {"guid": g}} for g in guids]})

    fake.handlers["GetElementsByType"] = by_type
    fake.handlers["API.GetPropertyIds"] = lambda p: fake.ok(
        {"properties": [{"propertyId": {"guid": "P"}}]}, addon=False
    )
    fake.handlers["API.GetPropertyValuesOfElements"] = lambda p: fake.ok(
        {"propertyValuesForElements": [
            {"propertyValues": [{"propertyValue": {"value": "ID-" + e["elementId"]["guid"]}}]}
            for e in p["elements"]
        ]},
        addon=False,
    )
    fake.handlers["GetDetailsOfElements"] = lambda p: fake.ok(
        {"detailsOfElements": [
            {"layerIndex": LAYER_OF[e["elementId"]["guid"]]} for e in p["elements"]
        ]}
    )
    fake.handlers["GetAttributesByType"] = lambda p: fake.ok(
        {"attributes": [
            {"index": 98, "name": "S2 New"}, {"index": 56, "name": "X Hidden"},
            {"index": 33, "name": "S1 Existing"},
        ]}
    )


def run(fake, ifc_guids, scope="EntireProject"):
    install(fake)
    model = IfcModel(path="x", schema="IFC4", elements={g: element(g) for g in ifc_guids})
    return check_completeness(TapirClient(fake.port, timeout=5), model, scope)


def test_everything_arrived(fake_archicad):
    result = run(fake_archicad, ["W1", "W2", "W3", "D1", "O1"])
    assert result.ok and result.expected == 5
    assert "All 5" in result.summary()


def test_missing_elements_are_named_with_layer_and_reason(fake_archicad):
    result = run(fake_archicad, ["W1", "D1"])  # W2, W3, O1 missing
    assert not result.ok
    assert {m.guid for m in result.missing} == {"W2", "W3", "O1"}
    by_guid = {m.guid: m for m in result.missing}
    assert by_guid["W3"].reasons == ("on a hidden layer",)
    assert by_guid["W2"].reasons == ()  # visible but still missing -> unexplained
    assert by_guid["W2"].layer == "S2 New" and by_guid["W3"].layer == "X Hidden"
    assert by_guid["W2"].element_id == "ID-W2"
    assert result.missing_by_type == {"Wall": 2, "Object": 1}
    text = "\n".join(result.lines())
    assert "3 of 5" in text and "S2 New" in text and "Cause not known" in text


def test_guid_matching_ignores_case(fake_archicad):
    result = run(fake_archicad, ["w1", "w2", "w3", "d1", "o1"])
    assert result.ok


def test_guids_of_spatial_elements_are_not_compared(fake_archicad):
    result = run(fake_archicad, ["W1", "W2", "W3", "D1", "O1", "ifc:SITE"])
    assert result.ok


@pytest.mark.parametrize("scope", ["SelectedElementsOnly", "AllElementsOnCurrentStory"])
def test_check_is_skipped_for_partial_scopes(fake_archicad, scope):
    result = run(fake_archicad, ["W1"], scope=scope)
    assert not result.checked and "skipped" in result.lines()[0]


def test_archicad_errors_do_not_raise(fake_archicad):
    install(fake_archicad)
    fake_archicad.handlers["GetElementsByType"] = lambda p: fake_archicad.error(4001, "dialog")
    model = IfcModel(path="x", schema="IFC4", elements={})
    result = check_completeness(TapirClient(fake_archicad.port, timeout=5), model, "EntireProject")
    assert not result.checked and "failed" in result.note
