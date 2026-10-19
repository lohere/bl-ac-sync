"""Building materials excluded from collision detection: finding them (collision.py) and
switching them on for an export (flagswitch.py)."""

import copy
import os

import pytest
from acsync import (
    IfcModel,
    MaterialFlagSwitch,
    TapirClient,
    check_completeness,
    collision,
    flagswitch,
    restore_pending,
)
from acsync.errors import AcSyncError
from acsync.ifc_reader import Element

TALAJ, AIR, BETON = "T-GUID", "A-GUID", "B-GUID"

MATERIALS = {
    TALAJ: {"attributeId": {"guid": TALAJ}, "index": 46, "name": "Talaj", "id": "G-1",
            "density": 1800.0, "connPriority": 100, "cutFillIndex": 7,
            "cutFillOrientation": "ProjectOrigin", "showUncutLines": True,
            "collisionDetection": False},
    AIR: {"attributeId": {"guid": AIR}, "index": 42, "name": "Air Space", "id": "EF-06",
          "density": 1.2, "connPriority": 560, "cutFillIndex": 16,
          "cutFillOrientation": "ProjectOrigin", "showUncutLines": True,
          "collisionDetection": False},
    BETON: {"attributeId": {"guid": BETON}, "index": 6, "name": "Beton", "id": "B-1",
            "density": 2400.0, "connPriority": 800, "cutFillIndex": 9,
            "cutFillOrientation": "ProjectOrigin", "showUncutLines": True,
            "collisionDetection": True},
}

# element -> Building Material of each component
COMPONENTS = {
    "W1": ["Beton", "Air Space"],  # composite wall with an air gap: exported, not missing
    "W2": ["Beton"],  # missing for some other reason
    "S1": ["Talaj"],  # missing because of the flag
    "M1": ["Talaj"],  # mesh, not in the IFC
    "M2": ["Talaj"],  # mesh that did arrive
    "M3": ["Beton"],
}
ELEMENTS = {"Wall": ["W1", "W2"], "Slab": ["S1"], "Mesh": ["M1", "M2", "M3"]}
OUTLINE = {
    "M1": [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)],
    "M2": [(100.0, 100.0), (110.0, 100.0), (110.0, 110.0), (100.0, 110.0)],
    "M3": [(200.0, 0.0), (210.0, 0.0), (210.0, 10.0)],
}


class FakeProject:
    """Archicad's answers for the commands used here; ``materials`` is mutable state."""

    def __init__(self, fake):
        self.fake = fake
        self.materials = copy.deepcopy(MATERIALS)
        self.accept_writes = True
        self.writes = []
        by_name = {m["name"]: g for g, m in self.materials.items()}
        self.by_name = by_name
        h = fake.handlers
        h["GetAttributesByType"] = lambda p: fake.ok(
            {"attributes": [{"attributeId": m["attributeId"], "index": m["index"],
                             "name": m["name"]} for m in self.materials.values()]}
        )
        h["GetBuildingMaterials"] = self.get_materials
        h["CreateBuildingMaterials"] = self.create_materials
        h["API.GetPropertyIds"] = lambda p: fake.ok(
            {"properties": [{"propertyId": {"guid": "P-MAT"}}]}, addon=False
        )
        h["API.GetComponentsOfElements"] = self.components
        h["API.GetPropertyValuesOfElementComponents"] = self.component_values
        h["GetElementsByType"] = lambda p: fake.ok(
            {"elements": [{"elementId": {"guid": g}} for g in ELEMENTS.get(p["elementType"], [])]}
        )
        h["GetDetailsOfElements"] = lambda p: fake.ok(
            {"detailsOfElements": [
                {"details": {"polygonCoordinates": [
                    {"x": x, "y": y, "z": 0} for x, y in OUTLINE[e["elementId"]["guid"]]]}}
                for e in p["elements"]
            ]}
        )

    def get_materials(self, params):
        wanted = [a["attributeId"]["guid"] for a in params["attributeIds"]]
        fields = params.get("fields")
        out = []
        for guid in wanted:
            m = copy.deepcopy(self.materials[guid])
            if fields:
                m = {k: v for k, v in m.items() if k in ("attributeId", "index", "name", *fields)}
            out.append(m)
        return self.fake.ok({"buildingMaterials": out})

    def create_materials(self, params):
        assert params["overwriteExisting"] is True
        for item in params["buildingMaterialDataArray"]:
            self.writes.append(copy.deepcopy(item))
            if not self.accept_writes:
                continue
            guid = item["attributeId"]["guid"]
            index = self.materials[guid]["index"]
            # like Archicad: whatever is left out of an overwrite is reset
            self.materials[guid] = {**item, "index": index}
        return self.fake.ok({"attributeIds": []})

    def components(self, params):
        out = []
        for e in params["elements"]:
            guid = e["elementId"]["guid"]
            out.append({"elementComponents": [
                {"elementComponentId": {"elementId": {"guid": guid},
                                        "componentId": {"guid": f"{guid}/{i}"}}}
                for i, _ in enumerate(COMPONENTS[guid])
            ]})
        return self.fake.ok({"componentsOfElements": out}, addon=False)

    def component_values(self, params):
        out = []
        for ec in params["elementComponents"]:
            ids = ec["elementComponentId"]
            guid, index = ids["elementId"]["guid"], int(ids["componentId"]["guid"].split("/")[1])
            out.append({"propertyValues": [
                {"propertyValue": {"type": "string", "status": "normal",
                                   "value": COMPONENTS[guid][index]}}]})
        return self.fake.ok({"propertyValuesForElementComponents": out}, addon=False)

    def flag(self, name):
        return self.materials[self.by_name[name]]["collisionDetection"]


def site_model(*outlines):
    """An IFC model whose Site contains the given outlines (plan points)."""
    vertices = []
    for outline in outlines:
        for x, y in outline:
            vertices += [x, y, 0.0]
    site = Element(
        guid="ifc:SITE", element_id="Site", global_id="g", ifc_type="IfcSite",
        vertices=tuple(vertices), face_sizes=(), face_indices=(), face_materials=(),
        surfaces=(), geometry_hash="h",
    )
    return IfcModel(path="x", schema="IFC4", elements={"ifc:SITE": site})


@pytest.fixture()
def project(fake_archicad, tmp_path, monkeypatch):
    monkeypatch.setattr(flagswitch, "recovery_path", lambda: str(tmp_path / "pending.json"))
    proj = FakeProject(fake_archicad)
    proj.client = TapirClient(fake_archicad.port, timeout=5)
    proj.recovery = tmp_path / "pending.json"
    return proj


# -- finding ----------------------------------------------------------------------------------


def test_only_materials_that_explain_a_missing_element_are_named(project):
    found = collision.find_excluded(project.client, ["S1", "W1", "W2"])
    assert [(m.name, m.elements, m.meshes) for m in found.materials] == [("Talaj", 1, 0)]
    assert found.by_element == {"S1": ("Talaj",)}  # W1 has an air gap but also concrete


def test_nothing_to_find_costs_two_requests(project):
    for m in project.materials.values():
        m["collisionDetection"] = True
    found = collision.find_excluded(project.client, ["S1"])
    assert not found.materials
    assert len(project.fake.calls) == 2


def test_meshes_missing_from_the_site_are_found(project):
    model = site_model(OUTLINE["M2"])  # M1 (terrain with an excluded material) did not arrive
    found = collision.find_excluded(project.client, [], model, check_meshes=True)
    assert [(m.name, m.meshes) for m in found.materials] == [("Talaj", 1)]


def test_meshes_that_arrived_are_not_reported(project):
    model = site_model(OUTLINE["M1"], OUTLINE["M2"])
    found = collision.find_excluded(project.client, [], model, check_meshes=True)
    assert not found.materials


def test_meshes_are_skipped_unless_asked_for(project):
    found = collision.find_excluded(project.client, [], site_model(), check_meshes=False)
    assert not found.materials


def test_completeness_names_the_material(project):
    model = site_model(OUTLINE["M2"])
    model.elements["W1"] = model.elements["ifc:SITE"]  # W1 arrived; W2 and S1 did not
    result = check_completeness(project.client, model, "EntireProject", check_meshes=True)
    assert not result.ok
    by_guid = {m.guid: m for m in result.missing}
    assert by_guid["S1"].material == "Talaj" and by_guid["W2"].material == ""
    assert [(m.name, m.elements, m.meshes) for m in result.excluded_materials] == [("Talaj", 1, 1)]
    text = "\n".join(result.lines())
    assert "'Talaj' (1 element(s), 1 mesh(es))" in text and "Include excluded materials" in text
    assert "1 are not hidden by any Archicad filter" in text  # W2 only: S1 has its cause now
    assert "Air Space" not in text  # air gaps stay out of it
    assert "Talaj" in result.summary()


def test_a_failing_material_lookup_does_not_break_the_check(project):
    project.fake.handlers["API.GetComponentsOfElements"] = lambda p: project.fake.error(
        4001, "dialog"
    )
    result = check_completeness(project.client, site_model(), "EntireProject")
    assert result.checked and result.notes and "Building Materials" in result.notes[0]


# -- switching --------------------------------------------------------------------------------


def talaj_materials():
    return [collision.ExcludedMaterial("Talaj", TALAJ, 1)]


def test_flag_is_on_inside_and_everything_is_back_afterwards(project):
    original = copy.deepcopy(project.materials[TALAJ])
    with MaterialFlagSwitch(project.client, talaj_materials(), "p.pln") as switch:
        assert project.flag("Talaj") is True
        assert project.recovery.exists()
        assert project.flag("Air Space") is False  # not asked for: untouched
        assert project.materials[TALAJ]["density"] == 1800.0  # full settings were resent
    assert project.materials[TALAJ] == original
    assert not project.recovery.exists() and not switch.warnings
    assert all(w["name"] == "Talaj" for w in project.writes)


def test_flag_is_restored_when_the_block_fails(project):
    with pytest.raises(RuntimeError), MaterialFlagSwitch(project.client, talaj_materials()):
        raise RuntimeError("export failed")
    assert project.flag("Talaj") is False and not project.recovery.exists()


def test_a_material_that_is_already_on_is_left_alone(project):
    project.materials[TALAJ]["collisionDetection"] = True
    with MaterialFlagSwitch(project.client, talaj_materials()):
        pass
    assert not project.writes and not project.recovery.exists()


def test_if_archicad_ignores_the_change_the_switch_fails_cleanly(project):
    project.accept_writes = False
    with pytest.raises(AcSyncError), MaterialFlagSwitch(project.client, talaj_materials()):
        pass
    assert project.flag("Talaj") is False


def test_failed_restore_is_reported_and_recoverable(project):
    switch = MaterialFlagSwitch(project.client, talaj_materials(), "p.pln")
    with switch:
        project.accept_writes = False  # Archicad stops listening before the restore
        project.materials[TALAJ]["collisionDetection"] = True
    assert switch.warnings and "Talaj" in switch.warnings[0]
    assert project.recovery.exists()  # kept for the next run
    project.accept_writes = True
    messages = restore_pending(project.client, "p.pln")
    assert project.flag("Talaj") is False and not project.recovery.exists()
    assert "restored" in messages[0] and "Talaj" in messages[0]


def test_recovery_file_of_another_project_is_ignored(project):
    project.recovery.write_text('{"project_path": "other.pln", "materials": []}', encoding="utf-8")
    assert restore_pending(project.client, "p.pln") == []
    assert project.recovery.exists()
    assert os.path.exists(project.recovery)


def test_no_recovery_file_means_nothing_to_do(project):
    assert restore_pending(project.client, "p.pln") == []


# -- the whole second pass --------------------------------------------------------------------


def test_second_pass_runs_with_the_flag_on_and_leaves_it_off_afterwards(project):
    seen = {}

    def step(name):
        def run(*args):
            seen[name] = project.flag("Talaj")
            return name

        return run

    progress = []
    model, result, warnings = flagswitch.reexport_including(
        project.client, talaj_materials(), "p.pln",
        export=step("export"), read=step("read"), check=step("check"), progress=progress.append,
    )
    assert seen == {"export": True, "read": True, "check": True}
    assert (model, result, warnings) == ("read", "check", [])
    assert project.flag("Talaj") is False and project.flag("Air Space") is False
    assert len(progress) == 4 and "Talaj" in progress[0]


def test_second_pass_restores_when_the_export_fails(project):
    def broken_export():
        assert project.flag("Talaj") is True
        raise AcSyncError("Archicad closed a dialog")

    with pytest.raises(AcSyncError):
        flagswitch.reexport_including(
            project.client, talaj_materials(), "p.pln",
            export=broken_export, read=lambda: None, check=lambda m: None,
        )
    assert project.flag("Talaj") is False and not project.recovery.exists()
