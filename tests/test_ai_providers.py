import pytest

from mathbank.ai_providers import (
    apply_bailian_thinking_policy,
    apply_deepseek_thinking_policy,
    build_chat_completions_url,
    build_models_url,
    inject_reasoning_effort,
    is_deepseek_thinking_capable,
    parse_model_and_effort,
    parse_model_ids,
    resolve_draw_provider,
    resolve_ocr_fallbacks,
    resolve_ocr_provider,
    resolve_provider_endpoint,
    resolve_text_provider,
)


@pytest.mark.parametrize(
    ("configured", "expected_model", "expected_effort"),
    [
        ("gpt-5.6-sol:high", "gpt-5.6-sol", "high"),
        ("gpt-5.6-sol(XHigh)", "gpt-5.6-sol", "xhigh"),
        (
            "ZHONGZHAN_GPT/gpt-5.6-sol:max",
            "ZHONGZHAN_GPT/gpt-5.6-sol",
            "max",
        ),
        ("  gpt-5.6-sol : LOW  ", "gpt-5.6-sol", "low"),
        ("gpt-5.6-sol(default)", "gpt-5.6-sol", "default"),
        ("deepseek-chat", "deepseek-chat", None),
        ("model:unsupported", "model:unsupported", None),
        ("", "", None),
        (None, "", None),
    ],
)
def test_parse_model_and_effort(configured, expected_model, expected_effort):
    assert parse_model_and_effort(configured) == (expected_model, expected_effort)


@pytest.mark.parametrize(
    ("configured", "provider_code", "key_env", "expected_base"),
    [
        (
            "SILICONFLOW/deepseek-ai/DeepSeek-V3",
            "siliconflow",
            "SILICONFLOW_API_KEY",
            "https://api.siliconflow.cn/v1",
        ),
        (
            "BAILIAN/qwen-max",
            "bailian",
            "ALI_BAILIAN_API_KEY",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        (
            "DEEPSEEK/deepseek-chat",
            "deepseek",
            "DEEPSEEK_API_KEY",
            "https://api.deepseek.com",
        ),
        (
            "ZHONGZHAN_GPT/gpt-5.6-sol",
            "zhongzhan_gpt",
            "ZHONGZHAN_GPT_API_KEY",
            "https://api.openai.com/v1",
        ),
        (
            "ZHONGZHAN_CLAUDE/claude-sonnet",
            "zhongzhan_claude",
            "ZHONGZHAN_CLAUDE_API_KEY",
            "https://api.openai.com/v1",
        ),
    ],
)
def test_explicit_text_providers_resolve_to_expected_defaults(
    configured, provider_code, key_env, expected_base
):
    config = resolve_text_provider(configured, {key_env: "secret-value"})

    assert config.provider_code == provider_code
    assert config.api_key == "secret-value"
    assert config.api_base == expected_base
    assert config.chat_completions_url == f"{expected_base}/chat/completions"
    assert key_env in config.credential_label
    assert "secret-value" not in repr(config)


def test_unprefixed_models_keep_provider_selection_without_rewriting_model_id():
    environment = {
        "ALI_BAILIAN_API_KEY": "bailian-key",
        "DEEPSEEK_API_KEY": "deepseek-key",
    }

    qwen = resolve_text_provider("qwen3.7-max", environment)
    deepseek = resolve_text_provider("deepseek-flash", environment)

    assert (qwen.provider_code, qwen.model_name, qwen.api_key) == (
        "bailian",
        "qwen3.7-max",
        "bailian-key",
    )
    assert (deepseek.provider_code, deepseek.model_name, deepseek.api_key) == (
        "deepseek",
        "deepseek-flash",
        "deepseek-key",
    )


def test_custom_base_and_reasoning_effort_are_resolved_together():
    config = resolve_text_provider(
        "ZHONGZHAN_GPT/gpt-5.6-sol:high",
        {
            "ZHONGZHAN_GPT_API_KEY": "transit-key",
            "ZHONGZHAN_GPT_BASE_URL": "https://transit.example/v1/",
        },
    )

    assert config.model_name == "gpt-5.6-sol"
    assert config.reasoning_effort == "high"
    assert config.chat_completions_url == "https://transit.example/v1/chat/completions"


@pytest.mark.parametrize(
    ("api_base", "expected"),
    [
        (None, None),
        ("", None),
        ("  ", None),
        ("https://transit.example/v1", "https://transit.example/v1/chat/completions"),
        ("https://transit.example/v1/", "https://transit.example/v1/chat/completions"),
        (
            "https://transit.example/v1/chat/completions",
            "https://transit.example/v1/chat/completions",
        ),
        (
            " https://transit.example/v1/chat/completions/ ",
            "https://transit.example/v1/chat/completions",
        ),
    ],
)
def test_chat_completions_url_is_normalized_once(api_base, expected):
    assert build_chat_completions_url(api_base) == expected


def test_text_provider_does_not_duplicate_complete_chat_endpoint():
    config = resolve_text_provider(
        "ZHONGZHAN_GPT/gpt-5.6-sol",
        {
            "ZHONGZHAN_GPT_API_KEY": "transit-key",
            "ZHONGZHAN_GPT_BASE_URL": (
                "https://transit.example/v1/chat/completions/"
            ),
        },
    )

    assert config.chat_completions_url == (
        "https://transit.example/v1/chat/completions"
    )


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_reasoning_effort_is_allowlisted_normalized_and_pure(effort):
    original = {"model": "gpt-5.6-sol", "messages": [], "temperature": 0.2}

    result = inject_reasoning_effort(original, effort.upper())

    assert result == {
        **original,
        "reasoning_effort": effort,
        "enable_thinking": True,
    }
    assert result is not original
    assert "reasoning_effort" not in original
    assert "enable_thinking" not in original


@pytest.mark.parametrize("effort", [None, "", "default", "unsupported", "high\n"])
def test_default_or_invalid_reasoning_effort_leaves_payload_unchanged(effort):
    original = {
        "model": "gpt-5.6-sol",
        "enable_thinking": False,
        "provider_option": "keep-me",
    }

    result = inject_reasoning_effort(original, effort)

    assert result == original
    assert result is not original


@pytest.mark.parametrize(
    "provider_code",
    ["deepseek", "siliconflow", "zhongzhan_gpt", "zhongzhan_claude"],
)
def test_bailian_thinking_policy_is_a_pure_noop_for_other_providers(provider_code):
    original = {
        "model": "provider-model",
        "max_tokens": 777,
        "reasoning_effort": "high",
        "enable_thinking": True,
    }

    result = apply_bailian_thinking_policy(
        original,
        provider_code=provider_code,
        model_name="qwen3.8-max",
        task="solve",
        thinking_enabled=False,
    )

    assert result == original
    assert result is not original


def test_bailian_thinking_policy_preserves_legacy_custom_models():
    original = {"model": "qwen-vl-plus", "max_tokens": 2048}

    result = apply_bailian_thinking_policy(
        original,
        provider_code="bailian",
        model_name="qwen-vl-plus",
        task="ocr",
    )

    assert result == original


@pytest.mark.parametrize(
    ("enabled", "expected_limit"),
    [(True, 32768), (False, 16384)],
)
def test_qwen37_solve_uses_bounded_thinking_budget(enabled, expected_limit):
    result = apply_bailian_thinking_policy(
        {
            "model": "qwen3.7-plus",
            "max_tokens": 8192,
            "reasoning_effort": "high",
        },
        provider_code="bailian",
        model_name="qwen3.7-plus",
        task="solve",
        thinking_enabled=enabled,
    )

    assert result["enable_thinking"] is enabled
    assert result["max_completion_tokens"] == expected_limit
    assert "max_tokens" not in result
    assert "reasoning_effort" not in result
    if enabled:
        assert result["thinking_budget"] == 16384
    else:
        assert "thinking_budget" not in result


def test_qwen38_draw_uses_medium_effort_without_thinking_budget():
    result = apply_bailian_thinking_policy(
        {"model": "qwen3.8-max", "thinking_budget": 99999},
        provider_code="bailian",
        model_name="qwen3.8-max",
        task="draw",
    )

    assert result["enable_thinking"] is True
    assert result["reasoning_effort"] == "medium"
    assert result["max_completion_tokens"] == 32768
    assert "thinking_budget" not in result


@pytest.mark.parametrize(
    "task",
    ["classify", "parse", "paper_selection", "latex_diagnostic"],
)
def test_bailian_structured_tasks_disable_thinking_and_old_token_cap(task):
    result = apply_bailian_thinking_policy(
        {
            "model": "qwen3.7-flash",
            "max_tokens": 512,
            "reasoning_effort": "high",
        },
        provider_code="bailian",
        model_name="qwen3.7-flash",
        task=task,
    )

    assert result["enable_thinking"] is False
    assert "max_tokens" not in result
    assert "reasoning_effort" not in result
    assert "thinking_budget" not in result


def test_bailian_ocr_disables_thinking_with_bounded_completion():
    result = apply_bailian_thinking_policy(
        {"model": "qwen3.7-flash"},
        provider_code="bailian",
        model_name="qwen3.7-flash",
        task="ocr",
    )

    assert result["enable_thinking"] is False
    assert result["max_completion_tokens"] == 16384


def test_bailian_model_id_is_preserved_after_effort_is_removed():
    config = resolve_text_provider(
        "BAILIAN/qwen3.7-max:high",
        {"ALI_BAILIAN_API_KEY": "key"},
    )

    assert config.model_name == "qwen3.7-max"
    assert config.reasoning_effort == "high"


def test_bailian_ocr_defaults_to_current_flash_model():
    config = resolve_ocr_provider(
        "bailian",
        {"ALI_BAILIAN_API_KEY": "key"},
    )

    assert config.model_name == "qwen3.7-flash"


def test_draw_provider_removes_reasoning_suffix_and_keeps_legacy_transit_env():
    config = resolve_draw_provider(
        "ZHONGZHAN/gpt-5.6-luna:high",
        {
            "ZHONGZHAN_API_KEY": "legacy-key",
            "ZHONGZHAN_BASE_URL": "https://draw.example/v1",
        },
    )

    assert config.provider_code == "zhongzhan_gpt"
    assert config.model_name == "gpt-5.6-luna"
    assert config.reasoning_effort == "high"
    assert config.api_key == "legacy-key"
    assert config.chat_completions_url == "https://draw.example/v1/chat/completions"


def test_unknown_explicit_prefix_is_not_silently_rerouted():
    config = resolve_text_provider(
        "UNKNOWN/model-name",
        {"DEEPSEEK_API_KEY": "must-not-be-used"},
    )

    assert config.provider_code == "unknown"
    assert config.api_key is None
    assert config.api_base is None
    assert config.model_name == "model-name"


def test_ocr_provider_resolves_legacy_transit_settings_and_effort():
    config = resolve_ocr_provider(
        "zhongzhan_gpt",
        {
            "ZHONGZHAN_API_KEY": "legacy-key",
            "ZHONGZHAN_BASE_URL": "https://legacy.example/v1/chat/completions",
            "ZHONGZHAN_OCR_MODEL": "gpt-5.6-luna:high",
        },
    )

    assert config.provider_code == "zhongzhan_gpt"
    assert config.api_key == "legacy-key"
    assert config.model_name == "gpt-5.6-luna"
    assert config.reasoning_effort == "high"
    assert config.chat_completions_url == (
        "https://legacy.example/v1/chat/completions"
    )


def test_pdf_ocr_fallbacks_respect_claude_preference():
    providers = resolve_ocr_fallbacks(
        "zhongzhan_claude",
        {
            "ZHONGZHAN_CLAUDE_API_KEY": "claude-key",
            "SILICONFLOW_API_KEY": "sf-key",
            "ALI_BAILIAN_API_KEY": "bailian-key",
            "ZHONGZHAN_GPT_API_KEY": "gpt-key",
        },
    )

    assert [provider.provider_code for provider in providers] == [
        "zhongzhan_claude",
        "siliconflow",
        "bailian",
        "zhongzhan_gpt",
    ]


def test_draw_provider_strips_siliconflow_prefix_and_detects_images():
    config = resolve_draw_provider(
        "SILICONFLOW/Qwen/Qwen3-VL-32B-Instruct",
        {"SILICONFLOW_API_KEY": "sf-key"},
    )

    assert config.provider_code == "siliconflow"
    assert config.model_name == "Qwen/Qwen3-VL-32B-Instruct"
    assert config.supports_image_input is True
    assert "sf-key" not in repr(config)


def test_draw_provider_keeps_text_only_siliconflow_mode():
    config = resolve_draw_provider(
        "Qwen/Qwen3.5-397B-A17B",
        {"SILICONFLOW_API_KEY": "sf-key"},
    )

    assert config.model_name == "Qwen/Qwen3.5-397B-A17B"
    assert config.supports_image_input is False


# --------------------------------------------------------------- DeepSeek 思考策略


def test_deepseek_thinking_capability_detection():
    # 当前主推模型与 V4 Pro 都接受 thinking 开关
    assert is_deepseek_thinking_capable(model_name="deepseek-flash")
    assert is_deepseek_thinking_capable(model_name="deepseek-v4-pro")
    assert is_deepseek_thinking_capable(
        model_name="deepseek-ai/DeepSeek-V4.1-Flash",
        api_base="https://api.siliconflow.cn/v1",
    )
    assert is_deepseek_thinking_capable(model_name="whatever", provider_code="deepseek")
    # 旧模型不接受该字段，必须放行不做处理
    assert not is_deepseek_thinking_capable(model_name="deepseek-chat")
    assert not is_deepseek_thinking_capable(model_name="deepseek-reasoner")
    # 非 DeepSeek 提供商不受影响
    assert not is_deepseek_thinking_capable(model_name="qwen3.7-max")
    assert not is_deepseek_thinking_capable(model_name="gpt-5.6-sol")


def test_deepseek_thinking_policy_disables_thinking_for_structured_tasks():
    payload = {
        "model": "deepseek-flash",
        "enable_thinking": True,
        "reasoning_effort": "high",
        "temperature": 0.2,
    }

    result = apply_deepseek_thinking_policy(
        payload, model_name="deepseek-flash", task="classify"
    )

    assert result["thinking"] == {"type": "disabled"}
    # 关思考时不该再带 effort；百炼专有字段必须被剥掉
    assert "reasoning_effort" not in result
    assert "enable_thinking" not in result
    assert result["temperature"] == 0.2
    assert payload["enable_thinking"] is True, "输入载荷不得被就地修改"


def test_deepseek_thinking_policy_enables_and_normalizes_effort():
    cases = [
        ("low", "low"),
        ("medium", "high"),
        ("high", "high"),
        ("xhigh", "high"),
        ("max", "max"),
    ]
    for requested, expected in cases:
        result = apply_deepseek_thinking_policy(
            {"model": "deepseek-flash", "reasoning_effort": requested},
            model_name="deepseek-flash",
            task="solve",
            thinking_enabled=True,
        )
        assert result["thinking"] == {"type": "enabled"}
        assert result["reasoning_effort"] == expected, requested

    # 未映射的取值宁可交给服务端默认，也不发出去
    unmapped = apply_deepseek_thinking_policy(
        {"model": "deepseek-flash", "reasoning_effort": "turbo"},
        model_name="deepseek-flash",
        task="solve",
        thinking_enabled=True,
    )
    assert "reasoning_effort" not in unmapped
    assert unmapped["thinking"] == {"type": "enabled"}


def test_deepseek_thinking_policy_keeps_legacy_models_and_other_providers_untouched():
    legacy = {"model": "deepseek-chat", "thinking": {"type": "disabled"}}
    assert (
        apply_deepseek_thinking_policy(
            legacy, model_name="deepseek-chat", task="classify"
        )
        == legacy
    )

    bailian = {"model": "qwen3.8-max", "enable_thinking": True}
    assert (
        apply_deepseek_thinking_policy(
            bailian,
            model_name="qwen3.8-max",
            api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
            provider_code="bailian",
            task="classify",
        )
        == bailian
    )


def test_deepseek_thinking_policy_defaults_by_task():
    # 解题/画图默认保留服务端思考（V4.1-Flash 默认开启，effort=high）
    solve = apply_deepseek_thinking_policy(
        {"model": "deepseek-flash"}, model_name="deepseek-flash", task="solve"
    )
    assert solve["thinking"] == {"type": "enabled"}

    # 解析/打标默认关闭，避免批量任务被 high effort 拖慢
    parse = apply_deepseek_thinking_policy(
        {"model": "deepseek-flash", "reasoning_effort": "high"},
        model_name="deepseek-flash",
        task="parse",
    )
    assert parse["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in parse


def test_draw_provider_routes_deepseek_to_the_official_endpoint():
    """画图任务的提供商下拉里也有 DeepSeek，不能误发到硅基流动。"""

    env = {
        "DEEPSEEK_API_KEY": "ds-key",
        "SILICONFLOW_API_KEY": "sf-key",
    }

    unprefixed = resolve_draw_provider("deepseek-flash", env)
    assert unprefixed.provider_code == "deepseek"
    assert unprefixed.api_base == "https://api.deepseek.com"
    assert unprefixed.api_key == "ds-key"
    assert unprefixed.model_name == "deepseek-flash"
    # V4.1-Flash 原生支持视觉，可作为画图模型
    assert unprefixed.supports_image_input is True

    prefixed = resolve_draw_provider("DEEPSEEK/deepseek-flash:high", env)
    assert prefixed.provider_code == "deepseek"
    assert prefixed.model_name == "deepseek-flash"
    assert prefixed.reasoning_effort == "high"

    # V4 Pro 没有视觉能力，不能当画图模型用
    pro = resolve_draw_provider("deepseek-v4-pro", env)
    assert pro.provider_code == "deepseek"
    assert pro.supports_image_input is False

    # 无前缀的非 deepseek 模型仍回落到硅基流动
    fallback = resolve_draw_provider("Qwen/Qwen3-VL-32B-Instruct", env)
    assert fallback.provider_code == "siliconflow"


# --------------------------------------------------------------- 官网模型列表


def test_build_models_url_normalizes_base_variants():
    assert build_models_url("https://api.deepseek.com") == "https://api.deepseek.com/models"
    assert build_models_url("https://api.deepseek.com/") == "https://api.deepseek.com/models"
    assert (
        build_models_url("https://api.deepseek.com/chat/completions")
        == "https://api.deepseek.com/models"
    )
    assert build_models_url("https://transit.example/v1") == "https://transit.example/v1/models"
    assert build_models_url("") is None
    assert build_models_url(None) is None


def test_parse_model_ids_accepts_gateway_variants():
    assert parse_model_ids({"data": [{"id": "a"}, {"id": "b"}, {"id": "a"}]}) == ["a", "b"]
    assert parse_model_ids({"models": [{"name": "m1"}, "m2"]}) == ["m1", "m2"]
    assert parse_model_ids(["x", "y"]) == ["x", "y"]
    assert parse_model_ids({}) == []
    assert parse_model_ids("nope") == []


def test_resolve_provider_endpoint_maps_settings_values():
    env = {
        "DEEPSEEK_API_KEY": "ds",
        "ZHONGZHAN_GPT_BASE_URL": "https://t.example/v1",
    }
    assert resolve_provider_endpoint("deepseek", env) == (
        "deepseek",
        "DeepSeek",
        "https://api.deepseek.com",
        "ds",
    )

    # 兼容别名与历史写法
    code, label, base, key = resolve_provider_endpoint("zhongzhan", env)
    assert (code, label, base) == ("zhongzhan_gpt", "中转站 A", "https://t.example/v1")
    assert not key  # 未配置时为空字符串，端点据此返回配置错误

    assert resolve_provider_endpoint("ali_bailian", env)[0] == "bailian"
    assert resolve_provider_endpoint("bogus", env) == (None, None, None, None)
