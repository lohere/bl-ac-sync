import math

import pytest
from acsync import box_projection_uvs, read_ifc
from ifc_fixture import SLAB_GUID, WALL_GUID, build_synthetic_ifc


def flat(points):
    return tuple(c for p in points for c in p)


def uvs_of(points, faces, scale=1.0):
    sizes = tuple(len(f) for f in faces)
    indices = tuple(i for f in faces for i in f)
    return box_projection_uvs(flat(points), sizes, indices, scale)


def corner_uvs(uvs, face_start, size):
    return [(uvs[2 * (face_start + k)], uvs[2 * (face_start + k) + 1]) for k in range(size)]


def test_wall_facing_plus_x_runs_along_y_and_up():
    # A wall face at x = 5, seen from +x: counter-clockwise from outside is y up the right.
    pts = [(5, 0, 0), (5, 4, 0), (5, 4, 3), (5, 0, 3)]
    (a, b, c, d) = corner_uvs(uvs_of(pts, [(0, 1, 2, 3)]), 0, 4)
    assert flat((a, b, c, d)) == pytest.approx(flat(((0, 0), (4, 0), (4, 3), (0, 3))))


def test_wall_facing_minus_y_runs_along_x():
    # A wall face at y = 0 whose outside is -y: viewer sees +x to the right.
    pts = [(0, 0, 0), (4, 0, 0), (4, 0, 3), (0, 0, 3)]
    (a, b, c, d) = corner_uvs(uvs_of(pts, [(0, 1, 2, 3)]), 0, 4)
    assert flat((a, b, c, d)) == pytest.approx(flat(((0, 0), (4, 0), (4, 3), (0, 3))))


def test_opposite_faces_are_not_mirrored_for_their_viewers():
    front = uvs_of([(0, 0, 0), (4, 0, 0), (4, 0, 3), (0, 0, 3)], [(0, 1, 2, 3)])
    back = uvs_of([(4, 1, 0), (0, 1, 0), (0, 1, 3), (4, 1, 3)], [(0, 1, 2, 3)])
    # Both are listed counter-clockwise as seen from outside, so both run 0 -> 4 in u.
    assert front[2] - front[0] == pytest.approx(4.0)
    assert back[2] - back[0] == pytest.approx(4.0)


def test_scale_is_metres_per_uv_unit():
    pts = [(0, 0, 0), (4, 0, 0), (4, 0, 3), (0, 0, 3)]
    uvs = uvs_of(pts, [(0, 1, 2, 3)], scale=2.0)
    assert uvs[2] == pytest.approx(2.0) and uvs[5] == pytest.approx(1.5)
    with pytest.raises(ValueError):
        uvs_of(pts, [(0, 1, 2, 3)], scale=0)


def test_floor_uses_world_x_and_y():
    pts = [(1, 2, 0), (5, 2, 0), (5, 6, 0), (1, 6, 0)]  # counter-clockwise from above: +z normal
    uvs = uvs_of(pts, [(0, 1, 2, 3)])
    assert flat(corner_uvs(uvs, 0, 4)) == pytest.approx(flat([(1, 2), (5, 2), (5, 6), (1, 6)]))


def test_angled_wall_keeps_its_real_dimensions():
    ang = math.radians(30)
    ex, ey = 4 * math.cos(ang), 4 * math.sin(ang)
    pts = [(0, 0, 0), (ex, ey, 0), (ex, ey, 3), (0, 0, 3)]
    (a, b, c, _d) = corner_uvs(uvs_of(pts, [(0, 1, 2, 3)]), 0, 4)
    assert math.hypot(b[0] - a[0], b[1] - a[1]) == pytest.approx(4.0)  # length along the wall
    assert c[1] - b[1] == pytest.approx(3.0)  # height
    assert b[1] == pytest.approx(a[1])  # no shear: the bottom edge is horizontal in UV space


def test_sloped_roof_is_not_stretched():
    ang = math.radians(30)
    run, rise = 4 * math.cos(ang), 4 * math.sin(ang)  # 4 m along the slope
    pts = [(0, 0, 0), (6, 0, 0), (6, run, rise), (0, run, rise)]
    (a, b, c, _d) = corner_uvs(uvs_of(pts, [(0, 1, 2, 3)]), 0, 4)
    assert b[0] - a[0] == pytest.approx(6.0)
    assert abs(math.hypot(c[0] - b[0], c[1] - b[1])) == pytest.approx(4.0)


def test_texture_continues_across_coplanar_faces():
    pts = [(0, 0, 0), (2, 0, 0), (4, 0, 0), (4, 0, 3), (2, 0, 3), (0, 0, 3)]
    faces = [(0, 1, 4, 5), (1, 2, 3, 4)]
    uvs = uvs_of(pts, faces)
    first = dict(zip((0, 1, 4, 5), corner_uvs(uvs, 0, 4), strict=True))
    second = dict(zip((1, 2, 3, 4), corner_uvs(uvs, 4, 4), strict=True))
    for shared in (1, 4):
        assert first[shared] == pytest.approx(second[shared])  # same position, same UV


def test_degenerate_face_gets_zero_uvs_and_length_matches_corners():
    pts = [(0, 0, 0), (1, 0, 0), (2, 0, 0), (0, 0, 0), (1, 0, 1), (1, 1, 1)]
    uvs = uvs_of(pts, [(0, 1, 2), (3, 4, 5)])
    assert len(uvs) == 2 * 6
    assert uvs[:6] == (0.0,) * 6  # collinear points: no normal


def test_read_ifc_generates_uvs_only_when_asked(tmp_path):
    path = build_synthetic_ifc(tmp_path / "uv.ifc")
    plain = read_ifc(path)
    with_uv = read_ifc(path, uv_scale=1.0)
    assert plain.uv_scale is None and plain.elements[WALL_GUID].uvs == ()
    assert with_uv.uv_scale == 1.0
    for guid in (WALL_GUID, SLAB_GUID):
        element = with_uv.elements[guid]
        assert len(element.uvs) == 2 * len(element.face_indices)
        assert element.geometry_hash == plain.elements[guid].geometry_hash  # UVs are not geometry
