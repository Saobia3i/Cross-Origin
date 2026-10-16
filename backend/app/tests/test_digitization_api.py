import json

from app.config import settings


def test_signboard_extraction_requires_openrouter_key(client, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "")

    response = client.post(
        "/api/digitize/signboard",
        files={"image": ("signboard.png", b"image", "image/png")},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["errors"][0]["code"] == "VISION_NOT_CONFIGURED"
    assert "OPENROUTER_API_KEY" in body["errors"][0]["message"]


def test_signboard_extraction_uses_openrouter_vision(client, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")

    class MockResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps({
                "document_type": "fare_chart",
                "route_name": "Mirpur - Farmgate",
                "stops": ["Mirpur 10", "Farmgate"],
                "fare_rows": [{"from": "Mirpur 10", "to": "Farmgate", "fare_bdt": 25}],
                "confidence": 91,
            })}}]}

    def mock_post(url, *, headers, json, timeout):
        assert url == "https://openrouter.ai/api/v1/chat/completions"
        assert headers == {"Authorization": "Bearer test-key"}
        assert json["model"] == settings.openrouter_vision_model
        assert json["messages"][0]["content"][1]["type"] == "image_url"
        return MockResponse()

    monkeypatch.setattr("app.api_digitization.httpx.post", mock_post)
    response = client.post(
        "/api/digitize/signboard",
        files={"image": ("signboard.png", b"image", "image/png")},
    )

    assert response.status_code == 200
    draft = response.json()["data"]
    assert draft["source"] == "openrouter_vision"
    assert draft["route_name"] == "Mirpur - Farmgate"
    assert draft["fare_rows"][0]["fare_bdt"] == 25
