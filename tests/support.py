"""Runs the store scripts against local mocks of the store APIs, which check
requests against the API descriptions in tests/specs."""

import json
import re
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPECS = Path(__file__).resolve().parent / "specs"


class MockApi:
    """Local HTTP server recording requests and answering with handler(request);
    an exception handler raises goes to errors and is answered with HTTP 400."""

    def __init__(self, handler):
        self.handler = handler
        self.requests = []
        self.errors = []
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def handle_request(self):
                url = urllib.parse.urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                request = Request(
                    self.command,
                    url.path,
                    {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()},
                    self.rfile.read(length) if length else b"",
                    self.headers,
                )
                mock.requests.append(request)
                try:
                    status, body = mock.handler(request)
                except Exception as error:  # noqa: BLE001 - reported by the test
                    mock.errors.append(f"{request}: {error}")
                    status, body = 400, {"error": {"message": f"mock: {error}"}}
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if body is not None:
                    self.wfile.write(json.dumps(body).encode())

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = handle_request

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def calls(self):
        return [f"{r.method} {r.path}" for r in self.requests]


class Request:
    def __init__(self, method, path, query, raw, headers):
        self.method = method
        self.path = path
        self.query = query
        self.raw = raw
        self.headers = headers

    def json(self):
        return json.loads(self.raw)

    def __str__(self):
        return f"{self.method} {self.path}"


def fake_gh(directory):
    """Puts a gh on PATH that prints $FAKE_NOTES as the release description."""
    path = Path(directory) / "gh"
    path.write_text('#!/bin/sh\nprintf "%s" "$FAKE_NOTES"\n')
    path.chmod(0o755)


def run_script(name, env, skip_sleep=False):
    """Runs scripts/<name>; skip_sleep makes its time.sleep return at once."""
    script = str(ROOT / "scripts" / name)
    if skip_sleep:
        code = (
            "import runpy, sys, time; time.sleep = lambda seconds: None; "
            "runpy.run_path(sys.argv[1], run_name='__main__')"
        )
        args = [sys.executable, "-c", code, script]
    else:
        args = [sys.executable, script]
    return subprocess.run(args, env=env, capture_output=True, text=True, timeout=120)


class PlaySpec:
    """Request body checks from the Google Play Developer API's discovery document."""

    def __init__(self):
        self.schemas = json.loads((SPECS / "androidpublisher-v3.json").read_text())[
            "schemas"
        ]

    def validate(self, value, name, path="$"):
        properties = self.schemas[name]["properties"]
        for key, item in value.items():
            if key not in properties:
                raise ValueError(f"{path}: unknown field {key}")
            field = properties[key]
            if "enum" in field and item not in field["enum"]:
                raise ValueError(f"{path}.{key}: {item} is not one of {field['enum']}")
            if field.get("type") == "array":
                if not isinstance(item, list):
                    raise ValueError(f"{path}.{key}: not a list")
                ref = field["items"].get("$ref")
                for index, element in enumerate(item):
                    if ref:
                        self.validate(element, ref, f"{path}.{key}[{index}]")
                    elif not isinstance(element, str):
                        raise ValueError(f"{path}.{key}[{index}]: not a string")


class AppStoreSpec:
    """Request checks from the App Store Connect API's OpenAPI description."""

    def __init__(self):
        spec = json.loads((SPECS / "app-store-connect-api-4.5.json").read_text())
        self.paths = spec["paths"]
        self.schemas = spec["components"]["schemas"]

    def resolve(self, schema):
        while "$ref" in schema:
            schema = self.schemas[schema["$ref"].split("/")[-1]]
        return schema

    def operation(self, method, path):
        for template, operations in self.paths.items():
            pattern = "^" + re.sub(r"\{[^}]+\}", "[^/]+", template) + "$"
            if re.match(pattern, path) and method.lower() in operations:
                return operations[method.lower()]
        raise ValueError(f"no operation {method} {path} in the API")

    def check(self, request):
        operation = self.operation(request.method, request.path)
        parameters = {p["name"]: p for p in operation.get("parameters", [])}
        for name, value in request.query.items():
            if name not in parameters:
                raise ValueError(f"unknown parameter {name}")
            schema = parameters[name]["schema"]
            allowed = (schema.get("items") or schema).get("enum")
            for part in value.split(","):
                if allowed and part not in allowed:
                    raise ValueError(f"{name}={part} is not one of the allowed values")
        for name, parameter in parameters.items():
            if (
                parameter.get("required")
                and parameter["in"] == "query"
                and name not in request.query
            ):
                raise ValueError(f"missing required parameter {name}")
        if request.raw:
            schema = operation["requestBody"]["content"]["application/json"]["schema"]
            self.validate(request.json(), schema)

    def validate(self, value, schema, path="$"):
        schema = self.resolve(schema)
        if value is None:
            if schema.get("nullable"):
                return
            raise ValueError(f"{path}: null is not allowed")
        kind = schema.get("type")
        if kind == "object":
            properties = schema.get("properties", {})
            for name in schema.get("required", []):
                if name not in value:
                    raise ValueError(f"{path}: missing {name}")
            for name, item in value.items():
                if name not in properties:
                    raise ValueError(f"{path}: unknown field {name}")
                self.validate(item, properties[name], f"{path}.{name}")
        elif kind == "array":
            if not isinstance(value, list):
                raise ValueError(f"{path}: not a list")
            for index, item in enumerate(value):
                self.validate(item, schema["items"], f"{path}[{index}]")
        elif kind == "string":
            if "enum" in schema and value not in schema["enum"]:
                raise ValueError(f"{path}: {value} is not one of {schema['enum']}")
        elif kind == "boolean" and not isinstance(value, bool):
            raise ValueError(f"{path}: not a boolean")
