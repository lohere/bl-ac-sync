"""Shared fixtures.

``synthetic_ifc`` builds a tiny IFC4 file that mimics what Archicad's "IFC4 Reference View"
translator writes: products named by Element ID, the Archicad GUID in ``Tag``, polygon-face
sets, one styled item per material, project units in millimetres.

Real-model tests run only when ``ACSYNC_TEST_IFC`` points to an Archicad export.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

WALL_GUID = "AAAAAAAA-0000-4000-8000-000000000001"
SLAB_GUID = "BBBBBBBB-0000-4000-8000-000000000002"
WINDOW_GUID = "CCCCCCCC-0000-4000-8000-000000000003"
DUP_ID = "WALL-0005"  # two different elements share this Element ID, like after copy/paste


def _box(x, y, z, dx, dy, dz):
    """Vertices and quad faces of a box, in millimetres, outward-facing."""
    v = [
        (x, y, z), (x + dx, y, z), (x + dx, y + dy, z), (x, y + dy, z),
        (x, y, z + dz), (x + dx, y, z + dz), (x + dx, y + dy, z + dz), (x, y + dy, z + dz),
    ]
    f = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    return v, f


def build_synthetic_ifc(path, wall_height_mm=3000.0, second_wall_guid=None):
    import ifcopenshell
    import ifcopenshell.api

    f = ifcopenshell.api.run("project.create_file", version="IFC4")
    project = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcProject", name="P")
    ifcopenshell.api.run(
        "unit.assign_unit", f, length={"is_metric": True, "raw": "MILLIMETERS"}
    )
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
    product("IfcWindow", "WNDW-0001", WINDOW_GUID, [(_box(1000, 0, 900, 1200, 100, 1200), glass)])
    if second_wall_guid:
        product("IfcWall", DUP_ID, second_wall_guid, [(_box(0, 5000, 0, 1000, 300, 3000), brick)])
    # Things the reader must skip or exclude
    space = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcSpace", name="ZONE-0001")
    # (IfcSpace has no Tag attribute in IFC4; Archicad zones come out untagged, too.)
    rep = mesh_rep(*_box(0, 0, 0, 1000, 1000, 2500))
    ifcopenshell.api.run("geometry.assign_representation", f, product=space, representation=rep)
    untagged = product("IfcWall", "WALL-9999", None, [(_box(9000, 0, 0, 100, 100, 100), brick)])
    untagged.Tag = None
    f.write(str(path))
    return str(path)


@pytest.fixture(scope="session")
def synthetic_ifc(tmp_path_factory):
    return build_synthetic_ifc(tmp_path_factory.mktemp("ifc") / "synthetic.ifc")


@pytest.fixture()
def real_ifc():
    path = os.environ.get("ACSYNC_TEST_IFC")
    if not path or not os.path.isfile(path):
        pytest.skip("set ACSYNC_TEST_IFC to an Archicad IFC4 Reference View export")
    return path


class FakeArchicad:
    """A scripted stand-in for Archicad's JSON endpoint."""

    def __init__(self):
        self.calls = []  # (command, parameters)
        self.handlers = {}  # key -> callable(parameters) -> dict | str
        self.server = HTTPServer(("127.0.0.1", 0), self._handler())
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                command = body["command"]
                params = body.get("parameters") or {}
                key = command
                if command == "API.ExecuteAddOnCommand":
                    key = params["addOnCommandId"]["commandName"]
                    params = params["addOnCommandParameters"]
                outer.calls.append((key, params))
                handler = outer.handlers.get(key)
                if handler is None:
                    answer = {"succeeded": False, "error": {"code": 4010, "message": "missing"}}
                else:
                    answer = handler(params)
                data = json.dumps(answer).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        return Handler

    @staticmethod
    def ok(response=None, addon=True):
        if addon:
            return {"succeeded": True, "result": {"addOnCommandResponse": response or {}}}
        return {"succeeded": True, "result": response or {}}

    @staticmethod
    def error(code, message):
        return {"succeeded": False, "error": {"code": code, "message": message}}

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def fake_archicad():
    server = FakeArchicad()
    server.handlers["API.GetProductInfo"] = lambda p: server.ok(
        {"version": 29, "buildNumber": 3000, "languageCode": "INT"}, addon=False
    )
    server.handlers["GetAddOnVersion"] = lambda p: server.ok({"version": "1.7.1"})
    server.handlers["GetProjectInfo"] = lambda p: server.ok(
        {"isUntitled": False, "isTeamwork": False, "projectPath": "C:/x/test.pln",
         "projectLocation": "C:/x/test.pln", "projectName": "test"}
    )
    server.handlers["GetIFCExportTranslators"] = lambda p: server.ok(
        {"translators": [{"name": "IFC4 Reference View-based Export", "preview": False},
                         {"name": "Exact Geometry Export", "preview": False}]}
    )
    yield server
    server.close()
