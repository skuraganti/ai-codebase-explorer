"""HTTP boundaries exercised independently of index/worker implementation."""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.security import protect


@pytest.fixture
def client():
    app = FastAPI()
    protect(app)

    @app.get("/")
    def page():
        return {"ready": True}

    @app.post("/echo")
    async def echo(request: Request):
        return {"bytes": len(await request.body())}

    return TestClient(app, base_url="http://127.0.0.1")


def test_host_origin_and_cross_site_requests_refused(client):
    assert client.get("/", headers={"Host": "foreign.example"}).status_code == 400
    assert (
        client.post("/echo", content="x", headers={"Origin": "https://foreign.example"}).status_code
        == 403
    )
    assert (
        client.post("/echo", content="x", headers={"Sec-Fetch-Site": "cross-site"}).status_code
        == 403
    )
    assert (
        client.post("/echo", content="x", headers={"Origin": "http://127.0.0.1"}).status_code == 200
    )


def test_body_limit_precedes_endpoint_and_handles_stream(client):
    assert client.post("/echo", content=b"x" * 524289).status_code == 413

    def chunks():
        yield b"x" * 300000
        yield b"x" * 300000

    assert client.post("/echo", content=chunks()).status_code == 413
    assert client.post("/echo", content=b"x" * 32).json() == {"bytes": 32}


def test_security_headers_on_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
