"""Pure helpers for resolving text-model provider configuration.

This module deliberately does not send HTTP requests.  It only turns the model
value stored by the settings UI into a provider, credential, endpoint, clean
model name, and optional reasoning effort.
"""

from dataclasses import dataclass, field
import json
import os
import re
from typing import Any, Dict, List, Mapping, Optional, Tuple


_VALID_REASONING_EFFORTS = frozenset(
    {"high", "medium", "low", "xhigh", "max", "default"}
)


def build_chat_completions_url(api_base: Optional[str]) -> Optional[str]:
    """Normalize an OpenAI-compatible base URL to its chat endpoint."""

    normalized_base = str(api_base or "").strip().rstrip("/")
    if not normalized_base:
        return None
    if normalized_base.lower().endswith("/chat/completions"):
        return normalized_base
    return f"{normalized_base}/chat/completions"


def build_models_url(api_base: Optional[str]) -> Optional[str]:
    """Normalize an OpenAI-compatible base URL to its ``/models`` endpoint."""

    normalized_base = str(api_base or "").strip().rstrip("/")
    if not normalized_base:
        return None
    lowered = normalized_base.lower()
    for suffix in ("/chat/completions", "/completions", "/responses"):
        if lowered.endswith(suffix):
            normalized_base = normalized_base[: -len(suffix)]
            break
    return f"{normalized_base}/models"


def parse_model_ids(payload: Any) -> List[str]:
    """Extract model ids from an OpenAI-compatible ``/models`` response.

    Gateways are not perfectly uniform: some return ``{"data": [...]}``, others
    ``{"models": [...]}`` or a bare list. Entries may be objects (``id`` /
    ``name`` / ``model``) or plain strings. Order is preserved and duplicates
    are dropped.
    """

    entries: Any = []
    if isinstance(payload, Mapping):
        for key in ("data", "models", "model_list", "result"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                entries = candidate
                break
    elif isinstance(payload, list):
        entries = payload

    model_ids: List[str] = []
    for entry in entries:
        if isinstance(entry, Mapping):
            raw = entry.get("id") or entry.get("name") or entry.get("model")
        else:
            raw = entry
        text = str(raw or "").strip()
        if text and text not in model_ids:
            model_ids.append(text)
    return model_ids


# Settings UI provider values mapped onto the provider specs above.  ``zhongzhan``
# and ``ali_bailian``/``ali`` are accepted as historical aliases.
_PROVIDER_ALIASES = {
    "deepseek": "DEEPSEEK",
    "siliconflow": "SILICONFLOW",
    "bailian": "BAILIAN",
    "ali": "BAILIAN",
    "ali_bailian": "BAILIAN",
    "zhongzhan": "ZHONGZHAN_GPT",
    "zhongzhan_gpt": "ZHONGZHAN_GPT",
    "zhongzhan_claude": "ZHONGZHAN_CLAUDE",
}


def resolve_provider_endpoint(
    provider: str,
    environ: Optional[Mapping[str, str]] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Resolve a settings provider value into ``(code, label, api_base, api_key)``.

    Returns ``(None, None, None, None)`` for an unknown provider so callers can
    answer with a configuration error instead of guessing a vendor.
    """

    environment = os.environ if environ is None else environ
    provider = str(provider or "").strip().lower()
    # 自定义厂商（cp_<slug>）通过外层 custom map 解析，不改动内置 _PROVIDER_SPECS。
    if provider.startswith("cp_"):
        slug = provider[len("cp_"):].upper()
        custom = _load_custom_providers(environment)
        cp = custom.get(slug)
        if cp:
            return (
                "cp_" + cp["slug"].lower(),
                cp["name"],
                cp["base_url"] or None,
                cp["api_key"] or None,
            )
        return None, None, None, None
    spec_key = _PROVIDER_ALIASES.get(provider)
    if spec_key is None:
        return None, None, None, None
    spec = _PROVIDER_SPECS[spec_key]

    api_base = (
        _env_base_url(environment, spec.api_base_env, spec.default_api_base)
        if spec.api_base_env
        else spec.default_api_base
    )
    api_key = environment.get(spec.api_key_env)
    if spec_key == "ZHONGZHAN_GPT":
        api_key = _first_configured(
            environment, "ZHONGZHAN_GPT_API_KEY", "ZHONGZHAN_API_KEY"
        )
        api_base = _first_configured(
            environment,
            "ZHONGZHAN_GPT_BASE_URL",
            "ZHONGZHAN_BASE_URL",
            default=spec.default_api_base,
        )
    return spec.code, spec.label, api_base, api_key


def _slugify(name: str) -> str:
    """把任意厂商名压成稳定大写字母数字 slug（如 TokenPlan → TOKENPLAN）。

    用于生成不会与内置前缀冲突的自定义厂商模型前缀 ``CP_<SLUG>``。
    """

    return re.sub(r"[^A-Z0-9]", "", str(name or "").upper())


def _load_custom_providers(
    environ: Optional[Mapping[str, str]] = None,
) -> Dict[str, Dict[str, str]]:
    """从 CUSTOM_PROVIDERS_JSON 环境变量加载自定义 OpenAI 兼容厂商。

    返回以 slug 为键的字典；解析失败或为空时返回 {}。该映射在每次调用时
    重新读取（save_settings 后会即时更新 os.environ），因此无需额外缓存。
    """

    environment = os.environ if environ is None else environ
    raw = environment.get("CUSTOM_PROVIDERS_JSON", "") or ""
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(parsed, list):
        return {}
    result: Dict[str, Dict[str, str]] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        slug = _slugify(name)
        if not slug:
            continue
        result[slug] = {
            "name": name,
            "slug": slug,
            "base_url": str(item.get("base_url") or "").strip(),
            "api_key": str(item.get("api_key") or "").strip(),
            "model": str(item.get("model") or "").strip(),
        }
    return result


def inject_reasoning_effort(
    payload: Mapping[str, Any], reasoning_effort: Optional[str]
) -> Dict[str, Any]:
    """Return a payload copy with one allowlisted reasoning effort applied.

    ``default`` and invalid values deliberately leave provider-specific payload
    behavior untouched.  The input mapping is never mutated.
    """

    result = dict(payload)
    # Parsed efforts are already trimmed.  Do not silently normalize surrounding
    # whitespace here: control characters must never reach an outbound payload.
    normalized_effort = str(reasoning_effort or "").lower()
    if (
        normalized_effort not in _VALID_REASONING_EFFORTS
        or normalized_effort == "default"
    ):
        return result
    result["reasoning_effort"] = normalized_effort
    result["enable_thinking"] = True
    return result


_BAILIAN_NO_THINKING_TASKS = frozenset(
    {"classify", "parse", "paper_selection", "latex_diagnostic"}
)

# DeepSeek V4-generation models (``deepseek-flash`` = V4.1-Flash, ``deepseek-v4-pro``)
# expose the OpenAI-style ``thinking`` toggle plus ``reasoning_effort``.  The
# legacy ``deepseek-chat`` / ``deepseek-reasoner`` entries do not accept the
# toggle, so they must be left untouched.
_DEEPSEEK_LEGACY_MODELS = frozenset({"deepseek-chat", "deepseek-reasoner"})

# DeepSeek maps every requested effort onto low / high / max.
_DEEPSEEK_EFFORT_ALIASES = {
    "minimal": "low",
    "low": "low",
    "medium": "high",
    "high": "high",
    "xhigh": "high",
    "max": "max",
    "ultra": "max",
}

# Structured / low-latency tasks keep thinking off; V4.1-Flash enables it by
# default with effort ``high``, which is expensive for bulk classification.
_DEEPSEEK_NO_THINKING_TASKS = frozenset(
    {"classify", "parse", "paper_selection", "latex_diagnostic", "ocr"}
)


def is_deepseek_thinking_capable(
    *,
    model_name: str,
    api_base: str = "",
    provider_code: str = "",
) -> bool:
    """Return True when the model accepts DeepSeek's ``thinking`` parameter."""

    normalized_model = str(model_name or "").strip().lower()
    normalized_base = str(api_base or "").strip().lower()
    normalized_code = str(provider_code or "").strip().lower()
    mentions_deepseek = (
        "deepseek" in normalized_model
        or "deepseek" in normalized_base
        or normalized_code == "deepseek"
    )
    if not mentions_deepseek:
        return False
    return normalized_model not in _DEEPSEEK_LEGACY_MODELS


def apply_deepseek_thinking_policy(
    payload: Mapping[str, Any],
    *,
    model_name: str,
    api_base: str = "",
    provider_code: str = "",
    task: str,
    thinking_enabled: Optional[bool] = None,
) -> Dict[str, Any]:
    """Apply DeepSeek's current thinking contract to one payload copy.

    DeepSeek V4-generation models are controlled by ``{"thinking": {"type":
    "enabled"|"disabled"}}`` and ``reasoning_effort`` (low / high / max).
    ``enable_thinking`` is a Bailian-only field and is stripped here so it can
    never leak into a DeepSeek request.  Other providers and the legacy
    ``deepseek-chat`` / ``deepseek-reasoner`` models are returned unchanged.
    """

    result = dict(payload)
    if not is_deepseek_thinking_capable(
        model_name=model_name, api_base=api_base, provider_code=provider_code
    ):
        return result

    result.pop("enable_thinking", None)

    if thinking_enabled is None:
        enabled = str(task or "").strip().lower() not in _DEEPSEEK_NO_THINKING_TASKS
    else:
        enabled = bool(thinking_enabled)

    result["thinking"] = {"type": "enabled" if enabled else "disabled"}

    requested_effort = result.get("reasoning_effort")
    if not enabled:
        result.pop("reasoning_effort", None)
        return result
    if requested_effort is None:
        return result
    normalized_effort = _DEEPSEEK_EFFORT_ALIASES.get(
        str(requested_effort).strip().lower()
    )
    if normalized_effort:
        result["reasoning_effort"] = normalized_effort
    else:
        # An unmapped value would be rejected upstream; let the server default.
        result.pop("reasoning_effort", None)
    return result


def apply_bailian_thinking_policy(
    payload: Mapping[str, Any],
    *,
    provider_code: str,
    model_name: str,
    task: str,
    thinking_enabled: Optional[bool] = None,
) -> Dict[str, Any]:
    """Apply task-specific reasoning parameters only to current Bailian Qwen.

    Other providers and legacy/custom Bailian models are returned unchanged so
    this policy cannot alter DeepSeek, SiliconFlow, transit, or saved legacy
    behavior. Qwen3.7 uses ``thinking_budget`` while Qwen3.8 Max uses
    ``reasoning_effort``; the two controls are never sent together.
    """

    result = dict(payload)
    normalized_provider = str(provider_code or "").strip().lower()
    normalized_model = str(model_name or "").strip().lower()
    is_qwen37 = normalized_model.startswith("qwen3.7-")
    is_qwen38_max = normalized_model == "qwen3.8-max" or normalized_model.startswith(
        "qwen3.8-max-"
    )
    if normalized_provider != "bailian" or not (is_qwen37 or is_qwen38_max):
        return result

    normalized_task = str(task or "").strip().lower()
    result.pop("reasoning_effort", None)
    result.pop("thinking_budget", None)
    result.pop("max_completion_tokens", None)
    # Current Qwen APIs deprecate max_tokens; structured JSON tasks should not
    # impose the old answer-only cap because it can truncate valid JSON.
    result.pop("max_tokens", None)

    if normalized_task == "ocr":
        result["enable_thinking"] = False
        result["max_completion_tokens"] = 16384
        return result

    if normalized_task in _BAILIAN_NO_THINKING_TASKS:
        result["enable_thinking"] = False
        return result

    if normalized_task == "solve":
        enabled = bool(thinking_enabled)
        result["enable_thinking"] = enabled
        result["max_completion_tokens"] = 32768 if enabled else 16384
        if enabled:
            if is_qwen38_max:
                result["reasoning_effort"] = "medium"
            else:
                result["thinking_budget"] = 16384
        return result

    if normalized_task == "draw":
        result["enable_thinking"] = True
        if is_qwen38_max:
            # Medium effort maps to a 16K reasoning budget, so reserve another
            # 16K for the emitted TikZ source itself.
            result["reasoning_effort"] = "medium"
            result["max_completion_tokens"] = 32768
        else:
            result["thinking_budget"] = 8192
            result["max_completion_tokens"] = 16384
        return result

    raise ValueError(f"未知的百炼思考策略任务: {task}")


@dataclass(frozen=True)
class TextProviderConfig:
    """Resolved configuration for one OpenAI-compatible text provider."""

    provider_code: str
    provider_label: str
    api_key_env: str
    api_key: Optional[str] = field(repr=False)
    api_base: Optional[str]
    model_name: str
    reasoning_effort: Optional[str]
    raw_model: str

    @property
    def credential_label(self) -> str:
        """Human-readable provider name used by configuration errors."""

        if not self.api_key_env:
            return self.provider_label
        return f"{self.provider_label} ({self.api_key_env})"

    @property
    def chat_completions_url(self) -> Optional[str]:
        """Return the provider's OpenAI-compatible chat completion URL."""

        return build_chat_completions_url(self.api_base)


@dataclass(frozen=True)
class MultimodalProviderConfig:
    """Resolved configuration for OCR and TikZ image-aware requests."""

    provider_code: str
    provider_label: str
    api_key_env: str
    api_key: Optional[str] = field(repr=False)
    api_base: str
    model_name: str
    reasoning_effort: Optional[str]
    supports_image_input: bool
    raw_config: str

    @property
    def credential_label(self) -> str:
        return f"{self.provider_label} ({self.api_key_env})"

    @property
    def chat_completions_url(self) -> Optional[str]:
        return build_chat_completions_url(self.api_base)


@dataclass(frozen=True)
class _ProviderSpec:
    code: str
    label: str
    api_key_env: str
    api_base_env: Optional[str]
    default_api_base: str


_PROVIDER_SPECS = {
    "SILICONFLOW": _ProviderSpec(
        code="siliconflow",
        label="硅基流动",
        api_key_env="SILICONFLOW_API_KEY",
        api_base_env="SILICONFLOW_API_BASE",
        default_api_base="https://api.siliconflow.cn/v1",
    ),
    "BAILIAN": _ProviderSpec(
        code="bailian",
        label="阿里百炼",
        api_key_env="ALI_BAILIAN_API_KEY",
        api_base_env="ALI_BAILIAN_API_BASE",
        default_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
    ),
    "DEEPSEEK": _ProviderSpec(
        code="deepseek",
        label="DeepSeek",
        api_key_env="DEEPSEEK_API_KEY",
        api_base_env="DEEPSEEK_API_BASE",
        default_api_base="https://api.deepseek.com",
    ),
    "ZHONGZHAN_GPT": _ProviderSpec(
        code="zhongzhan_gpt",
        label="中转站 A",
        api_key_env="ZHONGZHAN_GPT_API_KEY",
        api_base_env="ZHONGZHAN_GPT_BASE_URL",
        default_api_base="https://api.openai.com/v1",
    ),
    "ZHONGZHAN_CLAUDE": _ProviderSpec(
        code="zhongzhan_claude",
        label="中转站 B",
        api_key_env="ZHONGZHAN_CLAUDE_API_KEY",
        api_base_env="ZHONGZHAN_CLAUDE_BASE_URL",
        default_api_base="https://api.openai.com/v1",
    ),
}


def parse_model_and_effort(model_str: str) -> Tuple[str, Optional[str]]:
    """Split a model setting into its clean name and reasoning effort."""

    if not model_str:
        return "", None

    normalized = str(model_str).strip()
    match_paren = re.search(
        r"^(.*?)\((high|medium|low|xhigh|max|default)\)$",
        normalized,
        re.IGNORECASE,
    )
    if match_paren:
        return match_paren.group(1).strip(), match_paren.group(2).lower()

    if ":" in normalized:
        model_name, potential_effort = normalized.rsplit(":", 1)
        potential_effort = potential_effort.strip().lower()
        if potential_effort in _VALID_REASONING_EFFORTS:
            return model_name.strip(), potential_effort

    return normalized, None


def resolve_text_provider(
    model_config: str,
    environ: Optional[Mapping[str, str]] = None,
) -> TextProviderConfig:
    """Resolve a saved model setting without performing any side effects."""

    environment = os.environ if environ is None else environ
    raw_model = str(model_config or "").strip()
    prefix = None
    model_name = raw_model
    if "/" in raw_model:
        prefix, model_name = raw_model.split("/", 1)
        prefix = prefix.upper()
        # 自定义厂商优先匹配：模型前缀 CP_<SLUG> 命中即走外层 custom map，
        # 不改动内置 frozen 的 _PROVIDER_SPECS。
        if prefix.startswith("CP_"):
            slug = prefix[len("CP_"):]
            custom = _load_custom_providers(environment)
            cp = custom.get(slug)
            if cp:
                model_name, reasoning_effort = parse_model_and_effort(model_name)
                return TextProviderConfig(
                    provider_code="cp_" + cp["slug"].lower(),
                    provider_label=cp["name"],
                    api_key_env="",
                    api_key=cp["api_key"] or None,
                    api_base=cp["base_url"] or None,
                    model_name=model_name or cp["model"] or "",
                    reasoning_effort=reasoning_effort,
                    raw_model=raw_model,
                )
        spec = _PROVIDER_SPECS.get(prefix)
        if spec is None:
            # Preserve the previous route behavior for an unknown explicit
            # prefix: report an unconfigured provider instead of guessing.
            return TextProviderConfig(
                provider_code="unknown",
                provider_label="DeepSeek",
                api_key_env="",
                api_key=None,
                api_base=None,
                model_name=model_name,
                reasoning_effort=None,
                raw_model=raw_model,
            )
    elif "qwen" in raw_model.lower():
        spec = _PROVIDER_SPECS["BAILIAN"]
    else:
        spec = _PROVIDER_SPECS["DEEPSEEK"]

    model_name, reasoning_effort = parse_model_and_effort(model_name)

    api_base = spec.default_api_base
    if spec.api_base_env:
        api_base = _env_base_url(environment, spec.api_base_env, spec.default_api_base)

    return TextProviderConfig(
        provider_code=spec.code,
        provider_label=spec.label,
        api_key_env=spec.api_key_env,
        api_key=environment.get(spec.api_key_env),
        api_base=api_base,
        model_name=model_name,
        reasoning_effort=reasoning_effort,
        raw_model=raw_model,
    )


def _first_configured(
    environment: Mapping[str, str], *names: str, default: str = ""
) -> str:
    for name in names:
        value = environment.get(name)
        if value:
            return value
    return default


def _env_base_url(
    environment: Mapping[str, str], env_name: str, default: str
) -> str:
    """读取接口地址环境变量，空值（含空串/全空白）视为未配置并回退默认。

    历史问题：`.env` 可能把 ``XXX_API_BASE=`` 写成空串，``os.environ`` 中该
    键存在但值为空。``environment.get(env_name, default)`` 会返回空串而非默认，
    导致内置厂商（DeepSeek/硅基流动/百炼）的接口地址被清空、模型拉取与解析失败。
    中转站厂商已用 ``_first_configured`` 规避；此处统一让其它内置厂商也具备
    “空值=未配置”的回退语义。
    """

    value = environment.get(env_name)
    if value and str(value).strip():
        return str(value).strip()
    return default


def resolve_ocr_provider(
    engine: str,
    environ: Optional[Mapping[str, str]] = None,
) -> MultimodalProviderConfig:
    """Resolve one OCR engine and its engine-specific model setting."""

    environment = os.environ if environ is None else environ
    normalized_engine = str(engine or "siliconflow").strip().lower()

    if normalized_engine in {"ali_bailian", "bailian"}:
        return MultimodalProviderConfig(
            provider_code="bailian",
            provider_label="阿里云百炼",
            api_key_env="ALI_BAILIAN_API_KEY",
            api_key=environment.get("ALI_BAILIAN_API_KEY"),
            # 优先用用户在设置页自定义的接口地址（ALI_BAILIAN_API_BASE），
            # 未设置（含空串）时回退到兼容模式默认地址。
            api_base=_env_base_url(
                environment,
                "ALI_BAILIAN_API_BASE",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
            model_name=environment.get("ALI_BAILIAN_OCR_MODEL", "qwen3.7-flash"),
            reasoning_effort=None,
            supports_image_input=True,
            raw_config=normalized_engine,
        )

    if normalized_engine == "zhongzhan_claude":
        model_name, reasoning_effort = parse_model_and_effort(
            environment.get("ZHONGZHAN_CLAUDE_OCR_MODEL", "claude-3-5-sonnet")
        )
        return MultimodalProviderConfig(
            provider_code="zhongzhan_claude",
            provider_label="中转站 B",
            api_key_env="ZHONGZHAN_CLAUDE_API_KEY",
            api_key=environment.get("ZHONGZHAN_CLAUDE_API_KEY"),
            api_base=_env_base_url(
                environment, "ZHONGZHAN_CLAUDE_BASE_URL", "https://api.openai.com/v1"
            ),
            model_name=model_name,
            reasoning_effort=reasoning_effort,
            supports_image_input=True,
            raw_config=normalized_engine,
        )

    if normalized_engine in {"zhongzhan", "zhongzhan_gpt"}:
        model_name, reasoning_effort = parse_model_and_effort(
            _first_configured(
                environment,
                "ZHONGZHAN_GPT_OCR_MODEL",
                "ZHONGZHAN_OCR_MODEL",
                default="gpt-4o",
            )
        )
        return MultimodalProviderConfig(
            provider_code="zhongzhan_gpt",
            provider_label="中转站 A",
            api_key_env="ZHONGZHAN_GPT_API_KEY",
            api_key=_first_configured(
                environment, "ZHONGZHAN_GPT_API_KEY", "ZHONGZHAN_API_KEY"
            ),
            api_base=_first_configured(
                environment,
                "ZHONGZHAN_GPT_BASE_URL",
                "ZHONGZHAN_BASE_URL",
                default="https://api.openai.com/v1",
            ),
            model_name=model_name,
            reasoning_effort=reasoning_effort,
            supports_image_input=True,
            raw_config=normalized_engine,
        )

    return MultimodalProviderConfig(
        provider_code="siliconflow",
        provider_label="SiliconFlow",
        api_key_env="SILICONFLOW_API_KEY",
        api_key=environment.get("SILICONFLOW_API_KEY"),
        api_base=_env_base_url(
            environment, "SILICONFLOW_API_BASE", "https://api.siliconflow.cn/v1"
        ),
        model_name=environment.get(
            "SILICONFLOW_OCR_MODEL", "Qwen/Qwen3-VL-8B-Instruct"
        ),
        reasoning_effort=None,
        supports_image_input=True,
        raw_config=normalized_engine,
    )


def resolve_ocr_fallbacks(
    preferred_engine: str,
    environ: Optional[Mapping[str, str]] = None,
) -> List[MultimodalProviderConfig]:
    """Return configured PDF OCR providers in deterministic fallback order."""

    environment = os.environ if environ is None else environ
    preferred = str(preferred_engine or "siliconflow").strip().lower()
    if preferred == "siliconflow":
        engine_order = ["siliconflow", "ali_bailian", "zhongzhan_gpt"]
    elif preferred in {"ali_bailian", "bailian"}:
        engine_order = ["ali_bailian", "siliconflow", "zhongzhan_gpt"]
    elif preferred == "zhongzhan_claude":
        engine_order = [
            "zhongzhan_claude",
            "siliconflow",
            "ali_bailian",
            "zhongzhan_gpt",
        ]
    else:
        engine_order = ["zhongzhan_gpt", "siliconflow", "ali_bailian"]

    providers = [resolve_ocr_provider(name, environment) for name in engine_order]
    return [provider for provider in providers if provider.api_key and provider.api_key.strip()]


def resolve_draw_provider(
    model_config: str,
    environ: Optional[Mapping[str, str]] = None,
) -> MultimodalProviderConfig:
    """Resolve the configured TikZ drawing/correction provider."""

    environment = os.environ if environ is None else environ
    raw_config = str(model_config or "Qwen/Qwen3-VL-32B-Instruct").strip()

    prefix, separator, configured_model = raw_config.partition("/")
    normalized_prefix = prefix.upper() if separator else ""
    if normalized_prefix == "ZHONGZHAN":
        normalized_prefix = "ZHONGZHAN_GPT"
    known_prefixes = {
        "BAILIAN",
        "SILICONFLOW",
        "DEEPSEEK",
        "ZHONGZHAN_GPT",
        "ZHONGZHAN_CLAUDE",
    }
    if normalized_prefix == "DEEPSEEK":
        spec = _PROVIDER_SPECS["DEEPSEEK"]
        model_name = configured_model
    elif not normalized_prefix and raw_config.lower().startswith("deepseek"):
        # 与文本模型一致：不带前缀的 deepseek* 视为官方 DeepSeek，而不是硅基流动。
        spec = _PROVIDER_SPECS["DEEPSEEK"]
        model_name = raw_config
    elif normalized_prefix in known_prefixes:
        spec = _PROVIDER_SPECS[normalized_prefix]
        model_name = configured_model
    else:
        spec = _PROVIDER_SPECS["SILICONFLOW"]
        model_name = raw_config

    model_name, reasoning_effort = parse_model_and_effort(model_name)
    key_env = spec.api_key_env
    api_key = environment.get(key_env)
    api_base = (
        _env_base_url(environment, spec.api_base_env, spec.default_api_base)
        if spec.api_base_env
        else spec.default_api_base
    )
    if normalized_prefix == "ZHONGZHAN_GPT":
        api_key = _first_configured(
            environment, "ZHONGZHAN_GPT_API_KEY", "ZHONGZHAN_API_KEY"
        )
        api_base = _first_configured(
            environment,
            "ZHONGZHAN_GPT_BASE_URL",
            "ZHONGZHAN_BASE_URL",
            default=spec.default_api_base,
        )

    force_multimodal = normalized_prefix in {
        "BAILIAN",
        "ZHONGZHAN_GPT",
        "ZHONGZHAN_CLAUDE",
    }

    # DeepSeek V4.1-Flash 原生支持视觉；deepseek-v4-pro 不支持图片输入。
    deepseek_vision = spec.code == "deepseek" and "flash" in model_name.lower()
    supports_image_input = (
        force_multimodal
        or deepseek_vision
        or any(marker in model_name.lower() for marker in ("vl", "thinking"))
    )
    return MultimodalProviderConfig(
        provider_code=spec.code,
        provider_label=spec.label,
        api_key_env=key_env,
        api_key=api_key,
        api_base=api_base,
        model_name=model_name,
        reasoning_effort=reasoning_effort,
        supports_image_input=supports_image_input,
        raw_config=raw_config,
    )
