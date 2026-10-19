"""Walls that inherit the end surface of the adjoining wall."""

from acsync import IfcModel, TapirClient, apply_inherited_end_surfaces
from acsync.ifc_reader import Element, SurfaceStyle, geometry_hash

SURFACES = {n: SurfaceStyle(n) for n in ("Stucco", "Brick", "Insulation", "Paint", "Oak", "Concrete")}


def build(guid, quads):
    """An Element from [(four (x, y, z) corners, surface name)]."""
    vertices, sizes, indices, names, styles = [], [], [], [], []
    for corners, name in quads:
        start = len(vertices) // 3
        for corner in corners:
            vertices += list(corner)
        sizes.append(4)
        indices += [start, start + 1, start + 2, start + 3]
        names.append(name)
    for name in names:
        if SURFACES[name] not in styles:
            styles.append(SURFACES[name])
    materials = tuple(styles.index(SURFACES[n]) for n in names)
    return Element(
        guid=guid, element_id=guid, global_id="g", ifc_type="IfcWall", vertices=tuple(vertices),
        face_sizes=tuple(sizes), face_indices=tuple(indices), face_materials=materials,
        surfaces=tuple(styles),
        geometry_hash=geometry_hash(vertices, sizes, indices, names),
    )


def face_x(x, y0, y1, normal_sign):
    """A 3 m high face in the plane x = const, listed so that its normal is along +/-x."""
    ys = (y0, y1) if normal_sign > 0 else (y1, y0)
    return ((x, ys[0], 0.0), (x, ys[1], 0.0), (x, ys[1], 3.0), (x, ys[0], 3.0))


def face_y(y, x0, x1, normal_sign):
    xs = (x0, x1) if normal_sign < 0 else (x1, x0)
    return ((xs[0], y, 0.0), (xs[1], y, 0.0), (xs[1], y, 3.0), (xs[0], y, 3.0))


def wall_a(guid="A", end_x=5.0, extra=()):
    """Wall along x from 0 to ``end_x``, 0.5 thick: both ends show strips of the wall's own
    skins (listed so that the strips look along the wall, outward)."""
    return build(guid, [
        (face_x(end_x, -0.25, 0.0, +1), "Brick"),  # end at x = end_x
        (face_x(end_x, 0.0, 0.25, +1), "Insulation"),
        (face_x(0.0, -0.25, 0.0, -1), "Brick"),  # free end at x = 0
        (face_x(0.0, 0.0, 0.25, -1), "Insulation"),
        (face_y(0.25, 0.0, end_x, +1), "Stucco"),  # long sides
        (face_y(-0.25, 0.0, end_x, -1), "Stucco"),
        *extra,
    ])


def wall_b(guid="B"):
    """Wall along y from -3 to 3 at x = 5.25, 0.5 thick: stucco outside (x = 5.5) with a covered
    brick face 1 cm behind it (every skin is its own solid), paint on the inside (x = 5.0),
    which has half the area."""
    return build(guid, [
        (face_x(5.5, -3.0, 3.0, +1), "Stucco"),
        (face_x(5.49, -3.0, 3.0, +1), "Brick"),
        (face_x(5.0, -3.0, 0.0, -1), "Paint"),
    ])


def model_of(*elements):
    return IfcModel(path="x", schema="IFC4", elements={e.guid: e for e in elements})


def install(fake, flagged=("A", "B"), overridden=(), listed=None, a_end=5.0, floors=None):
    """Archicad's answers. ``listed`` is what Tapir says A is connected to ({} = nothing)."""
    walls = {
        "A": {"begCoordinate": {"x": 0.0, "y": 0.0}, "endCoordinate": {"x": a_end, "y": 0.0}},
        "B": {"begCoordinate": {"x": 5.25, "y": -3.0}, "endCoordinate": {"x": 5.25, "y": 3.0}},
    }
    floors = floors or {}

    def details(params):
        out = []
        for e in params["elements"]:
            guid = e["elementId"]["guid"]
            det = dict(walls[guid])
            det["inheritEndSurface"] = guid in flagged
            det["sideMaterial"] = {"overridden": guid in overridden}
            out.append({"floorIndex": floors.get(guid, 0), "details": det})
        return fake.ok({"detailsOfElements": out})

    joins = {"A": ("connectedToEndPoint", ["B"])} if listed is None else listed

    def relations(params):
        out = []
        for e in params["elements"]:
            guid = e["elementId"]["guid"]
            connections = {"connectedToBeginPoint": [], "connectedToEndPoint": []}
            if guid in joins:
                key, others = joins[guid]
                connections[key] = [{"elementId": {"guid": o}} for o in others]
            out.append({"wallConnections": connections})
        return fake.ok({"relations": out})

    fake.handlers["GetDetailsOfElements"] = details
    fake.handlers["GetRelationsOfElements"] = relations


def names(element):
    return [element.surfaces[m].name for m in element.face_materials]


def run(fake, model):
    return apply_inherited_end_surfaces(TapirClient(fake.port, timeout=5), model)


def test_an_end_butting_against_the_neighbours_face_takes_that_face(fake_archicad):
    install(fake_archicad)
    model = model_of(wall_a(), wall_b())  # A ends at x = 5.0: B's inner (paint) face
    before = model.elements["A"]
    result = run(fake_archicad, model)
    after = model.elements["A"]
    assert names(after)[:2] == ["Paint", "Paint"]
    assert (result.walls, result.faces) == (1, 2) and "2 end face" in result.lines[0]
    assert names(after)[2:4] == ["Brick", "Insulation"]  # the free end keeps its skins
    assert names(after)[4:] == ["Stucco", "Stucco"]  # long sides untouched
    assert after.vertices == before.vertices and after.geometry_hash != before.geometry_hash
    assert set(names(after)) == {"Paint", "Brick", "Insulation", "Stucco"}
    assert names(model.elements["B"]) == names(wall_b())  # the neighbour is not changed


def test_an_end_flush_with_the_outer_face_takes_the_visible_skin_not_the_covered_one(fake_archicad):
    install(fake_archicad, a_end=5.5)
    model = model_of(wall_a(end_x=5.5), wall_b())  # A reaches x = 5.5: B's outer face
    run(fake_archicad, model)
    # stucco is the face you see; the brick face 1 cm behind it is covered
    assert names(model.elements["A"])[:2] == ["Stucco", "Stucco"]


def test_without_a_flush_face_the_neighbours_outer_finish_is_used(fake_archicad):
    install(fake_archicad, a_end=5.1)
    model = model_of(wall_a(end_x=5.1), wall_b())  # A ends inside B, in no plane of B
    run(fake_archicad, model)
    assert names(model.elements["A"])[:2] == ["Stucco", "Stucco"]  # 18 m2 outside, 9 m2 inside


def test_only_the_neighbour_that_has_the_setting_counts(fake_archicad):
    install(fake_archicad, flagged=("A",))
    model = model_of(wall_a(), wall_b())
    result = run(fake_archicad, model)
    assert model.elements["A"] == wall_a() and result.faces == 0 and not result.lines


def test_a_wall_without_the_setting_is_left_alone(fake_archicad):
    install(fake_archicad, flagged=("B",))
    model = model_of(wall_a(), wall_b())
    run(fake_archicad, model)
    assert model.elements["A"] == wall_a()


def test_a_free_end_is_left_alone(fake_archicad):
    install(fake_archicad, listed={}, a_end=9.0)  # nothing listed, no wall on the end point
    model = model_of(wall_a(), wall_b())
    run(fake_archicad, model)
    assert model.elements["A"] == wall_a()


def test_a_neighbour_tapir_does_not_list_is_found_by_position(fake_archicad):
    # A's end point (5.25, 0) lies on B's reference line, both on the same storey
    install(fake_archicad, listed={}, a_end=5.25)
    model = model_of(wall_a(), wall_b())
    run(fake_archicad, model)
    assert names(model.elements["A"])[:2] == ["Paint", "Paint"]


def test_a_wall_on_another_storey_is_not_found_by_position(fake_archicad):
    install(fake_archicad, listed={}, a_end=5.25, floors={"B": 1})
    model = model_of(wall_a(), wall_b())
    run(fake_archicad, model)
    assert model.elements["A"] == wall_a()


def test_a_sliver_does_not_decide_which_plane_is_the_end(fake_archicad):
    install(fake_archicad)
    sliver = (face_x(5.3, -0.25, -0.24, +1), "Oak")  # 3 cm2 of wall end, 30 cm further out
    model = model_of(wall_a(extra=[sliver]), wall_b())
    run(fake_archicad, model)
    assert names(model.elements["A"])[:2] == ["Paint", "Paint"]  # not decided by the sliver


def test_unused_surfaces_are_dropped(fake_archicad):
    install(fake_archicad)
    model = model_of(wall_a(), wall_b())
    run(fake_archicad, model)
    used = set(model.elements["A"].face_materials)
    assert used == set(range(len(model.elements["A"].surfaces)))


def test_a_wall_with_an_explicit_edge_surface_is_skipped_and_reported(fake_archicad):
    install(fake_archicad, overridden=("A",))
    model = model_of(wall_a(), wall_b())
    result = run(fake_archicad, model)
    assert model.elements["A"] == wall_a()
    assert result.skipped == 1 and "explicit edge surface" in result.lines[0]


def test_a_neighbour_missing_from_the_model_is_ignored(fake_archicad):
    install(fake_archicad)
    model = model_of(wall_a())
    run(fake_archicad, model)
    assert model.elements["A"] == wall_a()


def test_faces_that_do_not_lie_over_the_neighbour_keep_their_surface(fake_archicad):
    install(fake_archicad)
    moved = build("A", [(tuple((x - 4.0, y, z) for x, y, z in face_x(5.0, -0.25, 0.0, +1)), "Brick")])
    model = model_of(moved, wall_b())  # the end face sits 4 m away from B (a reveal, say)
    run(fake_archicad, model)
    assert names(model.elements["A"]) == ["Brick"]


def test_a_t_junction_through_the_reference_line_is_listed_under_either_end(fake_archicad):
    # Tapir lists such a neighbour under connectedWithReferenceLineToEndPoints and does not say
    # which end: A's end point (5.25, 0) lies on B's reference line x = 5.25
    install(
        fake_archicad, a_end=5.25,
        listed={"A": ("connectedWithReferenceLineToEndPoints", ["B"])},
    )
    model = model_of(wall_a(), wall_b())
    result = run(fake_archicad, model)
    assert names(model.elements["A"])[:2] == ["Paint", "Paint"] and result.faces == 2
    assert names(model.elements["A"])[2:4] == ["Brick", "Insulation"]  # far end: not on the line


def test_walls_that_end_on_the_side_of_this_wall_are_ignored(fake_archicad):
    install(fake_archicad, a_end=9.0, listed={"A": ("connectedToReferenceLine", ["B"])})
    model = model_of(wall_a(), wall_b())
    run(fake_archicad, model)
    assert model.elements["A"] == wall_a()
