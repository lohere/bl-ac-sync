from acsync import Diff, Element, SceneRecord, compute_diff


def element(guid, element_id="WALL-0001", geometry_hash="h1"):
    return Element(
        guid=guid, element_id=element_id, global_id="g", ifc_type="IfcWall",
        vertices=(0.0, 0.0, 0.0), face_sizes=(), face_indices=(), face_materials=(),
        surfaces=(), geometry_hash=geometry_hash,
    )


def test_empty_scene_means_everything_is_added():
    diff = compute_diff({"a": element("a"), "b": element("b")}, {})
    assert len(diff.added) == 2 and not diff.changed and not diff.removed


def test_unchanged_changed_removed():
    elements = {
        "same": element("same", geometry_hash="x"),
        "moved": element("moved", geometry_hash="new"),
        "new": element("new"),
    }
    scene = {
        "same": SceneRecord("same", "WALL-0001", "x"),
        "moved": SceneRecord("moved", "WALL-0001", "old"),
        "gone": SceneRecord("gone", "WALL-0002", "y"),
    }
    diff = compute_diff(elements, scene)
    assert [e.guid for e in diff.added] == ["new"]
    assert [e.guid for e in diff.unchanged] == ["same"]
    assert [c.element.guid for c in diff.geometry_changed] == ["moved"]
    assert [r.guid for r in diff.removed] == ["gone"]


def test_relabel_only_is_not_a_geometry_change():
    elements = {"a": element("a", element_id="WALL-0100", geometry_hash="x")}
    scene = {"a": SceneRecord("a", "WALL-0001", "x")}
    diff = compute_diff(elements, scene)
    assert len(diff.changed) == 1
    assert diff.changed[0].label_changed and not diff.changed[0].geometry_changed
    assert not diff.geometry_changed


def test_duplicate_element_ids_with_distinct_guids_stay_separate():
    elements = {"a": element("a", "WALL-0005", "h"), "b": element("b", "WALL-0005", "h")}
    scene = {"a": SceneRecord("a", "WALL-0005", "h"), "b": SceneRecord("b", "WALL-0005", "h")}
    diff = compute_diff(elements, scene)
    assert len(diff.unchanged) == 2 and not diff.added and not diff.removed


def test_summary_text():
    assert isinstance(compute_diff({}, {}), Diff)
    assert compute_diff({}, {}).summary().startswith("0 added")
