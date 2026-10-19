# -*- coding: utf-8 -*-
"""B 档：R1 快路径 schema 兼容与静默回退（走 routes_paper，mock AI，绝不联网、绝不重烧整卷）。"""

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import main  # noqa: F401  先完整加载 main，避免 routes_paper 循环导入
from mathbank import pdf_local_split, routes_paper

GOOD = """一、选择题
1. 已知集合 $A=\\{x|x^2<4\\}$，则 $A$ 的范围是
A．$(-2,2)$
B．$(0,2)$
C．$(-2,0)$
D．$(2,4)$
【答案】A
【解析】由 $x^2<4$ 得 $-2<x<2$。
2. 若 $z=1+i$，则模 $|z|$ 等于下面哪个
A．$1$  B．$\\sqrt{2}$  C．$2$  D．$2\\sqrt{2}$
【答案】B
3. 下列函数中与 $y=x$ 表示同一函数的是哪个
A．$y=\\sqrt{x^2}$  B．$y=(\\sqrt{x})^2$  C．$y=\\dfrac{x^2}{x}$  D．$y=\\sqrt[3]{x^3}$
【答案】D
二、填空题
4. 已知 $\\sin\\alpha=\\dfrac35$，则 $\\tan\\alpha$ 等于多少 ____。
【答案】$-\\dfrac34$
三、解答题
5. 已知函数 $f(x)=\\sin x+\\cos x$，求其最值。
（1）求最小正周期；
（2）求最大值。
【答案】（1）$2\\pi$；（2）$\\sqrt2$。
"""

_CLS = {
    "question_type": "single_choice",
    "category_compulsory": "必修一",
    "category_chapter": "1. 集合",
    "category_knowledge": "集合的基本运算",
    "topic": "集合表示",
    "difficulty": "L2",
    "source": "",
    "paper_question_no": "1",
}


def _stub_ai(payload_json, locks, task_id, deadline_seconds, cancel_check):
    payload = json.loads(payload_json)
    by_id = {}
    for b in payload.get("clean_blocks", []):
        by_id[b["block_id"]] = dict(_CLS)
    for b in payload.get("split_blocks", []):
        by_id[b["block_id"]] = {
            "block_id": b["block_id"], "action": "classify", **_CLS
        }  # 故意让 split 块分类失败（action!=split）→ 走本地原文兜底
    return by_id, {"prompt_tokens": 123, "completion_tokens": 45, "total_tokens": 168}


def test_fast_path_keeps_content_verbatim_and_fills_classification():
    plan = pdf_local_split.plan_pdf_local_split(GOOD)
    assert plan.trustworthy is True
    n_blocks = len(plan.clean_blocks)

    with patch.dict(os.environ, {"PDF_FASTPATH_ENABLED": "1"}), \
         patch.object(routes_paper, "_call_pdf_metadata_ai", side_effect=_stub_ai), \
         patch("main.parse_paper_text_internal") as full_split:
        diag = {}
        qs = routes_paper.parse_pdf_with_fast_path(
            GOOD, page_texts=None, generate_answers=False,
            split_info=None, task_id="t-b1", diagnostics_out=diag,
        )

    full_split.assert_not_called()
    assert len(qs) == n_blocks
    for q in qs:
        assert q["content"].strip()  # 本地正文
        assert q["content"] in GOOD  # 逐字来自原卷 = 没有整卷重打
        assert q["category_chapter"] == "1. 集合"
        assert q["question_type"]
    assert diag["trustworthy"] is True
    assert diag["fallback_level"] in ("none", "L0_microsplit")
    assert diag["ai_usage"]["completion_tokens"] == 45


def test_fast_path_output_flows_through_post_process_without_error():
    with patch.dict(os.environ, {"PDF_FASTPATH_ENABLED": "1"}), \
         patch.object(routes_paper, "_call_pdf_metadata_ai", side_effect=_stub_ai):
        qs = routes_paper.parse_pdf_with_fast_path(
            GOOD, None, False, None, "t-b2", {}
        )
    out = main.post_process_pdf_parsed_questions(
        qs, "测试卷", task_id=None, ocr_results=None,
        source_text=GOOD, generate_answers_bool=False,
    )
    assert out and all(q.get("content") for q in out)
    # 答案前缀规整应生效（原卷答案被保留、不凭空生成）
    assert any("【答案】" in (q.get("answer_markdown") or "") for q in out)


def test_generate_answers_forces_full_split_fallback():
    with patch.dict(os.environ, {"PDF_FASTPATH_ENABLED": "1"}), \
         patch.object(routes_paper, "_call_pdf_metadata_ai") as ai, \
         patch("main.parse_paper_text_internal", return_value=[{"content": "x"}]) as full_split:
        qs = routes_paper.parse_pdf_with_fast_path(
            GOOD, None, True, None, "t-b3", {}
        )
    ai.assert_not_called()
    full_split.assert_called_once()
    assert qs == [{"content": "x"}]


def test_untrusted_paper_falls_back_without_ai():
    bad = "1. 一题 $x$\n3. 三题 $y$\n2. 二题 $z$\n1. 甲题\n2. 乙题\n"
    with patch.dict(os.environ, {"PDF_FASTPATH_ENABLED": "1"}), \
         patch.object(routes_paper, "_call_pdf_metadata_ai") as ai, \
         patch("main.parse_paper_text_internal", return_value=[{"content": "full"}]) as full_split:
        diag = {}
        qs = routes_paper.parse_pdf_with_fast_path(bad, None, False, None, "t-b4", diag)
    ai.assert_not_called()
    full_split.assert_called_once()
    assert qs == [{"content": "full"}]
    assert diag.get("fallback_level") == "L1_fullpaper"


def test_kill_switch_off_uses_full_split_no_ai():
    with patch.dict(os.environ, {"PDF_FASTPATH_ENABLED": "0"}), \
         patch.object(routes_paper, "_call_pdf_metadata_ai") as ai, \
         patch("main.parse_paper_text_internal", return_value=[{"content": "full"}]) as full_split:
        qs = routes_paper.parse_pdf_with_fast_path(GOOD, None, False, None, "t-b5", {})
    ai.assert_not_called()
    full_split.assert_called_once()


def test_ai_failure_degrades_clean_without_reburn():
    def boom(*a, **k):
        raise RuntimeError("AI 超时/坏 JSON")
    with patch.dict(os.environ, {"PDF_FASTPATH_ENABLED": "1"}), \
         patch.object(routes_paper, "_call_pdf_metadata_ai", side_effect=boom), \
         patch("main.parse_paper_text_internal") as full_split:
        diag = {}
        qs = routes_paper.parse_pdf_with_fast_path(GOOD, None, False, None, "t-b6", diag)
    full_split.assert_not_called()  # 关键：绝不重打整卷
    assert diag["fallback_level"] == "L2_degrade"
    assert diag["rebill"] is False
    plan = pdf_local_split.plan_pdf_local_split(GOOD)
    assert len(qs) == len(plan.all_blocks)
    for q in qs:
        assert q["content"] in GOOD
        assert "待refine" in q.get("review", "")


def test_split_block_r2_lock_restore_and_merge():
    """可疑块经 AI 微拆并用锁 id 回公式 → 还原成真实 LaTeX 后入库。"""
    # 造一个含可疑块的卷：题1正常，题2塞奇数$→G6可疑，其余干净；总保留率≥0.6 仍可信
    src = (
        "1. 这是第一道题 $x+1$\nA．1  B．2  C．3  D．4\n【答案】A\n"
        "2. 这是第二道题 $x+1$\n【答案】B\n"
        "3. 这是第三道题正文足够长\n【答案】C\n"
        "4. 这是第四道题正文足够长\n【答案】D\n"
        "5. 这是第五道题正文足够长\n【答案】E\n"
    )
    plan = pdf_local_split.plan_pdf_local_split(src, generate_answers=False)
    # 题2 有奇数 $（被 _ANSWER_HEAD 切分后 content 可能含奇$）；不强依赖具体块，直接跑快路径
    if not plan.trustworthy:
        pytest.skip("该合成样本裁判未达可信，跳过 R2 专项")

    captured = {}

    def stub(payload_json, locks, task_id, deadline_seconds, cancel_check):
        payload = json.loads(payload_json)
        captured["locks"] = locks
        by_id = {}
        for b in payload.get("clean_blocks", []):
            by_id[b["block_id"]] = dict(_CLS)
        for b in payload.get("split_blocks", []):
            lock_ids = b.get("locks", [])
            content = "微拆后正文 " + (" ".join(lock_ids) if lock_ids else "")
            by_id[b["block_id"]] = {
                "block_id": b["block_id"], "action": "split",
                "questions": [{
                    "content": content, "answer_markdown": "",
                    "question_type": "detailed_answer", "referenced_images": [],
                    **{k: v for k, v in _CLS.items() if k != "question_type"},
                }],
            }
        return by_id, {"completion_tokens": 10}

    with patch.dict(os.environ, {"PDF_FASTPATH_ENABLED": "1"}), \
         patch.object(routes_paper, "_call_pdf_metadata_ai", side_effect=stub), \
         patch("main.parse_paper_text_internal") as full_split:
        qs = routes_paper.parse_pdf_with_fast_path(src, None, False, None, "t-b7", {})
    full_split.assert_not_called()
    assert qs  # 至少产出一批题目
    # 若有 split 块，其锁应被还原（不再有裸 <mathbank-math> 残留）
    for q in qs:
        assert "<mathbank" not in (q.get("content") or "")


def test_fastpath_is_disabled_by_default():
    """默认必须关：暗启动期内，没有显式开启就不许走快路径。"""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("PDF_FASTPATH_ENABLED", None)
        assert routes_paper._pdf_fastpath_enabled() is False


def test_fastpath_enabled_only_when_explicitly_set():
    with patch.dict(os.environ, {"PDF_FASTPATH_ENABLED": "1"}):
        assert routes_paper._pdf_fastpath_enabled() is True
    with patch.dict(os.environ, {"PDF_FASTPATH_ENABLED": "0"}):
        assert routes_paper._pdf_fastpath_enabled() is False


def test_bailian_metadata_call_disables_thinking_and_keeps_json_mode():
    """分类调用必须显式 enable_thinking=False —— 本轮唯一的实质行为修复，此前零覆盖。

    qwen3.8-flash 不在 apply_bailian_thinking_policy 的白名单里，默认带 5000+ 推理 token，
    单卷 130s 顶穿 deadline 变成静默 L2 降级（不报错、只是慢且分类为空）。
    这里故意给出 reasoning_effort，让 inject_reasoning_effort 先把 enable_thinking 置 True，
    这样一旦那两行覆盖被重构掉，本锁立刻变红。
    """
    plan = pdf_local_split.plan_pdf_local_split(GOOD)
    user_payload, split_locks = routes_paper._build_metadata_envelope(plan, GOOD, "t-think")

    captured = {}
    items = {
        "items": [
            {"block_id": b.block_id, "action": "classify", **_CLS}
            for b in list(plan.clean_blocks) + list(plan.suspicious_blocks)
        ]
    }
    response = MagicMock()
    response.json.return_value = {
        "choices": [{"message": {"content": json.dumps(items, ensure_ascii=False)}}],
        "usage": {"prompt_tokens": 900, "completion_tokens": 1813, "total_tokens": 2713},
    }

    def fake_post(provider, data, **kwargs):
        captured["data"] = data
        captured["kwargs"] = kwargs
        return response

    bailian = SimpleNamespace(
        api_key="test-key",
        api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model_name="qwen3.8-flash",
        provider_label="Bailian",
        credential_label="Bailian Key",
        reasoning_effort="high",
        provider_code="bailian",
    )
    with patch.object(routes_paper._main, "resolve_text_provider", return_value=bailian), \
         patch.object(routes_paper._main, "post_chat_completion", side_effect=fake_post), \
         patch.object(routes_paper._main, "get_current_curriculum", return_value={"必修一": {"1. 集合": []}}), \
         patch.object(routes_paper, "get_metadata_cache", return_value={}):
        by_id, usage = routes_paper._call_pdf_metadata_ai(
            user_payload, split_locks, "t-think", 60, lambda: False
        )

    data = captured["data"]
    assert data.get("enable_thinking") is False
    assert data["response_format"] == {"type": "json_object"}
    assert captured["kwargs"]["read_timeout"] == 60  # 缓冲型 JSON 响应不能被 30s 单次读误杀
    assert by_id and usage["completion_tokens"] == 1813


def test_strict_mode_makes_ai_failure_raisable_not_a_usable_result():
    """PDF_FASTPATH_STRICT=1：快路径 AI 异常必须抛出，既不 L2 降级也不 L1 重烧。"""
    def boom(*a, **k):
        raise TimeoutError("AI 超时")

    with patch.dict(os.environ, {"PDF_FASTPATH_ENABLED": "1", "PDF_FASTPATH_STRICT": "1"}), \
         patch.object(routes_paper, "_call_pdf_metadata_ai", side_effect=boom), \
         patch("main.parse_paper_text_internal") as full_split:
        diag = {}
        with pytest.raises(TimeoutError):
            routes_paper.parse_pdf_with_fast_path(GOOD, None, False, None, "t-s1", diag)
    full_split.assert_not_called()
    assert diag["ai_ok"] is False


def test_default_mode_still_degrades_but_records_error_class():
    """strict 未开（默认 0）时仍走 L2 保正文降级，但必须留下可判红的 ai_ok / ai_error_class。"""
    class _FakeReadTimeout(Exception):
        """模拟 requests.ReadTimeout：str() 里没有类名，只有 ai_error_class 能判读。"""

    def boom(*a, **k):
        raise _FakeReadTimeout("timed out")

    with patch.dict(os.environ, {"PDF_FASTPATH_ENABLED": "1", "PDF_FASTPATH_STRICT": "0"}), \
         patch.object(routes_paper, "_call_pdf_metadata_ai", side_effect=boom), \
         patch("main.parse_paper_text_internal") as full_split:
        diag = {}
        qs = routes_paper.parse_pdf_with_fast_path(GOOD, None, False, None, "t-s2", diag)
    full_split.assert_not_called()  # 关着 strict 也绝不重烧整卷
    assert qs
    assert diag["fallback_level"] == "L2_degrade"
    assert diag["ai_ok"] is False
    assert diag["ai_error_class"] == "_FakeReadTimeout"
    assert diag["degrade_reason"].startswith("_FakeReadTimeout:")
