# -*- coding: utf-8 -*-
"""本地令牌（X-Local-Token）接线的回归测试。

真实事故：教案保存时用了从未写入过的 localStorage 键名
``mathbank_local_token``，取到空串后仍原样塞进请求头；全局 fetch 包装器看到
头已存在就不再补令牌，于是写接口被 403 → 表现成「教案无法新建」。
"""

from pathlib import Path

import pytest

from mathbank.auth import LOCAL_TOKEN

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_JS_DIR = PROJECT_ROOT / "static" / "js"
API_JS = STATIC_JS_DIR / "api.js"
LESSON_JS = STATIC_JS_DIR / "lesson.js"

ALL_JS = sorted(STATIC_JS_DIR.glob("*.js"))


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_no_js_uses_the_bogus_token_key():
    assert not any("mathbank_local_token" in _read(p) for p in ALL_JS)


def test_fetch_wrapper_overrides_empty_token_header():
    """空的 X-Local-Token 必须被包装器替换，而不是直接放行。"""

    source = _read(API_JS)
    start = source.index("CSRF Token protection & window.fetch Monkey Patch")
    block = source[start:start + 4000]
    assert "headers.get('X-Local-Token')" in block
    assert "headers.has('X-Local-Token')" not in block


def test_shared_token_helper_exists_and_is_used():
    api = _read(API_JS)
    assert "window.getLocalToken = function" in api
    # 教案保存与模型拉取都改用统一入口
    lesson = _read(LESSON_JS)
    assert "window.getLocalToken" in lesson
    assert api.count("window.getLocalToken") >= 3


def test_lesson_auth_headers_omits_empty_token():
    """取不到令牌就不要写这个头，交给全局包装器补。"""

    lesson = _read(LESSON_JS)
    start = lesson.index("function authHeaders()")
    block = lesson[start:start + 400]
    assert "if (token) headers['X-Local-Token'] = token;" in block
    assert "'X-Local-Token': ''" not in block


@pytest.mark.parametrize(
    "verb,path",
    [("post", "/api/lesson-plans"), ("post", "/api/lecture/drafts")],
)
def test_write_endpoints_reject_empty_token(client, verb, path):
    """服务端拦得住空令牌，因此前端必须真的带上它。"""

    response = getattr(client, verb)(
        path, json={}, headers={"X-Local-Token": ""}
    )
    assert response.status_code in (401, 403)


def test_write_endpoints_accept_real_token(client):
    response = client.post(
        "/api/lesson-plans",
        json={"title": "令牌校验用教案"},
        headers={"X-Local-Token": LOCAL_TOKEN},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "令牌校验用教案"
