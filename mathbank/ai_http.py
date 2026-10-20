"""Shared HTTP transport helpers for AI provider requests."""

import json
import time
from typing import Any, Callable, Dict, Optional, Protocol

import requests

# 免代理豁免域名：国内网关走系统代理会被拖慢甚至连接失败，必须直连。
# qianwenaiapi.com 是实测在用的百炼兼容模式网关（token-plan.maas.qianwenaiapi.com）。
_DOMESTIC_NO_PROXY_DOMAINS = ("aliyuncs.com", "siliconflow", "qianwenaiapi.com")


class ChatProviderConfig(Protocol):
    provider_label: str
    api_key: Optional[str]
    credential_label: str
    chat_completions_url: Optional[str]


class AIProviderHTTPError(RuntimeError):
    """Raised when an AI provider returns a non-success HTTP response."""


def robust_request_post(url: str, **kwargs):
    """POST with a conservative proxy fallback.

    A response/read failure may happen after the provider has already started
    and billed the request, so non-idempotent POSTs are only retried for an
    explicit proxy failure or a connection-establishment timeout.
    """

    is_domestic = any(
        domain in url.lower() for domain in _DOMESTIC_NO_PROXY_DOMAINS
    )
    if is_domestic and "proxies" not in kwargs:
        kwargs["proxies"] = {"http": None, "https": None}

    try:
        return requests.post(url, **kwargs)
    except (
        requests.exceptions.ProxyError,
        requests.exceptions.ConnectTimeout,
    ) as exc:
        if kwargs.get("proxies") == {"http": None, "https": None}:
            raise
        print(
            f"[Robust Network] Provider connection failed before a response "
            f"(type={type(exc).__name__}). Retrying once without proxies..."
        )
        retry_kwargs = kwargs.copy()
        retry_kwargs["proxies"] = {"http": None, "https": None}
        return requests.post(url, **retry_kwargs)


def robust_request_get(url: str, **kwargs):
    """GET with the project's existing proxy-bypass retry behavior."""

    is_domestic = any(
        domain in url.lower() for domain in _DOMESTIC_NO_PROXY_DOMAINS
    )
    if is_domestic and "proxies" not in kwargs:
        kwargs["proxies"] = {"http": None, "https": None}

    try:
        return requests.get(url, **kwargs)
    except requests.exceptions.RequestException as exc:
        if kwargs.get("proxies") == {"http": None, "https": None}:
            raise
        print(
            f"[Robust Network] GET request to {url} failed: {str(exc)}. "
            "Retrying with proxies bypassed..."
        )
        retry_kwargs = kwargs.copy()
        retry_kwargs["proxies"] = {"http": None, "https": None}
        return requests.get(url, **retry_kwargs)


def build_bearer_headers(api_key: str) -> Dict[str, str]:
    """Build the common OpenAI-compatible authorization headers."""

    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


class AICancelled(RuntimeError):
    """Raised when a cooperative cancel check fires during a provider read."""


class _BufferedResponse:
    """Minimal response view returned by deadline-bounded provider reads."""

    def __init__(self, status_code: int, headers: Any, content: bytes) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._content = content

    @property
    def content(self) -> bytes:
        return self._content

    @property
    def text(self) -> str:
        return self._content.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self._content.decode("utf-8", errors="replace"))


def _post_bounded(
    url: str,
    request_kwargs: Dict[str, Any],
    *,
    deadline_seconds: Optional[float] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    read_timeout: Optional[float] = None,
):
    """POST with a hard wall-clock deadline and a cooperative cancel hook.

    ``requests`` timeouts are per socket operation: a provider that dribbles
    bytes slowly never trips them, so one call can pin an import worker for
    hours.  Reading in bounded chunks while watching both a deadline and a
    cancel flag guarantees the call always comes back.

    ``read_timeout`` 显式放宽「单次读等待」上限：默认压到 30s，便于取消尽快生效；
    但整卷拆题这类**非流式、一次性生成**的调用首字节可能要远超 30s，若仍按 30s
    掐断就会把正常的长响应误判成超时。此类调用传入 ``read_timeout`` 放宽即可。
    """

    kwargs = dict(request_kwargs)
    kwargs["stream"] = True
    requested = float(kwargs.get("timeout") or 60)
    short_read = min(requested, 30.0)
    if read_timeout:
        # 显式传入的 read_timeout 就是「单次读等待」上限，不再与调用方 timeout 取 min：
        # min 只能收紧不能放宽，timeout=180 + read_timeout=240 时 240 永远不生效，
        # 整卷拆题这类缓冲型响应会在首字节（TTFB）到达前被 180s 误杀（线上已复现）。
        # 连接上限仍取短值；慢速吐字与取消响应由 deadline_seconds 墙上时钟兜底。
        kwargs["timeout"] = (short_read, float(read_timeout))
    else:
        kwargs["timeout"] = short_read
    started = time.monotonic()
    deadline = started + deadline_seconds if deadline_seconds else None

    response = robust_request_post(url, **kwargs)
    chunks = []
    try:
        for chunk in response.iter_content(chunk_size=8192):
            if cancel_check is not None and cancel_check():
                raise AICancelled("任务已取消，AI 请求已中断。")
            if deadline is not None and time.monotonic() > deadline:
                raise requests.exceptions.ReadTimeout(
                    f"AI 响应超时：已超过整体截止时间 {deadline_seconds:.0f} 秒。"
                )
            if chunk:
                chunks.append(chunk)
    finally:
        response.close()
    return _BufferedResponse(
        response.status_code, response.headers, b"".join(chunks)
    )


def post_chat_completion(
    provider: ChatProviderConfig,
    payload: Dict[str, Any],
    *,
    timeout: float,
    stream: bool = False,
    check_status: bool = True,
    provider_name: Optional[str] = None,
    deadline_seconds: Optional[float] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    read_timeout: Optional[float] = None,
):
    """Send one OpenAI-compatible chat completion request.

    Payload construction and response parsing remain the responsibility of the
    calling business flow.  Streaming callers can disable status checking so
    they can preserve their existing SSE error format.

    ``deadline_seconds`` caps the *total* wall-clock time of a non-streaming
    call, and ``cancel_check`` is polled while reading so a cancelled task can
    break out of a slow provider response instead of blocking a worker.
    """

    if not provider.api_key:
        raise ValueError(f"未配置对应的 API Key ({provider.credential_label})")
    if not provider.chat_completions_url:
        raise ValueError(f"未配置对应的 API Base ({provider.provider_label})")

    request_kwargs = {
        "headers": build_bearer_headers(provider.api_key),
        "json": payload,
        "timeout": timeout,
    }
    if stream:
        request_kwargs["stream"] = True
        response = robust_request_post(
            provider.chat_completions_url,
            **request_kwargs,
        )
    elif deadline_seconds or cancel_check is not None:
        response = _post_bounded(
            provider.chat_completions_url,
            request_kwargs,
            deadline_seconds=deadline_seconds,
            cancel_check=cancel_check,
            read_timeout=read_timeout,
        )
    else:
        response = robust_request_post(
            provider.chat_completions_url,
            **request_kwargs,
        )
    if check_status and response.status_code != 200:
        display_name = provider_name or provider.provider_label
        # Provider bodies can echo prompt fragments or internal diagnostics.
        # Keep them out of browser-visible exceptions and logs; a request ID is
        # sufficient for support without exposing user content.
        request_id = ""
        headers = getattr(response, "headers", None)
        if headers:
            request_id = (
                headers.get("x-request-id")
                or headers.get("request-id")
                or headers.get("x-dashscope-request-id")
                or ""
            )
        suffix = f" (request_id={request_id})" if request_id else ""
        raise AIProviderHTTPError(
            f"{display_name} 接口错误: HTTP {response.status_code}{suffix}"
        )
    return response
