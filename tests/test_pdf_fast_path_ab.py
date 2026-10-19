# -*- coding: utf-8 -*-
"""C 档：真实联网 A/B —— 整卷老路 vs R1 快路径（默认跳过，需 PDF_AB_RUN=1）。

会真的调用大模型、消耗额度，只在 PDF_AB_RUN=1 时运行，永不参与常规全量回归。
每卷、每次调用都独立 try/except 并即时打印，单点超时/失败不中断整体。
老路为拿到真实输出 token，镜像 parse_paper_text_internal 的请求并把读超时放宽到 420s
（生产里默认 180s 读超时，扫描卷整卷重打经常直接超时——这本身就是要展示的痛点）。

运行：$env:PDF_AB_RUN="1"; venv\\Scripts\\python.exe -m pytest tests/test_pdf_fast_path_ab.py -s -q
"""
import os
import time
from collections import Counter

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("PDF_AB_RUN") != "1",
    reason="C 档 A/B 需显式 PDF_AB_RUN=1（会调用真实 AI、消耗额度）",
)

import main  # noqa: E402  先加载 main，避免 routes_paper 循环导入
from mathbank import mineru_client, pdf_local_split, routes_paper
from mathbank.ai_http import post_chat_completion as REAL_POST_CHAT
from mathbank.ai_providers import (apply_bailian_thinking_policy,
                                   apply_deepseek_thinking_policy, inject_reasoning_effort)
from mathbank.ai_json import parse_ai_json
from mathbank.curriculums import get_current_curriculum
from mathbank.metadata import get_metadata_cache
from mathbank.prompts import build_pdf_parse_system_prompt

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CACHE = os.path.join(_ROOT, ".system_generated", "mineru_cache")
PICKS = [
    ("c41d7c9ad75f4788", "纯题干·无答案"),
    ("19100d7a0b2e53a5", "带答案·选择题卷"),
    ("122054c38da333e9", "大庆13页·大题+插图"),
]
_OLD_READ_TIMEOUT = 420


def _provider():
    model = os.getenv("PREFER_PARSE_MODEL") or os.getenv("DEEPSEEK_PARSE_MODEL", "deepseek-flash")
    return routes_paper._main.resolve_text_provider(model)


def _old_path_measure(text):
    """复刻 parse_paper_text_internal 的整卷请求，仅放宽读超时以拿全量 token/耗时。"""
    provider = _provider()
    system = build_pdf_parse_system_prompt(
        get_current_curriculum(), False, difficulties=get_metadata_cache().get("difficulties"))
    data = {
        "model": provider.model_name,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": text}],
        "response_format": {"type": "json_object"}, "temperature": 0.2, "max_tokens": 65536,
    }
    data = inject_reasoning_effort(data, provider.reasoning_effort)
    data = apply_deepseek_thinking_policy(data, model_name=provider.model_name, api_base=provider.api_base,
                                          provider_code=provider.provider_code, task="parse", thinking_enabled=False)
    data = apply_bailian_thinking_policy(data, provider_code=provider.provider_code,
                                         model_name=provider.model_name, task="parse")
    t0 = time.perf_counter()
    resp = REAL_POST_CHAT(provider, data, timeout=_OLD_READ_TIMEOUT, provider_name=provider.provider_label)
    dt = round(time.perf_counter() - t0, 2)
    js = resp.json()
    usage = js.get("usage") or {}
    raw = js["choices"][0]["message"]["content"].strip()
    parsed = parse_ai_json(raw, raw_markdown=text)
    if isinstance(parsed, dict):
        qs = parsed.get("questions") or parsed.get("data") or []
    else:
        qs = parsed if isinstance(parsed, list) else []
    return qs, dt, usage, len(raw)


def _load(frag):
    d = next((p for p in os.listdir(_CACHE) if p.startswith(frag)), None)
    assert d, f"未找到缓存卷 {frag}"
    wdir = os.path.join(_CACHE, d)
    text = open(os.path.join(wdir, "full.md"), encoding="utf-8", errors="replace").read()
    return text, mineru_client.collect_page_texts(wdir)


def _dist(qs):
    return dict(Counter((q.get("question_type") or "?") for q in qs)) if qs else {}


def _img(qs):
    return sum(len(q.get("referenced_images") or []) for q in qs) if qs else 0


def _row(label, chars, nblocks, old, fast, diag):
    oq, ow, ou = old
    fq, fw, fu = fast
    ot = f"{ou.get('completion_tokens','?')}/{fu.get('completion_tokens','?')}"
    it = f"{ou.get('prompt_tokens','?')}/{fu.get('prompt_tokens','?')}"
    print(f"{label:<16} {chars:>6}字 {nblocks:>3}题 {fw if fw is not None else '?':>7}s {ow if ow is not None else '?':>7}s "
          f"{ot:>12} {it:>12} {_img(fq)}/{_img(oq):<4} {diag.get('fallback_level','?')}")


def test_pdf_fast_path_ab():
    provider = _provider()
    if not provider.api_key:
        pytest.skip(f"未配置 {provider.credential_label}，无法跑真实 A/B")
    print(f"\n模型={provider.model_name} provider={provider.provider_label}  读超时(老路)={_OLD_READ_TIMEOUT}s")
    print("label | chars | blocks | 快路s | 老路s | OutTok(老/快) | InTok(老/快) | 插图快/老 | 回退级别")
    print("-" * 90)
    for frag, label in PICKS:
        text, page_texts = _load(frag)
        nblocks = len(pdf_local_split.plan_pdf_local_split(text).all_blocks)

        # 老路（仅在 PDF_AB_OLD=1 时才真跑，重测快路可省时间/额度）
        if os.getenv("PDF_AB_OLD") == "1":
            try:
                oq, ow, ou, _ = _old_path_measure(text)
            except Exception as exc:
                oq, ow, ou = None, "T/O", {"prompt_tokens": None, "completion_tokens": "T/O"}
                print(f"[{label}] 老路失败: {type(exc).__name__}: {str(exc)[:80]}")
        else:
            oq, ow, ou = None, "-", {"prompt_tokens": "-", "completion_tokens": "-"}

        # 快路
        diag = {}
        try:
            t0 = time.perf_counter()
            saved = os.environ.get("PDF_FASTPATH_ENABLED")
            os.environ["PDF_FASTPATH_ENABLED"] = "1"
            try:
                fq = routes_paper.parse_pdf_with_fast_path(text, page_texts, False, None, f"ab-{frag}", diag)
            finally:
                if saved is None:
                    os.environ.pop("PDF_FASTPATH_ENABLED", None)
                else:
                    os.environ["PDF_FASTPATH_ENABLED"] = saved
            fw = round(time.perf_counter() - t0, 2)
            fu = diag.get("ai_usage") or {"prompt_tokens": "?", "completion_tokens": "?"}
        except Exception as exc:
            fq, fw, fu = None, "ERR", {}
            print(f"[{label}] 快路失败: {type(exc).__name__}: {str(exc)[:80]}")

        _row(label, len(text), nblocks, (oq, ow, ou), (fq, fw, fu), diag)
        print(f"      DIAG: trusted={diag.get('trustworthy')} level={diag.get('fallback_level')} "
              f"local={diag.get('local_blocks')} susp={diag.get('suspicious_blocks')} "
              f"ai_ms={diag.get('ai_ms')} usage={diag.get('ai_usage')} degrade={diag.get('degrade_reason','')}")
        if fq is not None and oq is not None:
            print(f"      题型 快={_dist(fq)} 老={_dist(oq)} | trusted={diag.get('trustworthy')} in_chars≈{len(text)}")
        elif fq is not None:
            print(f"      题型 快={_dist(fq)} 老=TIMEOUT | trusted={diag.get('trustworthy')}")
