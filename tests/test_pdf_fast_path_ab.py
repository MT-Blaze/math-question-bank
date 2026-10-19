# -*- coding: utf-8 -*-
"""C 档：真实联网 A/B —— 整卷老路 vs R1 快路径（默认跳过，需 PDF_AB_RUN=1）。

会真的调用大模型、消耗额度，只在 PDF_AB_RUN=1 时运行，永不参与常规全量回归。

老路直接调用生产真身 main.parse_paper_text_internal（不镜像、不放宽超时）。
因此老路的 token 用量不进 diagnostics，A/B 只比对墙钟、题数与题型分布。

判定全部是硬 fail（任一判据不满足即 pytest.fail，不打印了事、不 continue 后静默）；
每卷每臂一行落盘 outputs/ab_<YYYYmmdd_HHMMSS>/rows.jsonl，首行为 meta 报告头。

运行：$env:PDF_AB_RUN="1"; venv\\Scripts\\python.exe -m pytest tests/test_pdf_fast_path_ab.py -s -q
变量：PDF_AB_ALL=1 枚举全部缓存卷（默认只跑 PICKS 三卷冒烟）；PDF_AB_OLD=0 跳过老路（默认跑）。
"""
import json
import os
import subprocess
import time
from collections import Counter
from datetime import datetime
from urllib.parse import urlsplit

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("PDF_AB_RUN") != "1",
    reason="C 档 A/B 需显式 PDF_AB_RUN=1（会调用真实 AI、消耗额度）",
)

import main  # noqa: E402  先加载 main，避免 routes_paper 循环导入
from mathbank import mineru_client, parsing_pipeline, pdf_local_split, routes_paper  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CACHE = os.path.join(_ROOT, ".system_generated", "mineru_cache")
# 默认冒烟集只跑 3 卷（不烧额度）；PDF_AB_ALL=1 时改为枚举全部缓存卷
PICKS = [
    ("c41d7c9ad75f4788", "纯题干·无答案"),
    ("19100d7a0b2e53a5", "带答案·选择题卷"),
    ("122054c38da333e9", "大庆13页·大题+插图"),
]
# 只有这两档说明「快路径真的被调用且没有整卷回退」；L1_* / L2_degrade 一律判红
_OK_FALLBACK_LEVELS = ("none", "L0_microsplit")


def _provider():
    model = os.getenv("PREFER_PARSE_MODEL") or os.getenv("DEEPSEEK_PARSE_MODEL", "deepseek-flash")
    return routes_paper._main.resolve_text_provider(model)


def _old_path_measure(text, task_id):
    """老路 = 生产真身 parse_paper_text_internal，不镜像、不放宽超时。

    task_id 只用于日志归类：A/B 不登记文档任务，cancel_check 恒为 False；
    deadline / read 超时全部引用生产常量，harness 无权私自放宽。
    """
    print(f"[legacy] {task_id} 调用生产真身（deadline={routes_paper.AI_SPLIT_DEADLINE_SECONDS}s）")
    t0 = time.perf_counter()
    qs = main.parse_paper_text_internal(
        text,
        generate_answers_bool=False,
        deadline_seconds=routes_paper.AI_SPLIT_DEADLINE_SECONDS,
        cancel_check=lambda: False,
        preserve_extracted_answers=False,
    )
    dt = round(time.perf_counter() - t0, 2)
    return qs, dt


def _cache_dirs():
    """枚举带非空 full.md 的缓存卷目录（全量卷不硬编码清单）。"""
    if not os.path.isdir(_CACHE):
        return []
    names = []
    for name in sorted(os.listdir(_CACHE)):
        try:
            if os.path.getsize(os.path.join(_CACHE, name, "full.md")) > 0:
                names.append(name)
        except OSError:
            continue
    return names


def _papers():
    """[(frag, label, dir_name)]；PDF_AB_ALL=1 时 label 用目录名前 16 位。"""
    dirs = _cache_dirs()
    if os.getenv("PDF_AB_ALL") == "1":
        return [(d[:16], d[:16], d) for d in dirs]
    picks = []
    for frag, label in PICKS:
        hit = [d for d in dirs if d.startswith(frag)]
        if not hit:
            pytest.fail(f"未找到缓存卷 {frag}（{label}），A/B 无法开始")
        picks.append((frag, label, hit[0]))
    return picks


def _load(dir_name):
    wdir = os.path.join(_CACHE, dir_name)
    with open(os.path.join(wdir, "full.md"), encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    return text, mineru_client.collect_page_texts(wdir)


def _dist(qs):
    return dict(Counter((q.get("question_type") or "?") for q in qs)) if qs else {}


def _img(qs):
    return sum(len(q.get("referenced_images") or []) for q in qs) if qs else 0


def _n_unclassified(qs):
    """题型为空的题数：clean 块正文本地原样保留，题型只能来自 AI 分类。"""
    return sum(1 for q in (qs or []) if not str(q.get("question_type") or "").strip())


def _review_tagged(qs):
    """「元数据待refine」标记数（Block.to_parsed_question 分类缺失时写入）。"""
    return sum(1 for q in (qs or []) if "元数据待refine" in str(q.get("review") or ""))


def _host(url):
    """报告头只留 api_base 的域名，绝不写 key 或完整 URL。"""
    raw = str(url or "").strip()
    if raw and "//" not in raw:
        raw = "https://" + raw
    return urlsplit(raw).hostname or ""


def _git_head():
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_ROOT, capture_output=True, text=True, timeout=10,
        )
        return (out.stdout or "").strip() or "unknown"
    except Exception as exc:  # 取不到 HEAD 不能拖垮整轮 A/B
        return f"unknown({type(exc).__name__})"


def _production_constants():
    """A/B 引用的生产超时（读超时未在 routes_paper 导出时回取 parsing_pipeline）。"""
    read_timeout = getattr(routes_paper, "AI_SPLIT_READ_TIMEOUT_SECONDS", None)
    read_source = "routes_paper"
    if read_timeout is None:
        read_timeout = getattr(parsing_pipeline, "AI_SPLIT_READ_TIMEOUT_SECONDS", None)
        read_source = "parsing_pipeline"
    return {
        "AI_SPLIT_DEADLINE_SECONDS": routes_paper.AI_SPLIT_DEADLINE_SECONDS,
        "AI_SPLIT_READ_TIMEOUT_SECONDS": read_timeout,
        "AI_SPLIT_READ_TIMEOUT_SOURCE": read_source,
        "PDF_METADATA_DEADLINE_SECONDS": routes_paper.PDF_METADATA_DEADLINE_SECONDS,
        "PDF_FASTPATH_STRICT": os.getenv("PDF_FASTPATH_STRICT", "0"),
    }


def _row(paper_id, arm, *, ok, wall_ms, diag=None, qs=None, error_cls=None):
    """固定 schema 的一行记录；legacy 臂的 AI 内部指标一律 null（真身不返回 usage）。"""
    diag = diag or {}
    usage = diag.get("ai_usage") or {}
    items = qs or []
    legacy = arm == "legacy"
    return {
        "paper_id": paper_id,
        "arm": arm,
        "ok": bool(ok),
        "wall_ms": wall_ms,
        "ai_ms": None if legacy else diag.get("ai_ms"),
        "ai_ok": None if legacy else diag.get("ai_ok"),
        "ai_error_class": error_cls or (None if legacy else diag.get("ai_error_class")),
        "degrade_reason": None if legacy else (diag.get("degrade_reason") or ""),
        "fallback_level": None if legacy else diag.get("fallback_level"),
        "prompt_tokens": None if legacy else usage.get("prompt_tokens"),
        "completion_tokens": None if legacy else usage.get("completion_tokens"),
        "total_tokens": None if legacy else usage.get("total_tokens"),
        "n_questions": len(items),
        "q_types": _dist(items),
        "n_images": _img(items),
        "n_unclassified": _n_unclassified(items),
        "review_tagged": _review_tagged(items),
    }


def _run_legacy_arm(text, frag):
    """老路臂：异常原样带回，由主流程记成 fail，绝不静默降级成「跳过」。"""
    try:
        qs, dt = _old_path_measure(text, f"ab-{frag}-legacy")
    except Exception as exc:
        return [], None, exc
    return qs, int(round(dt * 1000)), None


def _run_fast_arm(text, page_texts, frag):
    """快路臂：只在本次调用内临时打开 kill switch，退出后原样恢复环境。"""
    diag: dict = {}
    saved = os.environ.get("PDF_FASTPATH_ENABLED")
    os.environ["PDF_FASTPATH_ENABLED"] = "1"
    t0 = time.perf_counter()
    try:
        qs = routes_paper.parse_pdf_with_fast_path(
            text, page_texts, False, None, f"ab-{frag}", diag)
        return qs, diag, int(round((time.perf_counter() - t0) * 1000)), None
    except Exception as exc:
        return None, diag, int(round((time.perf_counter() - t0) * 1000)), exc
    finally:
        if saved is None:
            os.environ.pop("PDF_FASTPATH_ENABLED", None)
        else:
            os.environ["PDF_FASTPATH_ENABLED"] = saved


def _secs(row):
    if row is None or row.get("wall_ms") is None:
        return "-"
    return f"{row['wall_ms'] / 1000:.2f}"


def _print_table_row(label, chars, nblocks, legacy_row, fast_row):
    counts = f"{(fast_row or {}).get('n_questions')}/{(legacy_row or {}).get('n_questions')}"
    print(f"{label:<18}{chars:>8}{nblocks:>8}{_secs(fast_row):>10}{_secs(legacy_row):>10}"
          f"{str((fast_row or {}).get('completion_tokens')):>10}"
          f"{str((fast_row or {}).get('fallback_level')):>18}{counts:>12}"
          f"{str((fast_row or {}).get('n_unclassified')):>8}")


def test_pdf_fast_path_ab():
    papers = _papers()
    provider = _provider()
    if not provider.api_key:
        pytest.skip(f"未配置 {provider.credential_label}，无法跑真实 A/B")

    consts = _production_constants()
    run_legacy = os.getenv("PDF_AB_OLD") != "0"
    now = datetime.now()
    meta = {
        "kind": "meta",
        "git_head": _git_head(),
        "timestamp": now.isoformat(timespec="seconds"),
        "provider_code": provider.provider_code,
        "model_name": provider.model_name,
        "api_host": _host(provider.api_base),
        "ai_split_deadline_seconds": consts["AI_SPLIT_DEADLINE_SECONDS"],
        "ai_split_read_timeout_seconds": consts["AI_SPLIT_READ_TIMEOUT_SECONDS"],
        "ai_split_read_timeout_source": consts["AI_SPLIT_READ_TIMEOUT_SOURCE"],
        "pdf_metadata_deadline_seconds": consts["PDF_METADATA_DEADLINE_SECONDS"],
        "pdf_fastpath_strict": consts["PDF_FASTPATH_STRICT"],
        "n_papers": len(papers),
        "pdf_ab_all": "1" if os.getenv("PDF_AB_ALL") == "1" else "0",
        "legacy_arm": "run" if run_legacy else "skipped",
        "note": "老路走生产真身，token 用量不进 diagnostics（真身不返回 usage），仅比对墙钟与题数/题型。",
    }

    out_dir = os.path.join(_ROOT, "outputs", "ab_" + now.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    rows_path = os.path.join(out_dir, "rows.jsonl")

    print("\n" + "=" * 100)
    for key, value in meta.items():
        if key == "kind":
            continue
        print(f"{key} = {value}")
    print("=" * 100)
    print(f"{'label':<18}{'chars':>8}{'blocks':>8}{'快路s':>10}{'老路s':>10}"
          f"{'OutTok快':>10}{'fallback':>18}{'题数快/老':>12}{'未分类':>8}")
    print("-" * 100)

    failures: list[str] = []
    with open(rows_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(meta, ensure_ascii=False) + "\n")
        for frag, label, dir_name in papers:
            try:
                text, page_texts = _load(dir_name)
                plan = pdf_local_split.plan_pdf_local_split(text)
            except Exception as exc:
                failures.append(f"{frag} 读取/切块失败: {type(exc).__name__}: {str(exc)[:120]}")
                print(f"[{label}] 读取/切块失败: {type(exc).__name__}: {str(exc)[:120]}")
                continue

            # 前置硬门：不信任的卷会直接走 L1，快路径根本不被调用，那一卷等于「老路 vs 老路」
            if plan.trustworthy is not True:
                failures.append(
                    f"{frag} 不满足 trustworthy，无法用于 A/B"
                    f"（judge_report={plan.judge_report}）"
                )
                print(f"[{label}] 不信任卷：跳过双臂（不烧额度），并已记为失败")
                continue

            legacy_row = None
            oq: list = []
            if run_legacy:
                oq, ow_ms, oerr = _run_legacy_arm(text, frag)
                if oerr is not None:
                    failures.append(f"{frag} 老路（生产真身）异常: {type(oerr).__name__}: {str(oerr)[:120]}")
                elif not oq:
                    failures.append(f"{frag} 老路（生产真身）返回空 questions")
                legacy_row = _row(
                    frag, "legacy", ok=bool(oq), wall_ms=ow_ms, qs=oq,
                    error_cls=type(oerr).__name__ if oerr else None,
                )
                fh.write(json.dumps(legacy_row, ensure_ascii=False) + "\n")

            fq, fdiag, fw_ms, ferr = _run_fast_arm(text, page_texts, frag)
            if ferr is not None:
                failures.append(f"{frag} 快路径抛出: {type(ferr).__name__}: {str(ferr)[:120]}")
            if fdiag.get("ai_ok") is not True:
                failures.append(
                    f"{frag} 快路径 diagnostics.ai_ok 不为 True（实际 {fdiag.get('ai_ok')!r}，"
                    f"ai_error_class={fdiag.get('ai_error_class')!r}）"
                )
            if fdiag.get("degrade_reason"):
                failures.append(f"{frag} 出现降级 degrade_reason={fdiag.get('degrade_reason')!r}")
            level = fdiag.get("fallback_level")
            if level not in _OK_FALLBACK_LEVELS:
                failures.append(
                    f"{frag} 回退档位 {level!r} 不在 {_OK_FALLBACK_LEVELS}，快路径没有真正生效"
                )
            fast_row = _row(
                frag, "fast", ok=(ferr is None and bool(fq)), wall_ms=fw_ms, diag=fdiag, qs=fq,
                error_cls=type(ferr).__name__ if ferr else None,
            )
            if ferr is None and not fq:
                failures.append(f"{frag} 快路径返回空 questions")
            if fast_row["n_unclassified"]:
                failures.append(
                    f"{frag} 有 {fast_row['n_unclassified']} 题 question_type 为空"
                    f"（review_tagged={fast_row['review_tagged']}，题型分布={fast_row['q_types']}）"
                )
            fh.write(json.dumps(fast_row, ensure_ascii=False) + "\n")
            fh.flush()

            _print_table_row(label, len(text), len(plan.all_blocks), legacy_row, fast_row)
            print(f"      DIAG: level={fdiag.get('fallback_level')} ai_ms={fdiag.get('ai_ms')} "
                  f"usage={fdiag.get('ai_usage')} local={fdiag.get('local_blocks')} "
                  f"susp={fdiag.get('suspicious_blocks')} degrade={fdiag.get('degrade_reason', '')!r}")
            print(f"      题型 快={fast_row['q_types']} 老={_dist(oq)}")

    print("\nrows -> " + rows_path)
    if failures:
        pytest.fail(
            f"A/B 硬判据未通过（共 {len(failures)} 项）:\n"
            + "\n".join(f"  - {item}" for item in failures)
        )
