#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""全库 LaTeX 脏数据清洗（待办 #30）。

把「导出前预检自愈层」（``mathbank/latex_preflight.py``）目前在**每次导出时**反复
改写的脏数据，一次性还原到源头库里：清洗完成后预检层回归它应有的角色——兜底网，
而不是每卷都触发 Toast 的常态改写。

三类处置：

  1. **自动修复**（落库，并重算 ``question_fingerprints``）：
     * 预检层八条规则的全部确定性改写（``\left/\right`` 被中文打断并回、宏名紧贴汉字
       补 ``{}``、行式环境丢失的行分隔 ``\\`` 补回、数学区 Unicode 符号换等价宏、
       行内公式跨段并回、裸回车归一化等）——直接复用 ``repair_latex_before_compile``
       的产物，保证与编译侧逐字一致；
     * ``\nRightarrow`` 家族：单个（非段间双换行的）CRLF/LF 紧贴 ``Rightarrow`` 前，
       是入库转义解码把 ``\n`` 写成真实换行的确定性指纹（Windows 习惯再把 LF 放大成
       CRLF；正常段间换行必是双换行，被 ``(?<![\r\n])`` 排除），还原为 ``\nRightarrow``；
     * 补集 OCR 乱码家族：``\check{\partial}`` / ``\breve{\partial}`` / ``\text{痌}`` /
       ``(_U B)`` 还原为 ``\complement``（全库实测仅 #125 一题命中）。
  2. **题内定点裁决**（教师 2026-10-19 拍板，已内置）：#132「$ ##0.5」→「$ 或 0.5」，
     #329「}$##$\{」→「}$ $\{」。兜底检测仍在：若清洗后字段仍残留裸 ``#``，进人工清单。
  3. **观察项**（不动作）：正文 CRLF 存量惯例、``review`` 字段命中、【答案】前缀形态
     与双前缀扫描结果，仅汇报。

**--apply 落库前置动作**：先按 ``scripts/backup.py`` 口径创建完整备份；再调用
``mathbank.database.init_db()``——它是服务启动用的同一套 schema 入口（当前库 v25、
代码 v26，会先做官方迁移并自动留 pre-migration 快照），保证与线上行为逐字一致。

用法::

    python -m scripts.migrate_latex_dirty_data           # 默认 dry-run：只打印清单与 diff，不写库
    python -m scripts.migrate_latex_dirty_data --apply   # 备份 + 迁移 + 落库 + 重算指纹 + 刷新导出

注意：``--apply`` 会直接改 ``questions`` 表，请确认此时没有服务在并发编辑题目。
"""

from __future__ import annotations

import argparse
import difflib
import io
import re
import sqlite3
import sys
import time

from mathbank.backup import create_full_backup
from mathbank.database import Question, QuestionFingerprint, SessionLocal, init_db
from mathbank.latex_preflight import repair_latex_before_compile
from mathbank.paths import DATABASE_FILE, DATA_BACKUP_DIR, IS_TESTING, UPLOADS_DIR
from mathbank.question_duplicate_service import (
    fingerprint_for_question,
    upsert_question_fingerprint,
)
from mathbank.sync_helper import export_database_to_files

# 刻意不从 main 导入：main 在模块导入期抢 runtime 锁，线上服务持锁时会 PermissionError。
# 取值口径与 scripts/migrate_fillin.py 一致。
UPLOAD_DIR = str(UPLOADS_DIR)
UPLOAD_DIR_REL = "static/test_uploads" if IS_TESTING else "static/uploads"

DIFF_LOG_DIR = DATA_BACKUP_DIR / "latex_dirty_migration"
CONSOLE_LINE_CHARS = 260  # 控制台 diff 行截断长度，完整内容进日志文件

# #30 的清洗范围：题干与答案两个字段；review 只扫描汇报，不自动改写。
SCAN_FIELDS = ("content", "answer_markdown")

# 被转义解码劈断的 \nRightarrow：单个换行 + "Rightarrow"。
_BROKEN_IMPLIES_RE = re.compile(r"(?<![\r\n])(?:\r\n|\n)(?=Rightarrow)")

# 补集 OCR 乱码：全库仅 #125 命中（2026-10-19 体检），模式本身按全库通用写，
# 后续再从同类 OCR 源导入时同一脚本直接兜住。
_COMPLEMENT_GARBLE_REPLACEMENTS = (
    (re.compile(r"\\check\{\\partial\}"), "\\complement"),
    (re.compile(r"\\breve\{\\partial\}"), "\\complement"),
)

# 题内定点修复：痌 是 ∁（补集号）的 OCR 乱码，下标 _U 已丢失，只能按题面上下文
# 还原（全库 411 题仅此一处；答案判定 AC 与四个选项的补集语义互相印证）。
_QUESTION_SPECIFIC_FIXES = {
    125: (
        (
            "$(\\text{痌} A)\\cap (_U B)$",
            "$(\\complement_{U} A)\\cap (\\complement_{U} B)$",
        ),
    ),
    # 待办 #30 教师裁决（2026-10-19）：两处裸 ## 是「或」的误录，按裁决改写。
    # #329 用「$ $」隔开两个数学段，避免删号后出现相邻「$$」的断裂形态。
    132: ((" ##0.5", " 或 0.5"),),
    329: (("$##$", "$ $"),),
}

# 裸 #：与预检层 R8 同判据（未被反斜杠转义的 #）。库级只上报，不自动改写——
# 「##0.5」这类多半是「或」的误录，改字是语义决定，教师拍板。
_BARE_HASH_RE = re.compile(r"(?<!\\)#")

# 历史【答案】双前缀：与 parsing_pipeline 的空壳判据同源，扫库确认是否还有存量。
_ANSWER_SHELL_RE = re.compile(
    r"【答案】[ \t　]*[：:。.、]?[ \t　*]*(?:\r?\n+[ \t　*]*)+(?=(?:\*\*)?【答案】)"
)
_ANSWER_ADJACENT_RE = re.compile(r"【答案】[\s\u3000*]{0,8}【答案】")


def repair_field(qid: int, value: str) -> tuple[str, list[str]]:
    """对一个字段跑完整清洗链，返回 ``(修复后文本, 命中规则说明)``。

    说明列表为空即「该字段不脏」——原样返回，绝不回写（避免把全库 CRLF 无声洗成 LF）。
    链序：先做库级定向还原（\nRightarrow、补集乱码、题内定点），再交给预检层链；
    预检层排在最后是刻意的——它的判据按 LF 计算，且 \nRightarrow 的证据（单个 CRLF）
    会被 R1 归一化抹掉，必须先还原再归一化。
    """
    if not value:
        return value, []
    labels: list[str] = []

    hits = len(_BROKEN_IMPLIES_RE.findall(value))
    text = _BROKEN_IMPLIES_RE.sub(lambda _m: "\\n", value)
    if hits:
        labels.append(r"还原入库时被转义解码劈断的 \nRightarrow：%d 处" % hits)

    for pattern, repl in _COMPLEMENT_GARBLE_REPLACEMENTS:
        text, hits = pattern.subn(lambda _m, _r=repl: _r, text)
        if hits:
            labels.append("补集 OCR 乱码还原 \\complement：%d 处" % hits)

    for old, new in _QUESTION_SPECIFIC_FIXES.get(qid, ()):
        if old in text:
            text = text.replace(old, new)
            labels.append("题内定点还原：%s → %s" % (old, new))

    repaired, notes = repair_latex_before_compile(text)
    if notes:
        labels.extend("预检层规则：%s" % note for note in notes)

    if not labels:
        return value, []
    return repaired, labels


def open_readonly() -> sqlite3.Connection:
    """以 mode=ro 打开题库文件：dry-run 全程只读，物理上杜绝误写。

    刻意绕开 ORM 扫描：库里存量 schema（v25）比代码（v26）少三列，ORM 全列 SELECT
    会直接报错；只读取需要的列即可，且不触发任何写路径。
    """
    uri = "file:" + DATABASE_FILE.resolve().as_posix() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def scan_database(con: sqlite3.Connection) -> tuple[list, list, list, int]:
    """全库扫描，返回 (自动修复清单, 人工复核清单, 观察项, 总题数)。"""
    changes: list = []
    manual: list = []
    observations: list = []
    review_hits = 0
    crlf_fields = 0
    shell_prefix = 0
    prefix_stats: dict[str, int] = {}

    rows = con.execute(
        "select id, content, answer_markdown, review from questions order by id"
    ).fetchall()
    for qid, content, answer_markdown, review in rows:
        values = {"content": content or "", "answer_markdown": answer_markdown or "", "review": review or ""}
        for field in SCAN_FIELDS:
            value = values[field]
            if "\r" in value:
                crlf_fields += 1
            after, labels = repair_field(qid, value)
            if labels:
                changes.append((qid, field, value, after, labels))
                probe = after
            else:
                probe = value
            hash_count = len(_BARE_HASH_RE.findall(probe))
            if hash_count:
                manual.append(
                    (qid, field, "裸 #（LaTeX 保留字符，%d 处，疑似「或」的误录）" % hash_count, probe)
                )
            # 自洽校验：修复产物必须不再命中本脚本与预检层任何一条规则。
            after2, labels2 = repair_field(qid, after)
            if labels2 or after2 != after:
                manual.append((qid, field, "修复链不自洽（二次跑仍命中）：%s" % labels2, after))
            if _ANSWER_SHELL_RE.search(value) or _ANSWER_ADJACENT_RE.search(value):
                shell_prefix += 1
                manual.append((qid, field, "【答案】双前缀/空壳前缀", value))

        _after, labels = repair_field(qid, values["review"])
        if labels:
            review_hits += 1
            manual.append((qid, "review", "review 字段命中清洗规则（#30 范围外，仅汇报）", values["review"]))

        head = values["answer_markdown"].lstrip("\r\n 　")
        if not values["answer_markdown"].strip():
            key = "（空答案）"
        elif head.startswith("【答案】"):
            key = "【答案】裸前缀"
        elif head.startswith("\\textbf{【答案】}") or head.startswith("**【答案】**"):
            key = "【答案】带排版包装（\\textbf 或 **）"
        elif "【答案】" in head[:60]:
            key = "【答案】不在开头"
        else:
            key = "无【答案】前缀"
        prefix_stats[key] = prefix_stats.get(key, 0) + 1

    observations.append("触发自动修复的字段：%d 个（涉及 %d 道题）"
                        % (len(changes), len({c[0] for c in changes})))
    observations.append("需人工复核：%d 项" % len(manual))
    observations.append("【答案】双前缀/空壳前缀存量：%d 处" % shell_prefix)
    observations.append("review 字段命中：%d 个（仅汇报，不自动改）" % review_hits)
    observations.append("含 CR 的字段（存储惯例，非脏数据）：%d 个" % crlf_fields)
    for key, cnt in sorted(prefix_stats.items(), key=lambda kv: -kv[1]):
        observations.append("答案前缀形态「%s」：%d 题" % (key, cnt))
    return changes, manual, observations, len(rows)


def _truncate(line: str) -> str:
    if len(line) <= CONSOLE_LINE_CHARS:
        return line
    return line[:CONSOLE_LINE_CHARS] + "…"


def print_report(changes, manual, observations, total: int, dry_run: bool) -> None:
    print("=" * 66)
    print("🔍 DRY-RUN（只扫描，不写库）" if dry_run else "🚀 正式清洗（将写库）")
    print("  database = %s" % DATABASE_FILE)
    print("  题库总题数 = %d" % total)
    print("=" * 66)

    print("\n== 观察项 ==")
    for line in observations:
        print("  · " + line)

    print("\n== 自动修复清单：%d 个字段 ==" % len(changes))
    for qid, field, before, after, labels in changes:
        print("\n--- 题目 #%s (%s)" % (qid, field))
        for label in labels:
            print("    ▸ " + _truncate(label))
        for line in difflib.unified_diff(
            before.splitlines(), after.splitlines(), lineterm="", n=0
        ):
            if line.startswith(("---", "+++", "@@")):
                continue
            print("    " + _truncate(line))

    print("\n== 人工复核清单（本脚本不改字）：%d 项 ==" % len(manual))
    if not manual:
        print("  ✨ 无。")
    for qid, field, reason, value in manual:
        needle = value.find("#") if "#" in reason else value.find("【答案】")
        start = max(0, needle)
        print("  - #%s (%s)：%s  片段：%s" % (qid, field, reason, _truncate(repr(value[start:start + 120]))))


def write_diff_log(changes, manual, dry_run: bool):
    from pathlib import Path

    DIFF_LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = DIFF_LOG_DIR / ("latex-dirty-diff-%s%s.log" % (stamp, ".dryrun" if dry_run else ""))
    lines = [
        "# 全库 LaTeX 脏数据清洗 diff — %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
        "# database=%s  dry_run=%s  fields=%d" % (DATABASE_FILE, dry_run, len(changes)),
        "",
    ]
    for qid, field, before, after, labels in changes:
        lines.append("--- question #%s (%s)" % (qid, field))
        for label in labels:
            lines.append("### " + label)
        lines.append("--- BEFORE\n" + before)
        lines.append("--- AFTER\n" + after)
        lines.append("")
    for qid, field, reason, value in manual:
        lines.append("!!! 人工复核 question #%s (%s)：%s" % (qid, field, reason))
        lines.append("!!! 现状片段：" + value[:400])
        lines.append("")
    log_path.write_text("\n".join(lines), encoding="utf-8")
    return log_path


def rebuild_fingerprints(db, question_ids, questions_by_id):
    """受影响题目先删旧指纹行，再按清洗后的新内容重算（口径同 migrate_fillin）。"""
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


def verify_clean(db) -> int:
    """落库后全库复检：任何字段再触发清洗链即计数。"""
    remaining = 0
    for question in db.query(Question).all():
        for field in SCAN_FIELDS:
            _after, labels = repair_field(question.id, getattr(question, field, "") or "")
            if labels:
                remaining += 1
                print("  ⚠️ 复检仍有命中：#%s (%s) %s" % (question.id, field, labels))
    return remaining


def migrate(dry_run: bool, *, retain: int = 10, export: bool = True) -> int:
    con = open_readonly()
    try:
        changes, manual, observations, total = scan_database(con)
    finally:
        con.close()
    print_report(changes, manual, observations, total, dry_run)
    log_path = write_diff_log(changes, manual, dry_run)
    print("\n📝 完整 diff 日志：%s" % log_path)

    if dry_run:
        print("\n🔒 DRY-RUN 到此为止：未备份、未改库、未刷新导出。确认清单后加 --apply 落库。")
        return 0
    if not changes:
        print("\n✨ 库中已无预检层会改写的存量脏数据，无需迁移。")
        return 0

    print("\n💾 正在创建完整备份 ...")
    backup_path = create_full_backup(retention=retain)
    print("   备份完成：%s" % backup_path)

    # 与服务启动同一套 schema 入口：库 v25 → 代码 v26 会先做官方迁移（自带 pre-migration 快照）。
    print("🧱 正在确认数据库 schema（init_db，与服务启动路径一致）...")
    init_db()

    db = SessionLocal()
    try:
        questions_by_id = {q.id: q for q in db.query(Question).all()}
        changed_ids: list[int] = []
        skipped = 0
        for qid, field, before, after, _labels in changes:
            question = questions_by_id.get(qid)
            if question is None:
                skipped += 1
                continue
            current = getattr(question, field, "") or ""
            if current != before:
                # 扫描与写库之间题目被并发编辑过：放弃这一处，绝不覆盖新内容。
                print("  ⚠️ 跳过 #%s (%s)：内容在扫描后已变化" % (qid, field))
                skipped += 1
                continue
            setattr(question, field, after)
            if qid not in changed_ids:
                changed_ids.append(qid)
        db.flush()

        deleted, rebuilt = rebuild_fingerprints(db, sorted(changed_ids), questions_by_id)
        db.commit()
        print("\n✅ 已修复 %d 个字段，涉及 %d 道题（ids: %s），跳过 %d 处"
              % (len(changes) - skipped, len(changed_ids), sorted(changed_ids), skipped))
        print("🗂  指纹：删除旧 %d 行，按新内容重算 %d 条" % (deleted, rebuilt))

        remaining = verify_clean(db)
        if remaining:
            print("⚠️ 复检仍有 %d 个字段命中，请人工核对。" % remaining)
        else:
            print("🧪 复检通过：全库 content/answer_markdown 零触发预检层。")

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
    parser = argparse.ArgumentParser(description="全库 LaTeX 脏数据清洗（待办 #30）")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="正式落库：先完整备份，再改字段、重算指纹、刷新导出（缺省只 dry-run）",
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
