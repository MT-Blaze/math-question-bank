#!/usr/bin/env python3
"""
migrate_choice_double_parens.py - 题干「双括号」存量清理（待办 #2）
------------------------------------------------------------------
病灶见 ``mathbank/latex_normalize.py::clean_choice_stem_parentheses`` 的 2026-09-25
修复说明，两条缺陷叠加：

  ① 摘 ``\\paren`` 与清末尾空括号的**顺序反了** —— 末尾空括号的正则带 ``$`` 锚定，
     而输入形如 ``…（　）\\paren`` 时末尾是 ``\\paren``，锚定落空、括号清不掉；
     等随后删掉 ``\\paren``，括号虽已成末尾却不再重清，归一化又补一个 ``\\paren``。
  ② 末尾空括号后不允许跟**句末标点** —— 中文题面几乎总写 ``（　）.``，照样清不掉。

结果：「原卷的手写空括号」和「系统补的 ``\\paren``」同时留在题干里，渲染成两个括号。
新录入已由上面那个函数修好；本脚本清**存量**。

**默认只扫描，不落库。** 先看 ``--dry-run`` 报告，确认无误后再正式跑。

用法::

    python scripts/migrate_choice_double_parens.py --dry-run   # 只打印 diff，不备份不落库
    python scripts/migrate_choice_double_parens.py             # 先完整备份，再正式迁移
    python scripts/migrate_choice_double_parens.py --no-export # 跳过 JSON/Markdown 同步导出

范围：只处理 ``AUTOFIX_IDS`` 的 ``content`` 字段（``answer_markdown`` 无此问题，
已核对 0 处）。``MANUAL_IDS`` 只报告、绝不修改 —— 它们的手写括号嵌在句子中间或
位于行内数学里，自动清有语义风险，留人工定夺。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from mathbank.backup import create_full_backup
from mathbank.database import SessionLocal, Question, QuestionFingerprint
from mathbank.latex_normalize import normalize_choice_paren_macro
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
# 一 import main 就会 PermissionError。取值口径与 scripts/migrate_fillin.py 一致。
UPLOAD_DIR = str(UPLOADS_DIR)
UPLOAD_DIR_REL = "static/test_uploads" if IS_TESTING else "static/uploads"

# diff 日志落这里，方便回看与贴报告
DIFF_LOG_DIR = DATA_BACKUP_DIR / "choice_paren_migration"

# 2026-09-25 盘点出的「同时含手写空括号与 \paren」的题（411 题库共 15 处）。
# 其余只有 \paren 的题**不得**出现在这里 —— 它们本来就是对的。
#
# 形态与归属（用户 2026-09-25 拍板「形态 B 也一并清」）：
#   A  `（ ）. \paren`         括号后跟句末标点            → 可清 → 63, 323
#   B  `$…= (\quad)$ \paren`   数学模式内的作答括号         → 可清 → 36,41,59,73,102,159,177,200,235
#   C  `（）条件. \paren`      括号后接**正文**（嵌在句中） → 末尾锚定覆盖不到 → 人工
#   D  括号离 \paren 100+ 字符 括号在题干中间（附注/多命题）→ 同样覆盖不到   → 人工
AUTOFIX_IDS = [36, 41, 59, 63, 73, 102, 159, 177, 200, 235, 323]

# 只报告、不修改的四道。理由见各自条目。
MANUAL_IDS = [97, 264, 277, 430]

MANUAL_NOTES = {
    97: "手写 '( )' 在题干中间（'你认为顾客购得的黄金 ( )'），后面还跟着大段附注，"
        "不在末尾 → 末尾锚定覆盖不到。建议：人工把该 '( )' 删掉（保留末尾 \\paren），"
        "或确认它是否另有语义。",
    264: "手写 '（）' 在题干开头的作答位（'真命题的序号是（）'），后面跟着 ①②③④ 四个命题，"
         "距 \\paren 有 200+ 字符 → 同样不在末尾。建议：人工删掉该 '（）'，让作答位统一落到末尾 \\paren。",
    277: "手写 '（）' 后接**正文**：'…的（）条件. \\paren'。若放行「括号后接正文」，会把句中空括号"
         "误当成作答括号，故刻意不覆盖。建议：人工判断 —— 该 '（）' 若确是作答位就删掉它；"
         "若另有语义（原卷的选项占位等）则保留，并考虑去掉末尾多余的 \\paren。",
    430: "与 #97 同形态（'你认为顾客购得的黄金（）' 后跟附注段），括号不在末尾 → 覆盖不到。"
         "建议：同 #97，人工删掉 '（）'、保留末尾 \\paren。",
}


def _default_retain() -> int:
    """沿用 scripts/backup.py 的默认保留策略（10 份）。"""
    return 10


def scan_database(db) -> tuple[list[tuple[int, str, str, str]], list[tuple[int, str]]]:
    """只算不写。返回 (changes, manual_report)。

    changes: [(question_id, column, before, after)]，仅 AUTOFIX_IDS 里**确有变化**的。
    manual_report: [(question_id, 现状摘要)]，MANUAL_IDS 的只读快照。
    """
    wanted = set(AUTOFIX_IDS)
    questions = {q.id: q for q in db.query(Question).filter(Question.id.in_(list(wanted))).all()}

    changes: list[tuple[int, str, str, str]] = []
    for qid in AUTOFIX_IDS:
        question = questions.get(qid)
        if question is None or not question.content:
            continue
        before = question.content
        after = normalize_choice_paren_macro(before, question.question_type or "")
        if after != before:
            changes.append((qid, "content", before, after))

    manual_report: list[tuple[int, str]] = []
    for qid in MANUAL_IDS:
        question = db.query(Question).filter(Question.id == qid).first()
        if question is None:
            manual_report.append((qid, "(题目不存在)"))
            continue
        manual_report.append((qid, question.content or ""))
    return changes, manual_report


def print_report(changes, manual_report, total_questions: int) -> None:
    print("=" * 72)
    print(f"📋 自动可清 {len(changes)} 处 / 待清理清单 {len(AUTOFIX_IDS)} 道"
          f"（库内共 {total_questions} 道题）")
    print("=" * 72)
    for qid, column, before, after in changes:
        print(f"  [改动] 题目 #{qid} ({column})")
        print(f"    - {before}")
        print(f"    + {after}")
    unchanged = [qid for qid in AUTOFIX_IDS if qid not in {c[0] for c in changes}]
    if unchanged:
        print(f"\n  ⚪ 清单内但**无变化** {len(unchanged)} 道：{unchanged}")
        print("     （手写括号不在末尾，本次末尾锚定的修复覆盖不到 —— 见下方人工项）")
    if not changes:
        print("  ✨ 清单内已无可自动清理的题。")

    print("\n" + "=" * 72)
    print(f"🔎 人工审核 {len(manual_report)} 道（只报告，绝不修改）")
    print("=" * 72)
    for qid, content in manual_report:
        print(f"\n  [人工] 题目 #{qid}")
        print(f"    现状: {content[:400]}")
        note = MANUAL_NOTES.get(qid)
        if note:
            print(f"    建议: {note}")


def write_diff_log(changes, manual_report, dry_run: bool) -> Path:
    DIFF_LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = DIFF_LOG_DIR / f"choice-paren-diff-{stamp}{'.dryrun' if dry_run else ''}.log"
    lines = [
        f"# 题干双括号存量清理 diff — {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"# database={DATABASE_FILE}  dry_run={dry_run}  changes={len(changes)}",
        f"# AUTOFIX_IDS={AUTOFIX_IDS}",
        f"# MANUAL_IDS={MANUAL_IDS}",
        "",
    ]
    for qid, column, before, after in changes:
        lines.append(f"--- question #{qid} ({column})")
        lines.append(f"--- BEFORE\n{before}")
        lines.append(f"--- AFTER\n{after}")
        lines.append("")
    lines.append("=" * 40)
    lines.append("# 人工审核（未修改）")
    for qid, content in manual_report:
        lines.append(f"--- question #{qid} (人工，未改)")
        lines.append(content)
        lines.append(f"建议: {MANUAL_NOTES.get(qid, '')}")
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
    **只动 affected ids** —— 传全库会把 390 道题的指纹全部重算一遍，结果虽然一致，
    但日志与影响面都会失真。
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


def migrate_choice_double_parens(dry_run: bool = False, *, retain: int = 10,
                                 export: bool = True) -> int:
    print("=" * 72)
    mode = "🔍 DRY-RUN（只扫描，不落库）" if dry_run else "🚀 正式清理"
    print(f"[Choice Double-Paren Migration] {mode}")
    print(f"  database = {DATABASE_FILE}")
    print("=" * 72)

    db = SessionLocal()
    try:
        total_questions = db.query(Question).count()
        changes, manual_report = scan_database(db)
        print_report(changes, manual_report, total_questions)
        log_path = write_diff_log(changes, manual_report, dry_run)
        print(f"\n📝 diff 日志：{log_path}")

        if dry_run:
            print("\n🔒 DRY-RUN 到此为止：未备份、未改库、未刷新导出。")
            return 0
        if not changes:
            print("\n✨ 清单内已无可自动清理的题，未做任何改动。")
            return 0

        # 1) 改库之前先做一份完整备份
        print("\n💾 正在创建完整备份 ...")
        backup_path = create_full_backup(retention=retain)
        print(f"   备份完成：{backup_path}")

        # 2) 落库（只碰 content，且只在清单内）
        questions_by_id = {q.id: q for q in db.query(Question).all()}
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
        print(f"\n✅ 已清理 {len(changes)} 处，涉及 {len(changed_ids)} 道题（ids: {changed_ids}）")
        print(f"🗂  指纹：删除旧 {deleted} 行，按新内容重算 {rebuilt} 条")

        # 4) 同步 JSON / Markdown 备份与 AI 题库
        if export:
            print("\n🔄 正在同步导出最新数据至 JSON 备份文件与 AI 专属题库 ...")
            export_database_to_files()
            print("🎉 自动同步完成！(JSON 备份与 Markdown 题库已刷新)")
    except Exception as exc:
        db.rollback()
        print(f"❌ 清理过程中发生异常: {exc}")
        sys.exit(1)
    finally:
        db.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="题干双括号存量清理")
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
        help="备份保留份数（默认 10）",
    )
    args = parser.parse_args()
    return migrate_choice_double_parens(
        dry_run=args.dry_run,
        retain=args.retain,
        export=not args.no_export,
    )


if __name__ == "__main__":
    raise SystemExit(main())
