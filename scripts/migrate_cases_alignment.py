#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""一次性回洗：给存量分段函数 ``cases`` 补对齐符 ``&``（``1, x>0`` → ``1, & x>0``）。

判定与入库链同源（:func:`mathbank.latex_normalize.classify_cases_body`），本脚本不另写正则。
每个 ``\begin{cases}`` 块归入四类之一：

  * ``aligned``   每行都是「值, 条件」——补 ``&``；
  * ``has_amp``   已有 ``&``——跳过；
  * ``system``    方程组 / 不等式组（行内没有分隔逗号）——单栏本就正确，不动；
  * ``ambiguous`` 判不准——不动，列进「模棱两可」清单供人工核对。

**默认 dry-run**：只以 ``mode=ro`` 打开数据库，不需要停服。``--apply`` 必须先关闭 27182
服务：持运行锁 → 创建已验证完整备份 → 单事务改字段并重算受影响题目的查重指纹 → 刷新
JSON / AI 题库导出。

用法::

    python -m scripts.migrate_cases_alignment            # dry-run
    python -m scripts.migrate_cases_alignment --apply    # 备份 + 落库 + 指纹 + 导出
"""

from __future__ import annotations

import argparse
import io
import json
import sqlite3
import sys
import time
from pathlib import Path

from mathbank.latex_normalize import (
    _CASES_BLOCK_RE,
    align_cases_conditions,
    classify_cases_body,
    normalize_for_storage,
)
from mathbank.paths import DATA_BACKUP_DIR, DATABASE_FILE, UPLOADS_DIR

FIELDS = ("content", "answer_markdown")
LOG_DIR = DATA_BACKUP_DIR / "cases_alignment_backfill"
STATUS_LABELS = {
    "aligned": "补 & 对齐",
    "stale_amp": "删 aligned 遗留的错位 &",
    "has_amp": "已有 &（跳过）",
    "system": "方程组/不等式组（单栏，不动）",
    "ambiguous": "模棱两可（不动）",
}
CHANGED_STATUSES = ("aligned", "stale_amp")


def _open_readonly() -> sqlite3.Connection:
    uri = "file:" + DATABASE_FILE.resolve().as_posix() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _cases_blocks(text: str) -> list:
    return [match.group(0) for match in _CASES_BLOCK_RE.finditer(text or "")]


def scan(rows) -> dict:
    blocks: list = []
    changes: list = []
    chain_conflicts: list = []
    for qid, qtype, content, answer in rows:
        values = {"content": content or "", "answer_markdown": answer or ""}
        new_values = {}
        for field, text in values.items():
            for match in _CASES_BLOCK_RE.finditer(text):
                status, new_body, reason = classify_cases_body(match.group(2))
                blocks.append({
                    "id": qid,
                    "field": field,
                    "status": status,
                    "reason": reason,
                    "before": match.group(0),
                    "after": (match.group(1) + new_body + match.group(3)) if new_body else None,
                })
            updated = align_cases_conditions(text)
            if updated != text:
                new_values[field] = updated
                # 入库链对补好的文本必须保留全部 cases 块，否则下次保存会把 & 冲掉或改形。
                rechained = normalize_for_storage(
                    updated, qtype or "", is_answer=(field == "answer_markdown")
                )
                if _cases_blocks(rechained) != _cases_blocks(updated):
                    chain_conflicts.append((qid, field))
        if new_values:
            changes.append({"id": qid, "before": values, "after": {**values, **new_values},
                            "fields": sorted(new_values)})
    return {"blocks": blocks, "changes": changes, "chain_conflicts": chain_conflicts}


def print_report(result: dict, total: int, dry_run: bool) -> None:
    blocks = result["blocks"]
    print("=" * 70)
    print("DRY-RUN（只扫描，不写库）" if dry_run else "正式回洗（将写库）")
    print("  database = %s" % DATABASE_FILE)
    print("  题库总题数 = %d；含 cases 的题 = %d；cases 块 = %d"
          % (total, len({b["id"] for b in blocks}), len(blocks)))
    print("=" * 70)
    print("\n== 块分类统计 ==")
    for status, label in STATUS_LABELS.items():
        hit = [b for b in blocks if b["status"] == status]
        print("  · %-28s %3d 块 / %d 题" % (label, len(hit), len({b["id"] for b in hit})))

    print("\n== 将修改的块（修改前 → 修改后）==")
    for b in blocks:
        if b["status"] in CHANGED_STATUSES:
            print("\n  #%s %s【%s】" % (b["id"], b["field"], STATUS_LABELS[b["status"]]))
            print("    前：" + b["before"])
            print("    后：" + b["after"])

    print("\n== 模棱两可清单（不改，请人工核对）==")
    ambiguous = [b for b in blocks if b["status"] == "ambiguous"]
    if not ambiguous:
        print("  无。")
    for b in ambiguous:
        print("  #%s %s【%s】%s" % (b["id"], b["field"], b["reason"], b["before"]))

    print("\n== 已有 & 但并非每行都有（跳过，仅提示）==")
    partial = [b for b in blocks if b["status"] == "has_amp"
               and any("&" not in row for row in b["before"][13:-11].split("\\\\") if row.strip())]
    if not partial:
        print("  无。")
    for b in partial:
        print("  #%s %s %s" % (b["id"], b["field"], b["before"]))

    print("\n== 入库链复核 ==")
    if result["chain_conflicts"]:
        for qid, field in result["chain_conflicts"]:
            print("  !! #%s %s：补完再过一遍入库链后 cases 块变了" % (qid, field))
    else:
        print("  补完的文本再过 normalize_for_storage，cases 块逐字不变。")
    print("\n合计将修改 %d 道题。" % len(result["changes"]))


def write_log(result: dict, dry_run: bool) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = LOG_DIR / ("cases-alignment-%s%s.json" % (stamp, ".dryrun" if dry_run else ""))
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _export(db) -> None:
    from mathbank.metadata import METADATA_FILE
    from mathbank.sync_helper import export_database_to_files
    from mathbank.topics_helper import topic_id_to_name

    try:
        curriculum = json.loads(Path(METADATA_FILE).read_text(encoding="utf-8")).get("curriculum", {})
    except (OSError, ValueError):
        curriculum = {}
    export_database_to_files(db, topic_translator=lambda tid: topic_id_to_name(curriculum, tid))


def apply(result: dict, retain: int) -> int:
    from mathbank.backup import RuntimeLockError, acquire_runtime_lock, create_full_backup
    from mathbank.database import Question, QuestionFingerprint, SessionLocal
    from mathbank.question_duplicate_service import (
        fingerprint_for_question,
        upsert_question_fingerprint,
    )

    try:
        lock = acquire_runtime_lock()
    except RuntimeLockError as exc:
        print("检测到 27182 服务仍在运行：请先关闭服务，再执行 --apply。")
        raise SystemExit(2) from exc

    with lock:
        backup_path = create_full_backup(retention=retain)
        print("\n完整备份：%s" % backup_path)
        db = SessionLocal()
        try:
            changed: list = []
            for item in result["changes"]:
                question = db.get(Question, item["id"])
                if question is None:
                    print("  跳过 #%s：题目已不存在" % item["id"])
                    continue
                if any((getattr(question, f) or "") != item["before"][f] for f in item["fields"]):
                    print("  跳过 #%s：内容在扫描后已变化" % item["id"])
                    continue
                for field in item["fields"]:
                    setattr(question, field, item["after"][field])
                changed.append(question)
            db.flush()
            ids = [q.id for q in changed]
            deleted = 0
            if ids:
                deleted = (db.query(QuestionFingerprint)
                           .filter(QuestionFingerprint.question_id.in_(ids))
                           .delete(synchronize_session=False))
            for question in changed:
                fingerprint = fingerprint_for_question(
                    question, uploads_dir=str(UPLOADS_DIR), url_prefix="static/uploads"
                )
                upsert_question_fingerprint(db, question, fingerprint)
            db.commit()
            print("已回洗 %d 道题：%s" % (len(ids), ids))
            print("指纹：删除旧 %d 行，重算 %d 条" % (deleted, len(changed)))
        except Exception:
            db.rollback()
            raise
        try:
            _export(db)
            print("JSON / AI 题库导出已刷新。")
        except Exception as exc:  # 已提交的写入不因导出失败而回滚
            print("导出刷新失败（%s），下次写库时会重试。" % type(exc).__name__)
        finally:
            db.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="存量 cases 补对齐符 &（默认 dry-run）")
    parser.add_argument("--apply", action="store_true", help="正式落库（需先停服）")
    parser.add_argument("--retain", type=int, default=10, help="备份保留最近多少份（默认 10）")
    args = parser.parse_args()

    con = _open_readonly()
    try:
        rows = con.execute(
            "SELECT id, question_type, content, answer_markdown FROM questions ORDER BY id"
        ).fetchall()
    finally:
        con.close()
    result = scan(rows)
    print_report(result, len(rows), dry_run=not args.apply)
    print("\n完整结果：%s" % write_log(result, dry_run=not args.apply))

    if not args.apply:
        print("\nDRY-RUN 到此为止：未备份、未改库。确认后加 --apply（需先关闭服务）。")
        return 0
    if result["chain_conflicts"]:
        print("\n入库链复核未通过，拒绝落库。")
        return 1
    if not result["changes"]:
        print("\n没有需要回洗的题。")
        return 0
    return apply(result, args.retain)


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    raise SystemExit(main())
