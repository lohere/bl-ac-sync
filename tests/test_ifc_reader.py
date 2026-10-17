import pytest
from ifc_fixture import DUP_ID, SLAB_GUID, WALL_GUID, WINDOW_GUID, build_synthetic_ifc

from acsync import IfcReadError, read_ifc


def test_elements_are_keyed_by_archicad_guid(synthetic_ifc):
    model = read_ifc(synthetic_ifc)
    assert set(model.elements) == {WALL_GUID, SLAB_GUID, WINDOW_GUID}
    wall = model.elements[WALL_GUID]
    assert wall.element_id == "WALL-0001"
    assert wall.ifc_type == "IfcWall"


def test_excluded_and_untagged_products_are_reported_not_read(synthetic_ifc):
    model = read_ifc(synthetic_ifc)
    reasons = {(s.element_id, s.reason) for s in model.skipped}
    assert ("ZONE-0001", "excluded type") in reasons
    assert ("WALL-9999", "no Archicad GUID (Tag)") in reasons
    assert "IfcSpace" in {s.ifc_type for s in model.skipped}


def test_excluded_types_are_configurable(synthetic_ifc):
    model = read_ifc(synthetic_ifc, excluded_types=["IfcWall"])
    assert WALL_GUID not in model.elements
    assert SLAB_GUID in model.elements


def test_units_are_metres(synthetic_ifc):
    # The fixture is written in millimetres; slab is 4000 x 3000 x 300 mm.
    slab = read_ifc(synthetic_ifc).elements[SLAB_GUID]
    xs, ys, zs = slab.vertices[0::3], slab.vertices[1::3], slab.vertices[2::3]
    assert max(xs) - min(xs) == pytest.approx(4.0)
    assert max(ys) - min(ys) == pytest.approx(3.0)
    assert max(zs) - min(zs) == pytest.approx(0.3)


def test_quads_are_preserved(synthetic_ifc):
    slab = read_ifc(synthetic_ifc).elements[SLAB_GUID]
    assert slab.face_count == 6
    assert set(slab.face_sizes) == {4}
    assert len(slab.face_indices) == sum(slab.face_sizes)
    assert max(slab.face_indices) < slab.vertex_count


def test_per_face_materials_resolve_to_archicad_surface_names(synthetic_ifc):
    wall = read_ifc(synthetic_ifc).elements[WALL_GUID]
    names = {s.name for s in wall.surfaces}
    assert names == {"Brick - Aged Light", "Stucco - White Fine"}
    assert len(wall.face_materials) == wall.face_count
    assert {wall.surfaces[m].name for m in wall.face_materials} == names


def test_surface_colour_and_transparency(synthetic_ifc):
    window = read_ifc(synthetic_ifc).elements[WINDOW_GUID]
    glass = window.surfaces[0]
    assert glass.name == "Glass - Clear Fast"
    assert glass.diffuse == pytest.approx((0.5, 0.6, 0.7), abs=1e-6)
    assert glass.transparency == pytest.approx(0.7)


def test_geometry_hash_is_stable_and_detects_changes(tmp_path):
    a = read_ifc(build_synthetic_ifc(tmp_path / "a.ifc")).elements[WALL_GUID]
    b = read_ifc(build_synthetic_ifc(tmp_path / "b.ifc")).elements[WALL_GUID]
    taller = read_ifc(build_synthetic_ifc(tmp_path / "c.ifc", wall_height_mm=3100)).elements[
        WALL_GUID
    ]
    assert a.geometry_hash == b.geometry_hash
    assert a.geometry_hash != taller.geometry_hash


def test_duplicate_element_ids_do_not_collide(tmp_path):
    other = "EEEEEEEE-0000-4000-8000-000000000005"
    path = build_synthetic_ifc(tmp_path / "dup.ifc", second_wall_guid=other)
    model = read_ifc(path)
    assert WALL_GUID in model.elements and other in model.elements
    # Same Element ID text on two different elements must never merge them.
    assert model.elements[other].element_id == DUP_ID


def test_missing_file_and_garbage_file(tmp_path):
    with pytest.raises(IfcReadError, match="not found"):
        read_ifc(tmp_path / "nope.ifc")
    bad = tmp_path / "bad.ifc"
    bad.write_text("this is not an ifc file")
    with pytest.raises(IfcReadError):
        read_ifc(bad)


def test_real_model(real_ifc):
    """Smoke test against a real Archicad 'IFC4 Reference View' export."""
    model = read_ifc(real_ifc)
    assert model.schema == "IFC4"
    assert len(model.elements) > 100
    assert not model.warnings
    for element in model.elements.values():
        assert element.face_count > 0
        assert len(element.face_materials) == element.face_count
        assert len(element.face_indices) == sum(element.face_sizes)
        assert max(element.face_indices) < element.vertex_count
        assert all(abs(v) < 5000 for v in element.vertices)  # metres, not millimetres
    walls = [e for e in model.elements.values() if e.ifc_type == "IfcWall"]
    assert walls
    surfaces = set(model.surface_names())
    assert not any(s.startswith("IfcSurfaceStyleRendering") for s in surfaces)


def test_terrain_ifcsite_is_imported_keyed_by_global_id(tmp_path):
    path = build_synthetic_ifc(tmp_path / "terrain.ifc", terrain=True)
    model = read_ifc(path)
    site_keys = [k for k, e in model.elements.items() if e.ifc_type == "IfcSite"]
    assert len(site_keys) == 1 and site_keys[0].startswith("ifc:")
    site = model.elements[site_keys[0]]
    assert site.element_id == "Site" and site.face_count == 6
    assert site.global_id == site_keys[0].removeprefix("ifc:")


def test_site_can_be_excluded(tmp_path):
    path = build_synthetic_ifc(tmp_path / "terrain.ifc", terrain=True)
    model = read_ifc(path, excluded_types=["IfcSite"])
    assert not [e for e in model.elements.values() if e.ifc_type == "IfcSite"]


def test_site_rotation_is_undone_by_default(tmp_path):
    plain = read_ifc(build_synthetic_ifc(tmp_path / "plain.ifc")).elements[SLAB_GUID]
    path = build_synthetic_ifc(tmp_path / "rot.ifc", site_rotation_deg=23.0)
    restored = read_ifc(path).elements[SLAB_GUID]
    rotated = read_ifc(path, archicad_coordinates=False).elements[SLAB_GUID]
    assert restored.vertices == pytest.approx(plain.vertices, abs=1e-6)
    assert rotated.vertices != pytest.approx(plain.vertices, abs=1e-3)
    assert restored.geometry_hash == plain.geometry_hash
    assert restored.geometry_hash != rotated.geometry_hash
