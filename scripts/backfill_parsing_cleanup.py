#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""一次性回洗：把 2026-10 解析清洗修复应用到存量题库，并重算查重指纹。

覆盖的规则（与录入链同源，绝不复制正则）：

  * 题干越界吃进 ``【分析】/【详解】/【解析】/【解答】`` → 整块搬回解析区；
  * ``\left\{ + aligned/array/matrix + \right.`` → 统一为 ``\begin{cases}``；
  * 集合语境里的裸大写字母（``集合 A, B`` / ``全集U，集合M，N``）→ ``$A$``；
  * 题干末尾的横排 ``A．… B．… C．… D．…`` → ``\begin{choices}`` 并修正题型；
  * 以及同一入库链上的其余确定性改写（行内 ``\frac``→``\dfrac``、越界标题截断、
    解析配图去重、外层 ``【解析】`` 兜底等）。

**默认 dry-run**：只以 ``mode=ro`` 打开数据库，打印逐题 diff 与规则统计，绝不写库。
``--apply`` 才会先创建完整备份，再单事务落库、重算受影响题目的
``question_fingerprints``，最后刷新 JSON / AI 题库导出。

⚠️ 脚本通过 :mod:`mathbank.parsing_pipeline` 复用录入链，而 ``main`` 在导入期会持有
题库运行锁。因此运行本脚本前必须先关闭 27182 服务；落库完成后再启动服务。

用法::

    python -m scripts.backfill_parsing_cleanup            # dry-run
    python -m scripts.backfill_parsing_cleanup --apply    # 备份 + 落库 + 指纹 + 导出
"""

from __future__ import annotations

import argparse
import contextlib
import difflib
import io
import re
import sqlite3
import sys
import time
from pathlib import Path

from mathbank.paths import DATA_BACKUP_DIR, DATABASE_FILE, UPLOADS_DIR

SCAN_FIELDS = ("content", "answer_markdown")
UPLOAD_DIR = str(UPLOADS_DIR)
UPLOAD_DIR_REL = "static/uploads"
LOG_DIR = DATA_BACKUP_DIR / "parsing_cleanup_backfill"
CONSOLE_LINE_CHARS = 240

# 与 parsing_pipeline / latex_normalize 的判据同口径，只用于**归因与复检**，不参与改写。
_STEM_HEADING_RE = re.compile(r"【[ \t]*(?:分析|详解|解析|解答|参考详解|点睛|点评)[ \t]*】")
_BRACED_ROW_ENV_RE = re.compile(
    r"\\left[ \t]*\\?[ \t]*\{[ \t]*\\begin[ \t]*\{(?:aligned|alignedat|gathered|array|matrix|smallmatrix)"
)
_TYPE_LABELS = {
    "single_choice": "单选题",
    "multi_choice": "多选题",
    "fill_in_blank": "填空题",
    "detailed_answer": "解答题",
}


def _load_chain():
    """延迟导入录入链：main 在导入期抢运行锁，服务未停时这里给出可操作的提示。"""
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            from mathbank.parsing_pipeline import apply_storage_normalization
    except Exception as exc:  # RuntimeLockError 会以异常消息暴露运行锁冲突
        if "题库服务仍在运行" in str(exc):
            print("✋ 检测到 27182 服务仍在运行：请先关闭服务，再运行本脚本。")
            raise SystemExit(2) from exc
        raise
    return apply_storage_normalization


def _open_readonly() -> sqlite3.Connection:
    uri = "file:" + DATABASE_FILE.resolve().as_posix() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _run_chain(chain, q: dict) -> dict:
    """跑一遍录入链，返回新的卡片（不改原 dict）。stdout 里的逐题日志收进缓冲区。"""
    card = {
        "content": q.get("content") or "",
        "answer_markdown": q.get("answer_markdown") or "",
        "question_type": q.get("question_type") or "",
        "paper_question_no": q.get("paper_question_no") or "",
        "image_paths": [],
    }
    with contextlib.redirect_stdout(io.StringIO()):
        chain([card])
    return card


def _changed_fields(before: dict, after: dict) -> list[str]:
    return [field for field in ("content", "answer_markdown", "question_type", "paper_question_no")
            if (before.get(field) or "") != (after.get(field) or "")]


def _labels(before: dict, after: dict) -> list[str]:
    from mathbank.latex_normalize import _extract_inline_choice_run
    from mathbank.latex_preflight import wrap_bare_math_variables

    labels: list[str] = []
    old_content = before.get("content") or ""
    new_content = after.get("content") or ""
    old_answer = before.get("answer_markdown") or ""
    new_answer = after.get("answer_markdown") or ""
    if _STEM_HEADING_RE.search(old_content) and not _STEM_HEADING_RE.search(new_content):
        labels.append("题干越界解析搬回解析区")
    if (_BRACED_ROW_ENV_RE.search(old_content) or _BRACED_ROW_ENV_RE.search(old_answer)) and not (
        _BRACED_ROW_ENV_RE.search(new_content) or _BRACED_ROW_ENV_RE.search(new_answer)
    ):
        labels.append("分段/方程组统一为 cases")
    if r"\begin{choices}" not in old_content and r"\begin{choices}" in new_content:
        if any(_extract_inline_choice_run(line) is not None for line in old_content.split("\n")):
            labels.append("横排 ABCD 选项包装为 choices")
    wrapped = wrap_bare_math_variables(old_content)[1] + wrap_bare_math_variables(old_answer)[1]
    if wrapped:
        labels.append("裸数学字母补 $：%d 处" % wrapped)
    old_type, new_type = before.get("question_type") or "", after.get("question_type") or ""
    if old_type != new_type:
        labels.append("题型修正：%s → %s" % (
            _TYPE_LABELS.get(old_type, old_type or "（空）"),
            _TYPE_LABELS.get(new_type, new_type or "（空）"),
        ))
    if (before.get("paper_question_no") or "") != (after.get("paper_question_no") or ""):
        labels.append("原卷题号回填：%s" % (after.get("paper_question_no") or ""))
    if not labels:
        labels.append("其余入库存档归一化（行内分式/定界符/越界标题/配图去重/空行）")
    return labels


def scan_database(chain) -> tuple[list, list, dict, int]:
    """只读扫描全库，返回 (变更清单, 人工复核项, 规则统计, 总题数)。"""
    changes: list = []
    manual: list = []
    stats: dict[str, int] = {}
    con = _open_readonly()
    try:
        rows = con.execute(
            "SELECT id, question_type, content, answer_markdown, paper_question_no "
            "FROM questions ORDER BY id"
        ).fetchall()
    finally:
        con.close()

    for qid, qtype, content, answer, paper_no in rows:
        before = {
            "question_type": qtype or "",
            "content": content or "",
            "answer_markdown": answer or "",
            "paper_question_no": paper_no or "",
        }
        after = _run_chain(chain, before)
        fields = _changed_fields(before, after)
        if fields:
            labels = _labels(before, after)
            changes.append((qid, before, after, fields, labels))
            for label in labels:
                key = label.split("：")[0]
                stats[key] = stats.get(key, 0) + 1
        # 幂等自检：录入链对自身产物必须零改写，否则卡片预览与写库会漂移。
        again = _run_chain(chain, after)
        if _changed_fields(after, again) or _sanitized(after) != _sanitized(again):
            manual.append((qid, "清洗链不自洽（二次跑仍有改写）", again))
        # 复检：保守边界（题干切完为空、带竖线列规格的 array 等）会合法地留下原形态。
        if _STEM_HEADING_RE.search(after.get("content") or ""):
            manual.append((qid, "题干仍含解析标题（可能整卡都是解析，保守放行）", after))
        if _BRACED_ROW_ENV_RE.search(after.get("content") or "") or _BRACED_ROW_ENV_RE.search(
            after.get("answer_markdown") or ""
        ):
            manual.append((qid, "仍有大括号行式环境（符号表/无闭合形态，保守放行）", after))
    return changes, manual, stats, len(rows)


def _sanitized(card: dict) -> tuple:
    return tuple((card.get(field) or "") for field in
                 ("content", "answer_markdown", "question_type", "paper_question_no"))


def _truncate(text: str) -> str:
    return text if len(text) <= CONSOLE_LINE_CHARS else text[:CONSOLE_LINE_CHARS] + "…"


def print_report(changes, manual, stats, total: int, dry_run: bool) -> None:
    print("=" * 70)
    print("🔍 DRY-RUN（只扫描，不写库）" if dry_run else "🚀 正式回洗（将写库）")
    print("  database = %s" % DATABASE_FILE)
    print("  题库总题数 = %d" % total)
    print("=" * 70)
    print("\n== 规则命中统计（按涉及题数） ==")
    if not stats:
        print("  ✨ 没有需要改写的存量题。")
    for label, count in sorted(stats.items(), key=lambda kv: (-kv[1], kv[0])):
        print("  · %-32s %d 题" % (label, count))
    print("\n== 变更清单：%d 道题 ==" % len(changes))
    for qid, before, after, fields, labels in changes:
        print("\n--- 题目 #%s  字段=%s" % (qid, ",".join(fields)))
        for label in labels:
            print("    ▸ " + label)
        for field in fields:
            old = (before.get(field) or "").splitlines()
            new = (after.get(field) or "").splitlines()
            for line in difflib.unified_diff(old, new, lineterm="", n=0):
                if line.startswith(("---", "+++", "@@")):
                    continue
                print("    " + _truncate(line))
    print("\n== 人工复核清单（脚本不改这些形态）：%d 项 ==" % len(manual))
    if not manual:
        print("  ✨ 无。")
    for qid, reason, card in manual:
        print("  - #%s：%s" % (qid, reason))
        print("      题干尾：%s" % _truncate((card.get("content") or "")[-160:].replace("\n", "⏎")))
        print("      解析头：%s" % _truncate((card.get("answer_markdown") or "")[:160].replace("\n", "⏎")))


def write_diff_log(changes, manual, stats, dry_run: bool) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = LOG_DIR / ("parsing-cleanup-%s%s.log" % (stamp, ".dryrun" if dry_run else ""))
    lines = [
        "# 存量题库解析清洗回洗 — %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
        "# database=%s  dry_run=%s  changed=%d" % (DATABASE_FILE, dry_run, len(changes)),
        "",
        "## 规则命中统计",
    ]
    for label, count in sorted(stats.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append("- %s：%d 题" % (label, count))
    lines.append("")
    for qid, before, after, fields, labels in changes:
        lines.append("=" * 70)
        lines.append("question #%s  fields=%s" % (qid, ",".join(fields)))
        for label in labels:
            lines.append("### " + label)
        for field in fields:
            lines.append("--- BEFORE (%s)\n%s" % (field, before.get(field) or ""))
            lines.append("--- AFTER  (%s)\n%s" % (field, after.get(field) or ""))
    for qid, reason, card in manual:
        lines.append("=" * 70)
        lines.append("!!! 人工复核 #%s：%s" % (qid, reason))
        lines.append("content:\n" + (card.get("content") or ""))
        lines.append("answer:\n" + (card.get("answer_markdown") or ""))
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _rebuild_fingerprints(db, question_ids, questions_by_id):
    from mathbank.database import QuestionFingerprint
    from mathbank.question_duplicate_service import (
        fingerprint_for_question,
        upsert_question_fingerprint,
    )

    target = set(question_ids)
    deleted = (
        db.query(QuestionFingerprint)
        .filter(QuestionFingerprint.question_id.in_(list(target)))
        .delete(synchronize_session=False)
    )
    rebuilt = 0
    for question in questions_by_id.values():
        if question.id not in target:
            continue
        try:
            fingerprint = fingerprint_for_question(
                question, uploads_dir=UPLOAD_DIR, url_prefix=UPLOAD_DIR_REL
            )
        except Exception as exc:  # pragma: no cover - 仅供人工排查
            print("  ⚠️ 题目 #%s 指纹重算失败（%s），保留删除态" % (question.id, type(exc).__name__))
            continue
        upsert_question_fingerprint(db, question, fingerprint)
        rebuilt += 1
    return deleted, rebuilt


def migrate(dry_run: bool, *, retain: int = 10, export: bool = True) -> int:
    chain = _load_chain()
    changes, manual, stats, total = scan_database(chain)
    print_report(changes, manual, stats, total, dry_run)
    log_path = write_diff_log(changes, manual, stats, dry_run)
    print("\n📝 完整 diff 日志：%s" % log_path)

    if dry_run:
        print("\n🔒 DRY-RUN 到此为止：未备份、未改库、未刷新导出。确认后加 --apply。")
        return 0
    if not changes:
        print("\n✨ 库中已无需要回洗的存量题，无需迁移。")
        return 0

    from mathbank.backup import create_full_backup
    from mathbank.database import Question, SessionLocal, init_db
    from mathbank.sync_helper import export_database_to_files

    print("\n💾 正在创建完整备份 ...")
    backup_path = create_full_backup(retention=retain)
    print("   备份完成：%s" % backup_path)

    print("🧱 正在确认数据库 schema（init_db，与服务启动路径一致）...")
    init_db()

    db = SessionLocal()
    try:
        questions = {q.id: q for q in db.query(Question).all()}
        changed_ids: list[int] = []
        skipped = 0
        for qid, before, after, fields, _labels in changes:
            question = questions.get(qid)
            if question is None:
                skipped += 1
                continue
            current = {
                "content": question.content or "",
                "answer_markdown": question.answer_markdown or "",
                "question_type": question.question_type or "",
                "paper_question_no": question.paper_question_no or "",
            }
            if any(current[field] != (before.get(field) or "") for field in fields if field in current):
                print("  ⚠️ 跳过 #%s：内容在扫描后已变化" % qid)
                skipped += 1
                continue
            for field in fields:
                setattr(question, field, after.get(field) or "")
            changed_ids.append(qid)
        db.flush()

        deleted, rebuilt = _rebuild_fingerprints(db, changed_ids, questions)
        db.commit()
        print("\n✅ 已回洗 %d 道题（ids: %s），跳过 %d 道"
              % (len(changed_ids), sorted(changed_ids), skipped))
        print("🗂  指纹：删除旧 %d 行，按新内容重算 %d 条" % (deleted, rebuilt))

        if export:
            print("\n🔄 正在同步导出 JSON 备份与 AI 专属题库 ...")
            export_database_to_files(db)
            print("🎉 自动同步完成！")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="存量题库解析清洗回洗（默认 dry-run）")
    parser.add_argument("--apply", action="store_true", help="正式落库：先备份，再改字段、重算指纹、刷新导出")
    parser.add_argument("--no-export", action="store_true", help="跳过 JSON/Markdown 同步导出")
    parser.add_argument("--retain", type=int, default=10, help="备份保留最近多少份（默认 10）")
    args = parser.parse_args()
    return migrate(dry_run=not args.apply, retain=args.retain, export=not args.no_export)


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    raise SystemExit(main())
