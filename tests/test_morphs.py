import pytest
from acsync import (
    MorphApi,
    MorphFace,
    MorphGeometry,
    TapirClient,
    TapirCommandError,
    face_size,
    morph_body,
    parse_geometry,
    planar_deviation,
    polygon_normal,
)

GREY = "AA8293BC-6153-4A9E-8CBF-795E3887E333"
RED = "81A72F11-50C3-4BE8-A512-4D6B9043314D"

# What Tapir 1.8.0 answered for an L-shaped prism whose top face has a square hole.
BODY = {
    "vertices": [{"x": 12, "y": 0, "z": 0}, {"x": 16, "y": 0, "z": 0}, {"x": 16, "y": 2, "z": 0},
                 {"x": 12, "y": 2, "z": 0}, {"x": 13, "y": 1, "z": 0}, {"x": 13, "y": 1.5, "z": 0},
                 {"x": 14, "y": 1.5, "z": 0}],
    "polygons": [
        {"vertexIds": [0, 1, 2, 3], "holes": [{"vertexIds": [4, 5, 6]}], "surfaceId": {"guid": GREY.lower()}},
        {"vertexIds": [0, 1, 2]},
    ],
}


def geometry():
    return parse_geometry({"bodies": [BODY]})


def test_parse_geometry_reads_faces_holes_and_surfaces():
    g = geometry()
    assert len(g.vertices) == 7 and g.vertices[1] == (16, 0, 0)
    assert g.faces[0].loop == [0, 1, 2, 3]
    assert g.faces[0].holes == [[4, 5, 6]]
    assert g.faces[0].surface == GREY  # upper-cased, so GUIDs compare no matter the source
    assert g.faces[1].surface == ""
    assert g.surface_guids() == {GREY}


def test_several_bodies_are_merged_with_shifted_indices():
    g = parse_geometry({"bodies": [BODY, BODY]})
    assert len(g.vertices) == 14
    assert g.faces[2].loop == [7, 8, 9, 10] and g.faces[2].holes == [[11, 12, 13]]


def test_morph_body_is_relative_to_the_origin_and_keeps_holes_and_surfaces():
    body = morph_body(geometry(), (12.0, 0.0, 0.0))
    assert body["vertices"][0] == {"x": 0.0, "y": 0.0, "z": 0.0}
    assert body["vertices"][1] == {"x": 4.0, "y": 0.0, "z": 0.0}
    assert body["polygons"][0]["holes"] == [{"vertexIds": [4, 5, 6]}]
    assert body["polygons"][0]["surfaceId"] == {"guid": GREY}
    assert "surfaceId" not in body["polygons"][1] and "holes" not in body["polygons"][1]


def test_fingerprint_changes_with_geometry_and_surface_only():
    a = geometry()
    assert a.fingerprint() == geometry().fingerprint()
    moved = geometry()
    moved.vertices[0] = (12.0, 0.0, 0.001)
    assert moved.fingerprint() != a.fingerprint()
    painted = geometry()
    painted.faces[1].surface = RED
    assert painted.fingerprint() != a.fingerprint()
    noise = geometry()
    noise.vertices[0] = (12.0000000001, -0.0, 0.0)  # below a micrometre, and a negative zero
    assert noise.fingerprint() == a.fingerprint()


def test_edges_on_more_than_two_faces_are_counted():
    shared = MorphGeometry(
        [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 1), (-1, -1, 1)],
        [MorphFace([0, 1, 2]), MorphFace([1, 0, 3]), MorphFace([0, 1, 4])],
    )
    assert shared.overshared_edges() == 1 and not shared.is_closed()
    assert geometry().overshared_edges() == 0


def test_polygon_normal_follows_the_winding():
    square = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
    assert polygon_normal(square) == (0.0, 0.0, 1.0)
    assert polygon_normal(square[::-1]) == (0.0, 0.0, -1.0)
    assert polygon_normal([(0, 0, 0), (1, 0, 0), (2, 0, 0)]) == (0.0, 0.0, 0.0)


def test_face_size_is_the_longest_corner_distance():
    assert face_size([(0, 0, 0), (3, 0, 0), (3, 4, 0), (0, 4, 0)]) == 5.0
    ring = [(i, 0, 0) for i in range(20)]  # many corners: the box diagonal
    assert face_size(ring) == 19.0


def test_the_wait_for_archicad_grows_with_the_mesh():
    from acsync.morphs import request_timeout

    assert request_timeout(0) >= 30
    assert request_timeout(50_000) >= 75  # 50,000 faces took ~10 s to build; plenty of room
    assert request_timeout(100_000) > request_timeout(50_000)


def test_planar_deviation():
    assert planar_deviation([(0, 0, 0), (1, 0, 0), (1, 1, 5)]) == 0.0  # a triangle is always flat
    assert planar_deviation([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]) == 0.0
    warped = planar_deviation([(0, 0, 0), (1, 0, 0), (1, 1, 0.4), (0, 1, 0)])
    assert 0.09 < warped < 0.11
    assert planar_deviation([(0, 0, 0), (1, 0, 0), (2, 0, 0), (3, 0, 0)]) == 0.0  # no area


@pytest.fixture()
def api(fake_archicad):
    return MorphApi(TapirClient(fake_archicad.port, timeout=5))


def test_reading_asks_tapir_the_right_questions(fake_archicad, api):
    h = fake_archicad.handlers
    h["GetElementsByType"] = lambda p: fake_archicad.ok(
        {"elements": [{"elementId": {"guid": "aa"}}, {"elementId": {"guid": "bb"}}]}
    )
    h["Get3DGeometryOfElements"] = lambda p: fake_archicad.ok(
        {"geometries3D": [{"bodies": [BODY]} for _ in p["elements"]]}
    )
    h["GetDetailsOfElements"] = lambda p: fake_archicad.ok(
        {"detailsOfElements": [{"id": "MORPH-001", "details": {"origin": {"x": 1, "y": 2, "z": 3}}}]}
    )
    assert api.list_guids() == ["AA", "BB"]
    found = api.read(["aa", "bb"])
    assert set(found) == {"AA", "BB"} and len(found["AA"].faces) == 2
    assert api.details(["aa"])["AA"].origin == (1, 2, 3)
    assert api.details(["aa"])["AA"].element_id == "MORPH-001"
    calls = dict(fake_archicad.calls)
    assert calls["GetElementsByType"] == {"elementType": "Morph"}
    assert calls["Get3DGeometryOfElements"] == {
        "elements": [{"elementId": {"guid": "aa"}}, {"elementId": {"guid": "bb"}}]
    }


def test_selected_morphs_are_the_morphs_among_the_selection(fake_archicad, api):
    h = fake_archicad.handlers
    h["GetElementsByType"] = lambda p: fake_archicad.ok(
        {"elements": [{"elementId": {"guid": "AA"}}, {"elementId": {"guid": "BB"}}]}
    )
    h["GetSelectedElements"] = lambda p: fake_archicad.ok(
        {"elements": [{"elementId": {"guid": "bb"}}, {"elementId": {"guid": "wall"}}]}
    )
    assert api.selected_morphs() == ["BB"]


def test_a_short_answer_is_an_error_not_silent_data_loss(fake_archicad, api):
    fake_archicad.handlers["Get3DGeometryOfElements"] = lambda p: fake_archicad.ok(
        {"geometries3D": []}
    )
    with pytest.raises(TapirCommandError, match="asked for 1"):
        api.read(["aa"])


def test_surface_lookup(fake_archicad, api):
    h = fake_archicad.handlers
    h["GetAttributesByType"] = lambda p: fake_archicad.ok(
        {"attributes": [{"attributeId": {"guid": GREY.lower()}, "index": 99, "name": "Paint - Light Gray"}]}
    )
    h["GetSurfaces"] = lambda p: fake_archicad.ok(
        {"surfaces": [{"name": "Paint - Light Gray", "transparency": 25,
                       "surfaceColor": {"red": 0.5, "green": 0.25, "blue": 1.0}}]}
    )
    assert api.surface_names() == {GREY: "Paint - Light Gray"}
    info = api.surface_details([GREY, GREY.lower()])[GREY]
    assert info.name == "Paint - Light Gray" and info.color == (0.5, 0.25, 1.0)
    assert info.transparency == 0.25  # Archicad counts in percent
    assert dict(fake_archicad.calls)["GetAttributesByType"] == {"attributeType": "Surface"}


def test_create_sends_identity_axes_and_local_vertices(fake_archicad, api):
    fake_archicad.handlers["CreateMorphs"] = lambda p: fake_archicad.ok(
        {"elements": [{"elementId": {"guid": "new"}}]}
    )
    assert api.create([(geometry(), (12.0, 0.0, 0.0))]) == ["NEW"]
    sent = dict(fake_archicad.calls)["CreateMorphs"]["morphsData"][0]
    assert sent["basePoint"] == {"x": 12.0, "y": 0.0, "z": 0.0}
    assert sent["xAxis"] == {"x": 1.0, "y": 0.0, "z": 0.0} and sent["zAxis"]["z"] == 1.0
    assert sent["body"]["vertices"][1] == {"x": 4.0, "y": 0.0, "z": 0.0}


def test_create_reports_a_refused_item_and_keeps_the_others(fake_archicad, api):
    # Archicad answers a refused morph with an error entry in place of its element id
    fake_archicad.handlers["CreateMorphs"] = lambda p: fake_archicad.ok(
        {"elements": [{"elementId": {"guid": "ok"}},
                      {"error": {"code": -1, "message": "needs 4 faces"}},
                      {"elementId": {"guid": "ok2"}}]}
    )
    item = (geometry(), (0.0, 0.0, 0.0))
    results = api.create([item, item, item])
    assert results[0] == "OK" and results[2] == "OK2"
    assert isinstance(results[1], TapirCommandError) and "needs 4 faces" in str(results[1])
    assert api.create([]) == []


def test_create_retries_one_by_one_when_the_whole_request_fails(fake_archicad, api):
    def create(p):
        if len(p["morphsData"]) > 1:
            return fake_archicad.error(5000, "bad request")
        return fake_archicad.ok({"elements": [{"elementId": {"guid": "ok"}}]})

    fake_archicad.handlers["CreateMorphs"] = create
    item = (geometry(), (0.0, 0.0, 0.0))
    assert api.create([item, item]) == ["OK", "OK"]


def test_open_meshes_are_declared_as_surfaces():
    cube = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
    faces = [(3, 2, 1, 0), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    closed = MorphGeometry(cube, [MorphFace(list(f)) for f in faces])
    assert closed.is_closed() and morph_body(closed, (0, 0, 0))["bodyType"] == "Solid"
    open_box = MorphGeometry(cube, [MorphFace(list(f)) for f in faces[:5]])
    assert not open_box.is_closed() and morph_body(open_box, (0, 0, 0))["bodyType"] == "Surface"
    quad = MorphGeometry(cube[:4], [MorphFace([0, 1, 2, 3])])
    assert not quad.is_closed()
    assert not MorphGeometry([], []).is_closed()


def test_modify_reports_each_failure_separately(fake_archicad, api):
    fake_archicad.handlers["ModifyMorphs"] = lambda p: fake_archicad.ok(
        {"executionResults": [{"success": True}, {"success": False, "error": {"message": "nope"}}]}
    )
    results = api.modify([("a", geometry(), (0.0, 0.0, 0.0)), ("b", geometry(), (0.0, 0.0, 0.0))])
    assert results[0] is None and "nope" in str(results[1])
    sent = dict(fake_archicad.calls)["ModifyMorphs"]["morphsWithDetails"][0]
    assert sent["elementId"] == {"guid": "a"} and sent["xAxis"]["x"] == 1.0 and "body" in sent
