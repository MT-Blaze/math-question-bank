#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""Word 导入基线量测（任务二 · 第一步）。

按**线上原样链路**跑每份 .docx：
    extract_docx_markdown → lock_visible_math → parse_paper_text_internal
    → restore_visible_math → post_process_pdf_parsed_questions
记录：各阶段墙钟耗时、大模型请求的真实 usage（prompt/completion/total tokens，
提供方不回传 usage 时按字符估算兜底）、本地提取诊断计数（公式转换/疑点/图片）。

两种模式：
  * 缺省（不花钱）：只跑「本地提取 + 公式锁 + 拆题提示词体积估算」，**服务在跑也可执行**
    （全部依赖走 mathbank.* 原始定义，不碰 runtime 锁）。
  * ``--with-ai``：复刻 run_docx_parsing_task 的真实拆题与后处理。这条链经
    ``mathbank.parsing_pipeline``（它 `from main import ...`，导入期抢 runtime 锁），
    **必须先完全关闭题库服务**，否则会给出明确提示。

用法::

    python -m scripts.measure_word_import_baseline A.docx B.docx          # 本地提取基线
    python -m scripts.measure_word_import_baseline --with-ai A.docx       # 全链路（有 API 费用，需停服务）

报告 JSON 落在 output/word_baseline_<时间戳>.json。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

from mathbank.content_locks import lock_visible_math, restore_visible_math
from mathbank.docx_helper import extract_docx_markdown
from mathbank.paths import IS_TESTING, PROJECT_ROOT, TEST_UPLOADS_DIR, UPLOADS_DIR
from mathbank.prompts import build_pdf_parse_system_prompt

# 取值与 main.py:317-320/629 逐字一致（刻意不 import main，见 docstring）。
UPLOAD_DIR = str(TEST_UPLOADS_DIR if IS_TESTING else UPLOADS_DIR)
UPLOAD_DIR_REL = "static/test_uploads" if IS_TESTING else "static/uploads"
TMP_UPLOAD_DIR = os.path.join(UPLOAD_DIR, "tmp")

OUT_DIR = PROJECT_ROOT / "output"


def _estimate_tokens(text: str) -> int:
    """粗略 token 估算：CJK 每字≈1 token，其余每 4 字符≈1 token。仅作 usage 缺失兜底。"""
    cjk = sum(1 for ch in text
              if "\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f" or "\uff00" <= ch <= "\uffef")
    return int(cjk + (len(text) - cjk) / 4)


def _sec(t0: float) -> float:
    return round(time.perf_counter() - t0, 3)


def _load_parse_stack():
    """--with-ai 才加载生产拆题链（import main 会抢 runtime 锁）。"""
    try:
        import main as app
        from mathbank import parsing_pipeline
        return app, parsing_pipeline
    except Exception as exc:
        if "runtime" in str(exc).lower() or "RuntimeLock" in type(exc).__name__ or "题库服务" in str(exc):
            raise SystemExit(
                "❌ 题库服务仍在运行：--with-ai 走的是与线上完全相同的拆题链（含 runtime 锁）。\n"
                "   请先完全退出题库服务，再重跑本命令。"
            ) from exc
        raise


def measure_one(path: Path, with_ai: bool, generate_answers: bool, asset_tag: str) -> dict:
    result: dict = {"file": str(path), "size_bytes": path.stat().st_size}
    file_bytes = path.read_bytes()

    # 阶段 1：本地提取（docx → markdown + 图片资产 + 诊断）
    t0 = time.perf_counter()
    docx_res = extract_docx_markdown(
        file_bytes,
        output_dir=TMP_UPLOAD_DIR,
        url_prefix="/%s/tmp" % UPLOAD_DIR_REL,
        asset_prefix="word_baseline_%s" % asset_tag,
    )
    result["extract_seconds"] = _sec(t0)
    if not docx_res.get("success") or not docx_res.get("markdown"):
        result["error"] = docx_res.get("error") or "提取失败"
        return result
    full_markdown = docx_res["markdown"]
    diagnostics = docx_res.get("diagnostics", {})
    result["markdown_chars"] = len(full_markdown)
    result["image_count"] = docx_res.get("image_count", 0)
    result["extract_diagnostics"] = {
        key: diagnostics.get(key)
        for key in (
            "omml_converted", "omml_unsupported", "mtef_converted", "mtef_fallback_images",
            "mtef_unavailable", "images_extracted", "review_required", "numbering_converted",
            "numbering_unavailable", "symbols_unavailable", "tables_review_required",
        )
        if key in diagnostics
    }
    result["extract_warnings"] = list(diagnostics.get("warnings", []))[:8]

    # 阶段 2：公式锁（模型可见但不可改写，回库时按 ID 恢复原文）
    t0 = time.perf_counter()
    locked_content, math_locks = lock_visible_math(full_markdown, asset_tag[:16])
    result["lock_seconds"] = _sec(t0)
    result["math_locks_created"] = len(math_locks)

    # 阶段 3：拆题提示词体积（不花钱即可给出输入规模估算）
    from mathbank.curriculums import get_current_curriculum
    from mathbank.metadata import get_metadata_cache
    system_instructions = build_pdf_parse_system_prompt(
        get_current_curriculum(),
        generate_answers,
        difficulties=get_metadata_cache().get("difficulties"),
    )
    result["ai_input"] = {
        "system_chars": len(system_instructions),
        "user_chars": len(locked_content),
        "est_prompt_tokens": _estimate_tokens(system_instructions) + _estimate_tokens(locked_content),
    }

    if not with_ai:
        result["note"] = "未加 --with-ai：拆题模型调用与后处理未量测（服务停止后可跑全链路）。"
        return result

    # 阶段 4：真实拆题（与线上同栈；spy 捕获 usage 与延迟）
    app, parsing_pipeline = _load_parse_stack()
    ai_calls: list = []
    original_post = parsing_pipeline.post_chat_completion

    def spy(provider, payload, **kwargs):
        t0 = time.perf_counter()
        response = original_post(provider, payload, **kwargs)
        entry = {
            "model": payload.get("model"),
            "latency_seconds": _sec(t0),
            "max_tokens": payload.get("max_tokens"),
            "request_chars": sum(len(str(m.get("content", ""))) for m in payload.get("messages", [])),
        }
        try:
            usage = response.json().get("usage") or {}
            entry["usage"] = {
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
            }
        except Exception:
            entry["usage"] = None
        ai_calls.append(entry)
        return response

    parsing_pipeline.post_chat_completion = spy
    try:
        t0 = time.perf_counter()
        parsed = parsing_pipeline.parse_paper_text_internal(locked_content, generate_answers)
        result["ai_total_seconds"] = _sec(t0)
    finally:
        parsing_pipeline.post_chat_completion = original_post
    result["ai_calls"] = ai_calls

    # 阶段 5：公式还原 + 结构化后处理（与 routes_paper.run_docx_parsing_task 同参）
    t0 = time.perf_counter()
    lock_report = restore_visible_math(parsed, math_locks)
    result["restore_seconds"] = _sec(t0)
    result["lock_report"] = {k: v for k, v in lock_report.items() if not isinstance(v, list)}

    t0 = time.perf_counter()
    final_questions = parsing_pipeline.post_process_pdf_parsed_questions(
        parsed, path.stem, "baseline-" + asset_tag, [full_markdown], locked_content,
    )
    result["post_process_seconds"] = _sec(t0)
    result["questions_out"] = len(final_questions)
    result["questions_with_answer"] = sum(
        1 for q in final_questions if (q.get("answer_markdown") or "").strip()
    )
    result["wall_seconds_total"] = round(
        result["extract_seconds"] + result["lock_seconds"] + result["ai_total_seconds"]
        + result["restore_seconds"] + result["post_process_seconds"], 3,
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Word 导入基线量测")
    parser.add_argument("files", nargs="+", help=".docx 文件路径")
    parser.add_argument("--with-ai", action="store_true", help="实际调用拆题模型（有 API 费用，需先停服务）")
    parser.add_argument("--generate-answers", action="store_true", help="勾选自动生成答案（默认关）")
    args = parser.parse_args()

    stamp = time.strftime("%Y%m%d%H%M%S")
    reports = []
    for raw in args.files:
        path = Path(raw)
        print("=" * 62)
        print("📐 量测 %s" % path.name)
        if not path.exists():
            reports.append({"file": str(path), "error": "文件不存在"})
            print("  ❌ 文件不存在")
            continue
        try:
            rep = measure_one(path, args.with_ai, args.generate_answers, stamp)
        except SystemExit:
            raise
        except Exception as exc:
            print("  ❌ 异常：%s" % exc)
            traceback.print_exc()
            rep = {"file": str(path), "error": "%s: %s" % (type(exc).__name__, exc)}
        reports.append(rep)
        for key in ("extract_seconds", "lock_seconds", "ai_total_seconds",
                    "restore_seconds", "post_process_seconds", "wall_seconds_total"):
            if key in rep:
                print("  %-21s %s s" % (key, rep[key]))
        if "markdown_chars" in rep:
            print("  提取：%d 字符，图片 %d，公式锁 %d，诊断 %s"
                  % (rep["markdown_chars"], rep.get("image_count", 0),
                     rep.get("math_locks_created", 0), rep.get("extract_diagnostics", {})))
        if "ai_input" in rep:
            print("  拆题输入规模：%d 字符（估算 %d tokens）"
                  % (rep["ai_input"]["system_chars"] + rep["ai_input"]["user_chars"],
                     rep["ai_input"]["est_prompt_tokens"]))
        for i, call in enumerate(rep.get("ai_calls", []), 1):
            print("  第 %d 次模型调用：%s s，model=%s，usage=%s"
                  % (i, call["latency_seconds"], call.get("model"), call.get("usage")))
        if "questions_out" in rep:
            print("  出卡：%d 道（含原版答案 %d 道）"
                  % (rep["questions_out"], rep["questions_with_answer"]))
        if rep.get("error"):
            print("  ⚠️ %s" % rep["error"])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / ("word_baseline_%s.json" % stamp)
    out_path.write_text(
        json.dumps({"with_ai": args.with_ai, "reports": reports}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("\n📝 基线报告已写入： %s" % out_path)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
