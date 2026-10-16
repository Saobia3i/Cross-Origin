from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _assert_envelope(body):
    assert set(body) == {"ok", "data", "meta", "errors"}
    assert body["meta"]["source"] == "dhakapath-backend"
    assert body["meta"]["request_id"].startswith("req_")


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    _assert_envelope(body)
    assert body["ok"] is True


def test_catalog():
    body = client.get("/api/catalog").json()
    _assert_envelope(body)
    paths = {(e["method"], e["path"]) for e in body["data"]["endpoints"]}
    assert ("POST", "/api/trips/search") in paths
    assert ("PATCH", "/api/admin/hubs/{hub_id}") in paths


def test_not_implemented_placeholder():
    r = client.post("/api/admin/hubs", json={})
    assert r.status_code == 501
    body = r.json()
    _assert_envelope(body)
    assert body["ok"] is False and body["errors"][0]["code"] == "NOT_IMPLEMENTED"


def test_unknown_route_uses_envelope():
    r = client.get("/nope")
    assert r.status_code == 404
    assert r.json()["errors"][0]["code"] == "NOT_FOUND"
