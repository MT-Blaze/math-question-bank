# -*- coding: utf-8 -*-
"""从官网拉取模型列表（POST /api/ai/models）回归测试。

Covers: 鉴权、未知提供商、缺 Key、正常返回、上游报错、非 JSON 响应、
未保存的 Key/Base 覆盖，以及"绝不回显 API Key"。
"""

from unittest.mock import MagicMock, patch

from main import LOCAL_TOKEN

PROVIDER = "deepseek"
ENDPOINT = "/api/ai/models"


def _headers():
    return {"X-Local-Token": LOCAL_TOKEN}


def _mock_response(status_code=200, payload=None, json_error=False):
    response = MagicMock(status_code=status_code)
    if json_error:
        response.json.side_effect = ValueError("not json")
    else:
        response.json.return_value = payload if payload is not None else {}
    return response


def test_models_endpoint_requires_local_token(client):
    resp = client.post(ENDPOINT, json={"provider": PROVIDER})
    assert resp.status_code in (401, 403)


def test_models_endpoint_rejects_unknown_provider(client):
    resp = client.post(ENDPOINT, headers=_headers(), json={"provider": "not-a-vendor"})
    assert resp.status_code == 400
    assert "未知的模型提供商" in resp.json()["message"]


def test_models_endpoint_reports_missing_api_key(client, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    resp = client.post(ENDPOINT, headers=_headers(), json={"provider": PROVIDER})
    assert resp.status_code == 400
    assert "API Key" in resp.json()["message"]


def test_models_endpoint_returns_official_model_list(client, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-key")
    upstream = _mock_response(
        payload={
            "object": "list",
            "data": [
                {"id": "deepseek-flash", "object": "model"},
                {"id": "deepseek-v4-pro", "object": "model"},
                {"id": "deepseek-flash", "object": "model"},
            ],
        }
    )

    with patch("mathbank.routes_settings.robust_request_get", return_value=upstream) as get:
        resp = client.post(ENDPOINT, headers=_headers(), json={"provider": PROVIDER})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["provider"] == "deepseek"
    assert body["api_base"] == "https://api.deepseek.com"
    assert body["models"] == ["deepseek-flash", "deepseek-v4-pro"]
    assert body["count"] == 2
    # 密钥绝不能回显给前端
    assert "secret-key" not in resp.text

    get.assert_called_once_with(
        "https://api.deepseek.com/models",
        headers={
            "Authorization": "Bearer secret-key",
            "Content-Type": "application/json",
        },
        timeout=20,
    )


def test_models_endpoint_accepts_unsaved_key_and_base_overrides(client, monkeypatch):
    monkeypatch.delenv("ZHONGZHAN_GPT_API_KEY", raising=False)
    upstream = _mock_response(payload={"data": [{"id": "gpt-5.6-sol"}]})

    with patch("mathbank.routes_settings.robust_request_get", return_value=upstream) as get:
        resp = client.post(
            ENDPOINT,
            headers=_headers(),
            json={
                "provider": "zhongzhan_gpt",
                "api_key": "typed-key",
                "api_base": "https://transit.example/v1",
            },
        )

    assert resp.status_code == 200
    assert resp.json()["models"] == ["gpt-5.6-sol"]
    assert "typed-key" not in resp.text
    get.assert_called_once_with(
        "https://transit.example/v1/models",
        headers={
            "Authorization": "Bearer typed-key",
            "Content-Type": "application/json",
        },
        timeout=20,
    )


def test_models_endpoint_surfaces_upstream_http_error(client, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-key")
    upstream = _mock_response(status_code=401)

    with patch("mathbank.routes_settings.robust_request_get", return_value=upstream):
        resp = client.post(ENDPOINT, headers=_headers(), json={"provider": PROVIDER})

    assert resp.status_code == 502
    assert "HTTP 401" in resp.json()["message"]


def test_models_endpoint_rejects_non_json_upstream_body(client, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-key")
    upstream = _mock_response(json_error=True)

    with patch("mathbank.routes_settings.robust_request_get", return_value=upstream):
        resp = client.post(ENDPOINT, headers=_headers(), json={"provider": PROVIDER})

    assert resp.status_code == 502
    assert "非 JSON" in resp.json()["message"]


def test_models_endpoint_reports_empty_upstream_list(client, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-key")
    upstream = _mock_response(payload={"data": []})

    with patch("mathbank.routes_settings.robust_request_get", return_value=upstream):
        resp = client.post(ENDPOINT, headers=_headers(), json={"provider": PROVIDER})

    assert resp.status_code == 200
    assert resp.json()["models"] == []
    assert resp.json()["count"] == 0
