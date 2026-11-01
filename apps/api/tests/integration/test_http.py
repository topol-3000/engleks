import re

import pytest

from engleks_contracts import HealthResponse, HealthStatus


async def test_healthz_ok(client):
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert HealthResponse.model_validate(r.json()).status is HealthStatus.OK


async def test_request_id_generated(client):
    r = await client.get("/healthz")
    assert re.fullmatch(r"[0-9a-f]{32}", r.headers["X-Request-ID"])


async def test_request_id_propagated(client):
    r = await client.get("/healthz", headers={"X-Request-ID": "abc-123"})
    assert r.headers["X-Request-ID"] == "abc-123"


@pytest.mark.parametrize("bad", ["x" * 129, "bad id!", "a;b"])
async def test_invalid_request_id_replaced(client, bad):
    r = await client.get("/healthz", headers={"X-Request-ID": bad})
    assert re.fullmatch(r"[0-9a-f]{32}", r.headers["X-Request-ID"])


async def test_unhandled_error_maps_to_internal_error(app, client):
    async def boom() -> None:
        raise RuntimeError("secret detail")

    app.add_api_route("/boom", boom)
    r = await client.get("/boom")
    assert r.status_code == 500
    assert r.json() == {"code": "internal_error", "message": "Internal server error"}


async def test_unhandled_error_keeps_request_id(app, client):
    async def boom() -> None:
        raise RuntimeError("boom")

    app.add_api_route("/boom", boom)
    r = await client.get("/boom", headers={"X-Request-ID": "req-500"})
    assert r.headers["X-Request-ID"] == "req-500"


async def test_validation_error_maps(app, client):
    async def needs_int(n: int) -> None: ...

    app.add_api_route("/needs-int", needs_int)
    r = await client.get("/needs-int", params={"n": "abc"})
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"
