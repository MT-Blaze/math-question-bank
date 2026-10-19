# -*- coding: utf-8 -*-
"""A 档离线测量：对全部已缓存 MinerU 真实卷跑 R1 本地切块裁判，统计成功率与耗时。

纯本地、零 AI、零网络：只 import mathbank.pdf_local_split（顶层不触发 main，不抢锁）。
用法：
    venv\\Scripts\\python.exe -m scripts.measure_pdf_fastpath_offline            # 全量
    venv\\Scripts\\python.exe -m scripts.measure_pdf_fastpath_offline --dump-id <dir片段>  # 打印某卷逐块
"""
from __future__ import annotations

import argparse
import io
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parents[1]
_CACHE = _ROOT / ".system_generated" / "mineru_cache"

from mathbank import pdf_local_split as P  # noqa: E402


def _collect_full_md():
    out = []
    for d in sorted(_CACHE.glob("*/full.md")):
        try:
            text = d.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if text.strip():
            out.append((d, text))
    return out


def _pct(part, whole):
    return round(100.0 * part / whole, 1) if whole else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump-id", default="", help="打印匹配该目录名片段的逐块明细")
    args = ap.parse_args()

    papers = _collect_full_md()
    if not papers:
        print("未找到任何缓存 full.md")
        return

    n = len(papers)
    trustworthy_n = 0
    cover_ok_n = 0
    anchor_counts = []
    block_counts = []
    preserved_ratios = []
    suspicious_ratios = []
    split_ms = []
    gate_hits_total: dict = {}
    fallback_reasons: dict = {}
    skipped_gen = 0

    dump_rows = []
    for path, text in papers:
        t0 = time.perf_counter()
        plan = P.plan_pdf_local_split(text, page_texts=None, generate_answers=False)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        split_ms.append(dt_ms)

        rep = plan.judge_report
        if rep.get("skipped") == "generate_answers_on":
            skipped_gen += 1
        anchors = rep.get("anchor_count", 0)
        anchor_counts.append(anchors)
        nb = len(plan.all_blocks)
        block_counts.append(nb)
        if plan.cover_ok:
            cover_ok_n += 1
        if plan.trustworthy:
            trustworthy_n += 1
        preserved_ratios.append(rep.get("preserved_ratio", 0.0))
        suspicious_ratios.append(rep.get("suspicious_ratio", 0.0))

        for f, c in (rep.get("gate_hits") or {}).items():
            gate_hits_total[f] = gate_hits_total.get(f, 0) + c

        if not plan.trustworthy:
            reason = (
                "exception" if rep.get("exception")
                else "total_out_of_range" if rep.get("total_out_of_range")
                else "answer_key_restart" if rep.get("answer_key_restart")
                else "no_anchors" if anchors == 0
                else "monotonic" if rep.get("monotonic_ok") is False
                else "coverage_gap" if not plan.cover_ok
                else "preserved_ratio_low" if rep.get("preserved_ratio", 0) < P.MIN_PRESERVED_RATIO
                else "suspicious_high"
            )
            fallback_reasons[reason] = fallback_reasons.get(reason, 0) + 1

        if args.dump_id and args.dump_id in path.parent.name:
            dump_rows.append((path, plan))

    # ---------- 汇总 ----------
    def _q(vals, p):
        if not vals:
            return 0.0
        s = sorted(vals)
        k = min(len(s) - 1, int(p * (len(s) - 1)))
        return s[k]

    print("== A 档：R1 本地切块裁判 · 全量缓存真实卷 ==")
    print(f"卷数                     : {n}")
    print(f"trustworthy(可走快路径)  : {trustworthy_n} / {n} = {_pct(trustworthy_n, n)}%")
    print(f"cover_ok(结构审查通过)    : {cover_ok_n} / {n} = {_pct(cover_ok_n, n)}%")
    print(f"锚点数  mean/min/max     : {round(sum(anchor_counts)/n,1)} / {min(anchor_counts)} / {max(anchor_counts)}")
    print(f"块数   mean/min/max      : {round(sum(block_counts)/n,1)} / {min(block_counts)} / {max(block_counts)}")
    print(f"本地保留率 均值/中位      : {round(sum(preserved_ratios)/n,3)} / {_q(preserved_ratios,0.5)}")
    print(f"纯切块耗时 ms p50/p95/max : {round(_q(split_ms,0.5),1)} / {round(_q(split_ms,0.95),1)} / {round(max(split_ms),1)}")
    print("")
    print("整卷回退原因分布（不信任卷）:")
    if fallback_reasons:
        for r, c in sorted(fallback_reasons.items(), key=lambda x: -x[1]):
            print(f"  {r:22s}: {c}")
    else:
        print("  （全部可信，无回退）")
    print("")
    print("块级闸门命中分布（可疑块计数）:")
    if gate_hits_total:
        for f, c in sorted(gate_hits_total.items(), key=lambda x: -x[1]):
            print(f"  {f:22s}: {c}")
    else:
        print("  （无块级命中）")

    for path, plan in dump_rows:
        print("\n==== dump:", path.parent.name, "====")
        for b in plan.all_blocks:
            print(f"  #{b.paper_question_no:>3} [{b.local_type:14s}] flags={b.risk_flags} imgs={len(b.referenced_images)}")
            print(f"        content: {b.content[:90]!r}")


if __name__ == "__main__":
    main()
