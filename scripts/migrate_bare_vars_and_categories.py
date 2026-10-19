#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""存量回洗：中文语境裸数学变量（待办 #36）与教材分类字段（章/节/考向）。

新数据的两条清洗规则都已上线，本脚本把它们一次性补回**存量库**，判据完全复用
同一套函数，绝不另写一份：

  1. 裸变量补 ``$`` —— ``mathbank.latex_preflight.wrap_bare_math_variables``（R10）：
     「若 p 是 q 的充分不必要条件」这类中文里单独成词的小写变量，此前只在导出前
     自愈，库里仍是裸的；这次洗到源头，导出侧自愈层退回它应有的「兜底网」角色。
  2. 教材分类字段归一 —— ``mathbank.kaoxiang_tree.align_parsed_classification``：
     AI 把提示词里「章 > 小节」整行抄进 ``category_chapter`` 造成的脏章名，新卷已被
     入库第 7.6 步兜住，存量连同两张镜像表（``question_curriculums`` 活跃版本行 /
     ``question_topics`` 主槽位）一起补齐。

保守口径（protect-existing，一题的分类**要么全改要么不改**）：

  * 归一算出空值而原值非空 -> 整题分类不动，进人工清单（大纲改名/历史漂移的线索不能抹）；
  * 存量 ``topic_id`` 在当前大纲里翻译不出名字、而章节又要变 -> 整题分类不动；
  * 考向名在归一后的槽位里找不到 -> 整题分类不动；
  * 附加考向槽位（``sort_order != 0``）永不改动。

文本一改就重算 ``question_fingerprints``，落库口径与 ``scripts/migrate_latex_dirty_data.py``
一致：先完整备份，再走服务启动同一套 ``init_db()`` schema 入口。

用法::

    python -m scripts.migrate_bare_vars_and_categories           # 默认 dry-run：只读，不写库
    python -m scripts.migrate_bare_vars_and_categories --apply   # 备份 + 落库 + 重算指纹 + 刷新导出

注意：``--apply`` 前先停掉 27182 服务，避免与线上并发编辑互相覆盖。
"""

from __future__ import annotations

import argparse
import difflib
import io
import sys
import time

from mathbank.backup import create_full_backup
from mathbank.curriculums import get_current_curriculum
from mathbank.database import (
    Question,
    QuestionCurriculum,
    QuestionFingerprint,
    QuestionTopic,
    SessionLocal,
    init_db,
)
from mathbank.kaoxiang_tree import align_parsed_classification
from mathbank.latex_preflight import wrap_bare_math_variables
from mathbank.metadata import get_active_version_code, load_or_init_metadata
from mathbank.paths import DATABASE_FILE, DATA_BACKUP_DIR, IS_TESTING, UPLOADS_DIR
from mathbank.question_duplicate_service import (
    fingerprint_for_question,
    upsert_question_fingerprint,
)
from mathbank.sync_helper import export_database_to_files
from mathbank.topics_helper import topic_id_for_slot, topic_id_to_name

# 刻意不从 main 导入：main 在模块导入期抢 runtime 锁，线上服务持锁时会 PermissionError。
# 取值口径与 scripts/migrate_latex_dirty_data.py 一致。
UPLOAD_DIR = str(UPLOADS_DIR)
UPLOAD_DIR_REL = "static/test_uploads" if IS_TESTING else "static/uploads"

DIFF_LOG_DIR = DATA_BACKUP_DIR / "bare_vars_category_migration"
CONSOLE_LINE_CHARS = 240  # 控制台 diff 行截断长度，完整内容进日志文件

TEXT_FIELDS = ("content", "answer_markdown")
CATEGORY_FIELDS = ("category_compulsory", "category_chapter", "category_knowledge", "topic")

def _name(value) -> str:
    """分类字段统一口径：转字符串、去首尾空白，None 当空串。"""
    return str(value or "").strip()


def plan_text_wash(question) -> list:
    """题干/解析逐字段跑 R10；没命中的字段绝不产生改动（不做无谓回写）。"""
    hits: list = []
    for field in TEXT_FIELDS:
        before = getattr(question, field, "") or ""
        if not before:
            continue
        after, count, samples = wrap_bare_math_variables(before)
        if count and after != before:
            hits.append(
                {
                    "field": field,
                    "before": before,
                    "after": after,
                    "count": count,
                    "samples": list(samples[:3]),
                }
            )
    return hits


def current_category(question, curriculum) -> dict:
    """一题现状分类的可比较快照；考向显示名由 topic_id 反查大纲（库里不存名字）。"""
    return {
        "category_compulsory": _name(getattr(question, "category_compulsory", "")),
        "category_chapter": _name(getattr(question, "category_chapter", "")),
        "category_knowledge": _name(getattr(question, "category_knowledge", "")),
        "topic": _name(topic_id_to_name(curriculum, getattr(question, "topic_id", ""))),
    }


def plan_category_wash(question, curriculum) -> tuple:
    """算一题的分类归一方案，返回 (方案或 None, 跳过原因或空串)。"""
    before = current_category(question, curriculum)
    if not any(before.values()):
        return None, ""
    aligned = dict(before)
    align_parsed_classification(curriculum, [aligned])

    topic_id_before = _name(getattr(question, "topic_id", ""))
    drift = any(
        aligned[key] != before[key]
        for key in CATEGORY_FIELDS[:3]
    )
    if topic_id_before and not before["topic"] and drift:
        # 存量 topic_id 在当前大纲里翻不出名字：章节一变就没人能保证把它搬到新槽位的
        # 同一考向，宁可整题不动、把线索留给教师。
        return None, "考向 ID %s 不在当前大纲里，而章节需要变更" % topic_id_before

    after = dict(aligned)
    blocked = [
        "%s 归一后为空（原值 %r）" % (key, before[key])
        for key in CATEGORY_FIELDS
        if not after[key] and before[key]
    ]
    if blocked:
        # protect-existing：归一会抹掉非空存量线索时，整题分类一个字都不改。
        return None, "；".join(blocked)

    topic_id_after = topic_id_before
    if after["topic"]:
        resolved = topic_id_for_slot(
            curriculum,
            after["category_compulsory"],
            after["category_chapter"],
            after["category_knowledge"],
            after["topic"],
        )
        if not resolved:
            return None, (
                "考向 %r 在槽位（%r / %r）里找不到对应 ID（小节为空或与大纲不一致）"
                % (after["topic"], after["category_chapter"], after["category_knowledge"])
            )
        topic_id_after = resolved

    labels = [
        "%s %r -> %r" % (key, before[key], after[key])
        for key in CATEGORY_FIELDS
        if after[key] != before[key]
    ]
    if topic_id_after != topic_id_before:
        labels.append("topic_id %r -> %r" % (topic_id_before, topic_id_after))
    if not labels:
        return None, ""
    return {
        "before": before,
        "after": after,
        "topic_id_before": topic_id_before,
        "topic_id_after": topic_id_after,
        "labels": labels,
    }, ""


def plan_wash(rows, curriculum) -> tuple:
    """全库只读扫描，返回 (改动清单, 人工清单)。纯函数：只读属性，绝不写库。"""
    plans: list = []
    manual: list = []
    for question in rows or []:
        texts = plan_text_wash(question)
        category, reason = plan_category_wash(question, curriculum)
        if reason:
            manual.append((question.id, reason))
        if texts or category:
            plans.append({"question_id": question.id, "texts": texts, "category": category})
    return plans, manual

def _truncate(line: str) -> str:
    if len(line) <= CONSOLE_LINE_CHARS:
        return line
    return line[:CONSOLE_LINE_CHARS] + "…"


def print_report(plans, manual, total: int, version_code: str, dry_run: bool) -> None:
    text_fields = sum(len(plan["texts"]) for plan in plans)
    cat_plans = [plan for plan in plans if plan["category"]]
    print("=" * 66)
    print("🔍 DRY-RUN（只扫描，不写库）" if dry_run else "🚀 正式回洗（将写库）")
    print("  database = %s" % DATABASE_FILE)
    print("  活跃版本 = %s  题库总题数 = %d" % (version_code, total))
    print("  文本待洗：%d 题 / %d 个字段；分类待洗：%d 题；人工复核：%d 项"
          % (len({p["question_id"] for p in plans if p["texts"]}), text_fields, len(cat_plans), len(manual)))
    print("=" * 66)

    print("\n== 文本回洗清单（R10 裸数学变量）==")
    for plan in plans:
        for hit in plan["texts"]:
            print("\n--- 题目 #%s (%s)：命中 %d 处" % (plan["question_id"], hit["field"], hit["count"]))
            for sample in hit["samples"]:
                print("    ▸ " + _truncate(sample))
            for line in difflib.unified_diff(
                hit["before"].splitlines(), hit["after"].splitlines(), lineterm="", n=0
            ):
                if line.startswith(("---", "+++", "@@")):
                    continue
                print("    " + _truncate(line))

    print("\n== 分类归一清单（章/节/考向）==")
    if not cat_plans:
        print("  ✨ 无：存量分类字段已全部命中当前大纲。")
    for plan in cat_plans:
        print("\n--- 题目 #%s" % plan["question_id"])
        for label in plan["category"]["labels"]:
            print("    ▸ " + _truncate(label))

    print("\n== 人工复核清单（保守规则拦下、本脚本不改）：%d 项 ==" % len(manual))
    if not manual:
        print("  ✨ 无。")
    for qid, reason in manual:
        print("  - #%s：%s" % (qid, _truncate(reason)))


def write_diff_log(plans, manual, dry_run: bool):
    DIFF_LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = DIFF_LOG_DIR / (
        "bare-vars-category-diff-%s%s.log" % (stamp, ".dryrun" if dry_run else "")
    )
    lines = [
        "# 存量回洗（裸数学变量 + 教材分类字段）— %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
        "# database=%s  dry_run=%s  plans=%d  manual=%d"
        % (DATABASE_FILE, dry_run, len(plans), len(manual)),
        "",
    ]
    for plan in plans:
        for hit in plan["texts"]:
            lines.append("--- question #%s (%s) 命中 %d 处" % (plan["question_id"], hit["field"], hit["count"]))
            lines.append("--- BEFORE\n" + hit["before"])
            lines.append("--- AFTER\n" + hit["after"])
            lines.append("")
        if plan["category"]:
            lines.append("--- question #%s 分类归一" % plan["question_id"])
            lines.extend("### " + label for label in plan["category"]["labels"])
            lines.append("")
    for qid, reason in manual:
        lines.append("!!! 人工复核 question #%s：%s" % (qid, reason))
        lines.append("")
    log_path.write_text("\n".join(lines), encoding="utf-8")
    return log_path


def _primary_slot(db, question_id: int, version_code: str):
    """取主槽位（sort_order == 0）；老数据可能留 NULL，按 0 处理。"""
    for row in (
        db.query(QuestionTopic)
        .filter(
            QuestionTopic.question_id == question_id,
            QuestionTopic.version_code == version_code,
        )
        .all()
    ):
        if (row.sort_order or 0) == 0:
            return row
    return None


def apply_category(db, question, plan, version_code: str) -> bool:
    """分类真改了：questions 字段与两张镜像表一起跟上，附加槽位一律不碰。"""
    before = plan["before"]
    if any(_name(getattr(question, key)) != before[key] for key in CATEGORY_FIELDS[:3]):
        print("  ⚠️ 跳过 #%s 分类：扫描后章节字段已被并发编辑" % question.id)
        return False
    if _name(question.topic_id) != plan["topic_id_before"]:
        print("  ⚠️ 跳过 #%s 分类：扫描后考向 ID 已被并发编辑" % question.id)
        return False

    after = plan["after"]
    slot = _primary_slot(db, question.id, version_code)
    if slot is None:
        # 唯一约束落在 (题, 版本, 册, 章, 节, 考向 ID)：新主槽位若与某个附加槽位撞车，
        # 整题分类放弃改写，绝不让落库卡在约束冲突上。
        wanted = (
            after["category_compulsory"],
            after["category_chapter"],
            after["category_knowledge"],
            plan["topic_id_after"],
        )
        for row in (
            db.query(QuestionTopic)
            .filter(
                QuestionTopic.question_id == question.id,
                QuestionTopic.version_code == version_code,
            )
            .all()
        ):
            if (row.compulsory or "", row.chapter or "", row.knowledge or "", row.topic_id or "") == wanted:
                print("  ⚠️ 跳过 #%s 分类：主槽位缺失且新槽位与附加考向撞车" % question.id)
                return False

    question.category_compulsory = after["category_compulsory"]
    question.category_chapter = after["category_chapter"]
    question.category_knowledge = after["category_knowledge"]
    question.topic_id = plan["topic_id_after"]

    mirror = (
        db.query(QuestionCurriculum)
        .filter(
            QuestionCurriculum.question_id == question.id,
            QuestionCurriculum.version_code == version_code,
        )
        .first()
    )
    if mirror is None:
        mirror = QuestionCurriculum(question_id=question.id, version_code=version_code)
        db.add(mirror)
    mirror.compulsory = after["category_compulsory"]
    mirror.chapter = after["category_chapter"]
    mirror.knowledge = after["category_knowledge"]

    if slot is None:
        slot = QuestionTopic(
            question_id=question.id, version_code=version_code, sort_order=0
        )
        db.add(slot)
    slot.compulsory = after["category_compulsory"]
    slot.chapter = after["category_chapter"]
    slot.knowledge = after["category_knowledge"]
    slot.topic_id = plan["topic_id_after"]
    return True

def rebuild_fingerprints(db, question_ids, questions_by_id):
    """受影响题目先删旧指纹行，再按回洗后的新内容重算（口径同 #30）。"""
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
            print("  ⚠️ 题目 #%s 指纹重算失败（%s），保留删除态" % (question.id, type(exc).__name__))
            continue
        upsert_question_fingerprint(db, question, fingerprint)
        rebuilt += 1
    return deleted, rebuilt


def verify_clean(db, curriculum) -> tuple:
    """落库后全库复检：再跑一遍计划，期望零剩余（顺带证明这条链幂等）。"""
    rows = db.query(Question).order_by(Question.id).all()
    plans, _manual = plan_wash(rows, curriculum)
    remaining_text = sum(len(plan["texts"]) for plan in plans)
    remaining_cat = len([plan for plan in plans if plan["category"]])
    for plan in plans:
        for hit in plan["texts"]:
            print("  ⚠️ 复检仍有裸变量：#%s (%s) %d 处" % (plan["question_id"], hit["field"], hit["count"]))
        if plan["category"]:
            print("  ⚠️ 复检分类仍可归一：#%s %s" % (plan["question_id"], plan["category"]["labels"]))
    return remaining_text, remaining_cat


def migrate(dry_run: bool, *, retain: int = 10, export: bool = True) -> int:
    # 真实大纲必须走 load_or_init_metadata：它会把当前教材树灌进 curriculums 缓存，
    # 少了这一步 get_current_curriculum 会退回内置预设，章名对不上就全盘误判。
    load_or_init_metadata()
    curriculum = get_current_curriculum() or {}
    version_code = get_active_version_code()

    scan_db = SessionLocal()
    try:
        rows = scan_db.query(Question).order_by(Question.id).all()
        plans, manual = plan_wash(rows, curriculum)
        total = len(rows)
    finally:
        scan_db.close()

    print_report(plans, manual, total, version_code, dry_run)
    log_path = write_diff_log(plans, manual, dry_run)
    print("\n📝 完整 diff 日志：%s" % log_path)

    if dry_run:
        print("\n🔒 DRY-RUN 到此为止：未备份、未改库、未刷新导出。确认清单后加 --apply 落库。")
        return 0
    if not plans:
        print("\n✨ 库中已无这两类脏数据，无需回洗。")
        return 0

    print("\n💾 正在创建完整备份 ...")
    backup_path = create_full_backup(retention=retain)
    print("   备份完成：%s" % backup_path)

    # 与服务启动同一套 schema 入口，保证列齐全、行为逐字一致。
    print("🧱 正在确认数据库 schema（init_db）...")
    init_db()

    db = SessionLocal()
    try:
        questions_by_id = {q.id: q for q in db.query(Question).all()}
        changed_ids: list = []
        text_fields = 0
        cat_count = 0
        skipped = 0
        for plan in plans:
            question = questions_by_id.get(plan["question_id"])
            if question is None:
                skipped += 1
                continue
            touched = False
            for hit in plan["texts"]:
                current = getattr(question, hit["field"], "") or ""
                if current != hit["before"]:
                    # 扫描与写库之间被并发编辑过：放弃这一处，绝不覆盖新内容。
                    print("  ⚠️ 跳过 #%s (%s)：内容在扫描后已变化" % (question.id, hit["field"]))
                    skipped += 1
                    continue
                setattr(question, hit["field"], hit["after"])
                text_fields += 1
                touched = True
            if plan["category"] and apply_category(db, question, plan, version_code):
                cat_count += 1
                touched = True
            if touched:
                changed_ids.append(question.id)
        db.flush()

        deleted, rebuilt = rebuild_fingerprints(db, sorted(set(changed_ids)), questions_by_id)
        db.commit()
        print(
            "\n✅ 已回洗 %d 个文本字段、%d 题分类（改动涉及 %d 道题，跳过 %d 处）"
            % (text_fields, cat_count, len(set(changed_ids)), skipped)
        )
        print("🗂  指纹：删除旧 %d 行，按新内容重算 %d 条" % (deleted, rebuilt))

        remaining_text, remaining_cat = verify_clean(db, curriculum)
        if remaining_text or remaining_cat:
            print("⚠️ 复检仍有 %d 个字段 / %d 题分类命中，请人工核对。" % (remaining_text, remaining_cat))
        else:
            print("🧪 复检通过：全库零剩余，规则幂等。")

        if export:
            print("\n🔄 正在同步导出最新数据至 JSON 备份文件与 AI 专属题库 ...")
            export_database_to_files(db)
            print("🎉 自动同步完成！")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="存量回洗：裸数学变量（#36）与教材分类字段（章/节/考向）"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="正式落库：先完整备份，再改字段、同步镜像表、重算指纹、刷新导出（缺省只 dry-run）",
    )
    parser.add_argument("--no-export", action="store_true", help="跳过 JSON/Markdown 同步导出")
    parser.add_argument("--retain", type=int, default=10, help="备份保留最近多少份（默认 10）")
    args = parser.parse_args()
    return migrate(
        dry_run=not args.apply,
        retain=args.retain,
        export=not args.no_export,
    )


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    raise SystemExit(main())