"""Shared fixtures.

``synthetic_ifc`` builds a tiny IFC4 file that mimics Archicad's "IFC4 Reference View" export
(see ifc_fixture.py). Real-model tests run only when ``ACSYNC_TEST_IFC`` points to an export.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from ifc_fixture import build_synthetic_ifc


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
