"""AI 请求的整体截止时间与协作取消。

背景：``requests`` 的 timeout 只对**单次 socket 读写**生效，服务端持续慢速
吐字节时永远不触发，曾导致一个拆题任务挂住约 50 分钟、占死导入工作线程。
``_post_bounded`` 用分块读取 + 墙上时钟上限 + 取消轮询来保证调用一定回来。
"""

import time

import pytest
import requests

from mathbank import ai_http


class _FakeResponse:
    """模拟一个"慢慢吐字节"的响应，用于触发超时/取消分支。"""

    def __init__(self, chunks, status_code=200):
        self._chunks = chunks
        self.status_code = status_code
        self.headers = {"x-request-id": "req-1"}
        self.closed = False

    def iter_content(self, chunk_size=8192):
        for chunk in self._chunks:
            time.sleep(0.01)
            yield chunk

    def close(self):
        self.closed = True


def _install_fake(monkeypatch, chunks):
    captured = {}

    def _fake(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _FakeResponse(chunks)

    monkeypatch.setattr(ai_http, "robust_request_post", _fake)
    return captured


def test_bounded_read_buffers_body_and_exposes_json(monkeypatch):
    """正常响应：分块读完后仍能 .json()，且读取超时被压短以便及时取消。"""

    captured = _install_fake(
        monkeypatch,
        [b'{"choices":[{"message":', b'{"content":"hi"}}]}'],
    )
    resp = ai_http._post_bounded("http://x/v1", {"json": {}, "timeout": 180})

    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "hi"
    # 单次读等待必须被压短，否则取消要等满原 timeout 才生效
    assert captured["kwargs"]["stream"] is True
    assert captured["kwargs"]["timeout"] <= 30
    assert captured["kwargs"]["json"] == {}


def test_bounded_read_aborts_when_deadline_passes(monkeypatch):
    """超过整体截止时间必须抛 ReadTimeout，而不是无限读下去。"""

    _install_fake(monkeypatch, [b"x"] * 500)
    with pytest.raises(requests.exceptions.ReadTimeout):
        ai_http._post_bounded(
            "http://x/v1", {"timeout": 180}, deadline_seconds=0.05
        )


def test_bounded_read_aborts_when_task_cancelled(monkeypatch):
    """已取消的任务应在读取过程中立刻中断，释放工作线程。"""

    _install_fake(monkeypatch, [b"x"] * 500)
    with pytest.raises(ai_http.AICancelled):
        ai_http._post_bounded(
            "http://x/v1", {"timeout": 180}, cancel_check=lambda: True
        )


def test_post_chat_completion_uses_bounded_path_when_guarded(monkeypatch):
    """传入截止时间/取消钩子时走 bounded 路径；否则保持原有行为。"""

    captured = _install_fake(monkeypatch, [b'{"ok":1}'])

    class _Provider:
        api_key = "k"
        credential_label = "k"
        provider_label = "p"
        chat_completions_url = "http://x/v1"

    ai_http.post_chat_completion(
        _Provider(), {}, timeout=180, deadline_seconds=30
    )
    assert captured["kwargs"]["stream"] is True
    assert captured["kwargs"]["timeout"] <= 30


def test_bounded_read_honors_explicit_read_timeout(monkeypatch):
    """整卷拆题这类长生成可显式放宽；未传时仍压到 30s 以便取消尽快生效。"""

    captured = _install_fake(monkeypatch, [b'{"ok":1}'])
    ai_http._post_bounded("http://x/v1", {"json": {}, "timeout": 180}, read_timeout=240)
    # 显式 read_timeout 是读上限本身，不再被调用方 timeout 用 min 压死
    assert captured["kwargs"]["timeout"] == (30.0, 240.0)

    captured2 = _install_fake(monkeypatch, [b'{"ok":1}'])
    ai_http._post_bounded("http://x/v1", {"json": {}, "timeout": 180}, read_timeout=120)
    # 读按显式值放宽，连接仍取短上限
    assert captured2["kwargs"]["timeout"] == (30.0, 120.0)


def test_post_chat_completion_passes_read_timeout_through(monkeypatch):
    """read_timeout 必须透传到 bounded 路径，且默认路径不受影响。"""

    captured = _install_fake(monkeypatch, [b'{"ok":1}'])

    class _Provider:
        api_key = "k"
        credential_label = "k"
        provider_label = "p"
        chat_completions_url = "http://x/v1"

    ai_http.post_chat_completion(
        _Provider(), {}, timeout=180, deadline_seconds=480, read_timeout=240
    )
    # 放宽必须真的生效：老路整卷拆题曾被 min(180, 240)=180 静默压回 180s
    assert captured["kwargs"]["timeout"] == (30.0, 240.0)
