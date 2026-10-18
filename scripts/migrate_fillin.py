#!/usr/bin/env python3
"""
migrate_fillin.py - 历史题库 \\fillin 规范化批量迁移工具
------------------------------------------------------------------
``normalize_fillin_macro``（mathbank/math_markdown.py）在 2026-09-25 新增了三条规则：

  * 待办 #3  ：空的 ``\\fillin{}`` / ``\\fillin{ }`` 清理成裸 ``\\fillin``
  * 待办 #16①：``\\fillin`` 之后紧贴（或隔空白）的中文句末标点统一转 ASCII
               （。，；！？ ． 六个字形）
  * 待办 #16②：``\\fillin .`` 归一为紧贴的 ``\\fillin.``

本脚本把上述规则跑一遍存量库，并在完成后同步刷新 JSON/Markdown 备份与 AI 题库。

**默认只扫描，不落库。** 请先看 ``--dry-run`` 报告，确认无误后再正式跑。

用法::

    python scripts/migrate_fillin.py --dry-run     # 只打印 diff，不落库、不备份
    python scripts/migrate_fillin.py               # 先自动完整备份，再正式迁移
    python scripts/migrate_fillin.py --no-export   # 跳过 JSON/Markdown 同步导出
    python scripts/migrate_fillin.py --retain 20   # 备份保留最近 20 份（默认 10）

注意：本脚本只处理**当前项目自己那份库**。main 侧库在本次规范化中 \\fillin
零处（已核对），不需要迁移；如需顺手留一份 main 库的整体备份，请到 main 目录下
单独跑 ``python scripts/backup.py``。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from mathbank.backup import create_full_backup
from mathbank.database import SessionLocal, Question, QuestionFingerprint
from mathbank.math_markdown import normalize_fillin_macro
from mathbank.paths import (
    DATABASE_FILE,
    DATA_BACKUP_DIR,
    IS_TESTING,
    UPLOADS_DIR,
)
from mathbank.question_duplicate_service import (
    fingerprint_for_question,
    upsert_question_fingerprint,
)
from mathbank.sync_helper import export_database_to_files

# 刻意不从 main 导入：main 在模块导入期就去抢 runtime 锁，而线上服务正持着这把锁，
# 一 import main 就会 PermissionError。这里的取值与 main.py:317-320 在非测试态下
# 完全一致，换成从 paths 取让脚本在服务运行期间也能跑（尤其是 --dry-run）。
UPLOAD_DIR = str(UPLOADS_DIR)
UPLOAD_DIR_REL = "static/test_uploads" if IS_TESTING else "static/uploads"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# diff 日志落这里，方便回看与贴报告
DIFF_LOG_DIR = DATA_BACKUP_DIR / "fillin_migration"


def _default_retain() -> int:
    """沿用 scripts/backup.py 的默认保留策略（10 份）。"""
    return 10


def scan_database(db) -> list[tuple[int, str, str, str]]:
    """扫描整库，返回 [(question_id, column, before, after)] —— 只算不写。"""
    questions = {question.id: question for question in db.query(Question).all()}
    changes: list[tuple[int, str, str, str]] = []
    for question in questions.values():
        if not question.content:
            continue
        before = question.content
        after = normalize_fillin_macro(before)
        if after != before:
            changes.append((question.id, "content", before, after))
    return changes


def print_diff(changes, total_questions: int) -> None:
    print("=" * 70)
    print(f"📋 待迁移 {len(changes)} 处（库内共 {total_questions} 道题）")
    print("=" * 70)
    for qid, column, before, after in changes:
        print(f"  [改动] 题目 #{qid} ({column})")
        print(f"    - {before}")
        print(f"    + {after}")
    if not changes:
        print("  ✨ 库内已经全是规范形态，无需迁移。")


def write_diff_log(changes, dry_run: bool) -> Path:
    DIFF_LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = DIFF_LOG_DIR / f"fillin-diff-{stamp}{'.dryrun' if dry_run else ''}.log"
    lines = [
        f"# \\fillin 规范化迁移 diff — {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"# database={DATABASE_FILE}  dry_run={dry_run}  changes={len(changes)}",
        "",
    ]
    for qid, column, before, after in changes:
        lines.append(f"--- question #{qid} ({column})")
        lines.append(f"--- BEFORE\n{before}")
        lines.append(f"--- AFTER\n{after}")
        lines.append("")
    log_path.write_text("\n".join(lines), encoding="utf-8")
    return log_path


def rebuild_fingerprints(
    db,
    question_ids: list[int],
    questions_by_id: dict[int, Question],
) -> tuple[int, int]:
    """affected ids 的 question_fingerprints：先删后按新内容重算。

    旧指纹是按改之前的题干算的，留着会让去重索引拿旧内容去比对新题干。
    **只动 affected ids** —— 这里传全库会把 349 道题的指纹全部重算一遍，
    结果虽然一致，但日志与影响面都会失真。
    """
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
                question,
                uploads_dir=UPLOAD_DIR,
                url_prefix=UPLOAD_DIR_REL,
            )
        except Exception as exc:  # pragma: no cover - 仅供人工排查
            print(f"  ⚠️ 题目 #{question.id} 指纹重算失败（{type(exc).__name__}），保留删除态")
            continue
        upsert_question_fingerprint(db, question, fingerprint)
        rebuilt += 1
    return deleted, rebuilt


def migrate_database_fillin(dry_run: bool = False, *, retain: int = 10,
                            export: bool = True) -> int:
    print("=" * 65)
    mode = "🔍 DRY-RUN（只扫描，不落库）" if dry_run else "🚀 正式迁移"
    print(f"[Fillin Migration] {mode}")
    print(f"  database = {DATABASE_FILE}")
    print("=" * 65)

    db = SessionLocal()
    try:
        total_questions = db.query(Question).count()
        changes = scan_database(db)
        print_diff(changes, total_questions)
        log_path = write_diff_log(changes, dry_run)
        print(f"\n📝 diff 日志：{log_path}")

        if dry_run:
            print("\n🔒 DRY-RUN 到此为止：未备份、未改库、未刷新导出。")
            return 0
        if not changes:
            print(f"\n✨ 库中 {total_questions} 道题均已是规范 \\fillin 格式，无需迁移。")
            return 0

        # 1) 改库之前先做一份完整备份（照 scripts/backup.py 的口径）
        print("\n💾 正在创建完整备份 ...")
        backup_path = create_full_backup(retention=retain)
        print(f"   备份完成：{backup_path}")

        # 2) 落库
        questions_by_id = {question.id: question for question in db.query(Question).all()}
        changed_ids: list[int] = []
        for qid, _column, _before, after in changes:
            question = questions_by_id.get(qid)
            if question is None:
                continue
            question.content = after
            changed_ids.append(qid)
        db.flush()

        # 3) 指纹：删除旧行 + 按新内容重算
        deleted, rebuilt = rebuild_fingerprints(db, changed_ids, questions_by_id)
        db.commit()
        print(f"\n✅ 已迁移 {len(changes)} 处，涉及 {len(changed_ids)} 道题（ids: {changed_ids}）")
        print(f"🗂  指纹：删除旧 {deleted} 行，按新内容重算 {rebuilt} 条")

        # 4) 同步 JSON / Markdown 备份与 AI 题库
        if export:
            print("\n🔄 正在同步导出最新数据至 JSON 备份文件与 AI 专属题库 ...")
            export_database_to_files()
            print("🎉 自动同步完成！(JSON 备份与 Markdown 题库已刷新)")
    except Exception as exc:
        db.rollback()
        print(f"❌ 迁移过程中发生异常: {exc}")
        sys.exit(1)
    finally:
        db.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="\\fillin 规范化存量迁移")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只扫描并打印 diff，不备份、不落库、不刷新导出",
    )
    parser.add_argument(
        "--no-export",
        action="store_true",
        help="跳过 JSON/Markdown 同步导出",
    )
    parser.add_argument(
        "--retain",
        type=int,
        default=_default_retain(),
        help=f"备份保留最近多少份（默认 {_default_retain()}）",
    )
    args = parser.parse_args()
    return migrate_database_fillin(
        args.dry_run,
        retain=args.retain,
        export=not args.no_export,
    )


if __name__ == "__main__":
    raise SystemExit(main())
