"""Builds a tiny IFC4 file that mimics Archicad's "IFC4 Reference View" export.

Products are named by Element ID, carry the Archicad GUID in ``Tag``, use polygon-face sets
with one styled item per material, and the project unit is millimetres. No pytest import, so
it also runs inside Blender (where ifcopenshell is bundled) for the add-on tests.
"""

WALL_GUID = "AAAAAAAA-0000-4000-8000-000000000001"
SLAB_GUID = "BBBBBBBB-0000-4000-8000-000000000002"
WINDOW_GUID = "CCCCCCCC-0000-4000-8000-000000000003"
DUP_ID = "WALL-0005"  # two different elements share this Element ID, like after copy/paste


def _box(x, y, z, dx, dy, dz):
    """Vertices (mm) and outward-facing quad faces of a box."""
    v = [
        (x, y, z), (x + dx, y, z), (x + dx, y + dy, z), (x, y + dy, z),
        (x, y, z + dz), (x + dx, y, z + dz), (x + dx, y + dy, z + dz), (x, y + dy, z + dz),
    ]
    f = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    return v, f


def build_synthetic_ifc(
    path, wall_height_mm=3000.0, second_wall_guid=None, include_window=True, terrain=False,
    site_rotation_deg=0.0,
):
    import ifcopenshell
    import ifcopenshell.api

    f = ifcopenshell.api.run("project.create_file", version="IFC4")
    project = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcProject", name="P")
    ifcopenshell.api.run("unit.assign_unit", f, length={"is_metric": True, "raw": "MILLIMETERS"})
    ctx = ifcopenshell.api.run("context.add_context", f, context_type="Model")
    body = ifcopenshell.api.run(
        "context.add_context", f, context_type="Model", context_identifier="Body",
        target_view="MODEL_VIEW", parent=ctx,
    )
    site = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcSite", name="Site")
    ifcopenshell.api.run("aggregate.assign_object", f, products=[site], relating_object=project)

    def style(name, rgb, transparency=0.0):
        s = ifcopenshell.api.run("style.add_style", f, name=name)
        ifcopenshell.api.run(
            "style.add_surface_style", f, style=s, ifc_class="IfcSurfaceStyleRendering",
            attributes={
                "SurfaceColour": {"Name": None, "Red": rgb[0], "Green": rgb[1], "Blue": rgb[2]},
                "Transparency": transparency,
            },
        )
        return s

    brick = style("Brick - Aged Light", (0.6, 0.3, 0.2))
    plaster = style("Stucco - White Fine", (0.9, 0.9, 0.9))
    glass = style("Glass - Clear Fast", (0.5, 0.6, 0.7), 0.7)

    def mesh_rep(v_mm, faces):
        # The API expects one list per item and SI (metre) coordinates, and converts them to
        # the project unit (millimetres) itself.
        return ifcopenshell.api.run(
            "geometry.add_mesh_representation", f, context=body,
            vertices=[[[c / 1000.0 for c in p] for p in v_mm]],
            faces=[[list(fc) for fc in faces]],
        )

    def product(ifc_class, element_id, guid, boxes_and_styles):
        el = ifcopenshell.api.run("root.create_entity", f, ifc_class=ifc_class, name=element_id)
        el.Tag = guid
        items = []
        for (v, faces), st in boxes_and_styles:
            rep = mesh_rep(v, faces)
            item = rep.Items[0]
            ifcopenshell.api.run("style.assign_item_style", f, item=item, style=st)
            items.append(item)
        rep.Items = tuple(items)
        ifcopenshell.api.run("geometry.assign_representation", f, product=el, representation=rep)
        ifcopenshell.api.run("geometry.edit_object_placement", f, product=el)
        return el

    product("IfcWall", "WALL-0001", WALL_GUID,
            [(_box(0, 0, 0, 4000, 300, wall_height_mm), brick),
             (_box(0, 300, 0, 4000, 20, wall_height_mm), plaster)])
    product("IfcSlab", "SLAB-0001", SLAB_GUID, [(_box(0, 0, -300, 4000, 3000, 300), plaster)])
    if include_window:
        product("IfcWindow", "WNDW-0001", WINDOW_GUID,
                [(_box(1000, 0, 900, 1200, 100, 1200), glass)])
    if second_wall_guid:
        product("IfcWall", DUP_ID, second_wall_guid, [(_box(0, 5000, 0, 1000, 300, 3000), brick)])
    if terrain:
        # Archicad merges all Mesh elements into the IfcSite, which has no Tag.
        ground = mesh_rep(*_box(-5000, -5000, -600, 20000, 20000, 300))
        item = ground.Items[0]
        ifcopenshell.api.run("style.assign_item_style", f, item=item, style=plaster)
        ifcopenshell.api.run(
            "geometry.assign_representation", f, product=site, representation=ground
        )
    # Things the reader must skip or exclude. (IfcSpace has no Tag attribute in IFC4;
    # Archicad zones come out untagged, too.)
    space = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcSpace", name="ZONE-0001")
    rep = mesh_rep(*_box(0, 0, 0, 1000, 1000, 2500))
    ifcopenshell.api.run("geometry.assign_representation", f, product=space, representation=rep)
    untagged = product("IfcWall", "WALL-9999", None, [(_box(9000, 0, 0, 100, 100, 100), brick)])
    untagged.Tag = None
    if site_rotation_deg:
        # Like Archicad: Project North is baked into the IfcSite placement and every element
        # is placed relative to it, while raw coordinates stay in model coordinates.
        import math

        import numpy as np

        a = math.radians(site_rotation_deg)
        rot = np.eye(4)
        rot[0][0], rot[0][1] = math.cos(a), -math.sin(a)
        rot[1][0], rot[1][1] = math.sin(a), math.cos(a)
        ifcopenshell.api.run("geometry.edit_object_placement", f, product=site, matrix=rot)
        for el in f.by_type("IfcElement"):
            if el.ObjectPlacement is not None:
                el.ObjectPlacement.PlacementRelTo = site.ObjectPlacement
    f.write(str(path))
    return str(path)
