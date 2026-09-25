from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from conftest import SOURCE, assert_error, rows
from jsonschema import Draft4Validator
from openapi_spec_validator import validate

from app.db_models import (
    ProjectRecord,
)

SPEC = yaml.safe_load((Path(__file__).resolve().parents[2] / "openapi.yaml").read_text())


def json_schema(value):
    """Translate OpenAPI 3.0 nullable to JSON Schema for payload assertions."""
    if isinstance(value, list):
        return [json_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {k: json_schema(v) for k, v in value.items() if k != "nullable"}
    if value.get("nullable"):
        result["type"] = [result["type"], "null"]
        if "enum" in result:
            result["enum"].append(None)
    return result


SCHEMA = json_schema(SPEC)


def check(response, path, method):
    assert response.status_code == 200, response.text
    schema = SPEC["paths"][path][method]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    Draft4Validator({**json_schema(schema), "components": SCHEMA["components"]}).validate(
        response.json()
    )
    return response.json()


def test_openapi_valid_and_routes_match(client):
    validate(SPEC)
    generated = client.get("/openapi.json").json()
    validate(generated)
    expected = {
        (path, method, op["operationId"])
        for path, methods in SPEC["paths"].items()
        for method, op in methods.items()
    }
    actual = {
        (path, method, op["operationId"])
        for path, methods in generated["paths"].items()
        for method, op in methods.items()
    }
    assert expected == actual
    assert len(actual) == 19
    assert generated["components"]["securitySchemes"]["bearerAuth"]["scheme"] == "bearer"


PROTECTED = [
    (path, method)
    for path, methods in SPEC["paths"].items()
    for method, op in methods.items()
    if op.get("security", SPEC["security"])
]


@pytest.mark.parametrize("path,method", PROTECTED)
def test_every_protected_route_requires_bearer(client, path, method):
    response = client.request(method.upper(), path.replace("{id}", "foreign"), json={})
    assert_error(response, 401, "UNAUTHENTICATED")


def test_every_operation_response_matches_root_spec(client, clock, database, app):
    signin = check(
        client.post(
            "/v1/auth/google", json={"email": "maya@example.com", "password": "DemoPass123!"}
        ),
        "/v1/auth/google",
        "post",
    )
    headers = {"Authorization": "Bearer " + signin["access_token"]}
    check(client.get("/v1/me", headers=headers), "/v1/me", "get")
    check(client.get("/v1/usage", headers=headers), "/v1/usage", "get")
    check(client.get("/v1/projects", headers=headers), "/v1/projects", "get")
    up = check(client.post("/v1/uploads", headers=headers, json=SOURCE), "/v1/uploads", "post")
    check(
        client.post(f"/v1/uploads/{up['uploadId']}/complete", headers=headers),
        "/v1/uploads/{id}/complete",
        "post",
    )
    base = f"/v1/projects/{up['projectId']}"
    check(client.get(base, headers=headers), "/v1/projects/{id}", "get")
    check(
        client.post(base + "/process", headers=headers, json={"goal": "viral"}),
        "/v1/projects/{id}/process",
        "post",
    )
    check(client.get(base + "/status", headers=headers), "/v1/projects/{id}/status", "get")
    check(client.post(base + "/cancel", headers=headers), "/v1/projects/{id}/cancel", "post")
    failed = next(p for p in rows(database, ProjectRecord) if p["status"] == "failed")
    base = f"/v1/projects/{failed['id']}"
    check(client.post(base + "/retry", headers=headers), "/v1/projects/{id}/retry", "post")
    clock.advance(8)
    check(client.get(base + "/transcript", headers=headers), "/v1/projects/{id}/transcript", "get")
    check(
        client.patch(base + "/transcript", headers=headers, json={"segments": []}),
        "/v1/projects/{id}/transcript",
        "patch",
    )
    check(client.get(base + "/downloads", headers=headers), "/v1/projects/{id}/downloads", "get")
    check(client.get(base + "/clips", headers=headers), "/v1/projects/{id}/clips", "get")
    check(
        client.post(base + "/analysis-runs", headers=headers, json={"goal": "educational"}),
        "/v1/projects/{id}/analysis-runs",
        "post",
    )
    clock.advance(4)
    check(client.delete(base, headers=headers), "/v1/projects/{id}", "delete")
    app.state.settings = replace(app.state.settings, youtube_import=True)
    check(
        client.post(
            "/v1/projects/youtube",
            headers=headers,
            json={
                "url": "https://youtu.be/abcdefghijk",
                "rightsConfirmed": True,
            },
        ),
        "/v1/projects/youtube",
        "post",
    )
    check(client.post("/v1/auth/logout", headers=headers), "/v1/auth/logout", "post")


def test_cors_allows_local_frontend_and_rejects_unlisted_origin(client):
    for origin, expected in [("http://localhost:3000", 200), ("https://untrusted.example", 400)]:
        response = client.options(
            "/v1/me",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        assert response.status_code == expected
        if expected == 200:
            assert response.headers["access-control-allow-origin"] == origin


def test_validation_and_routing_errors_use_stable_envelope(client, headers):
    assert_error(
        client.post("/v1/uploads", headers=headers, content="{broken"), 422, "INVALID_INPUT"
    )
    assert_error(client.get("/missing"), 404, "NOT_FOUND")
