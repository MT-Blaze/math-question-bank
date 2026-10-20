"""全库选择题选项标号格式统一脚本。

默认 dry-run：扫描全库 content 字段，统计含非标号题目的数量与分布，列出真实样例。
--apply：实际写库，创建完整备份并刷新 JSON 同步导出。

用法:
    python3 -m scripts.normalize_choice_labels          # dry-run
    python3 -m scripts.normalize_choice_labels --apply  # 正式洗库
"""

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

# Windows CP936 兼容：stdout/stderr 设为 UTF-8
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

# 项目根目录锚定
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mathbank.paths import DATABASE_FILE, DATA_BACKUP_DIR
from mathbank.backup import create_full_backup
from mathbank.sync_helper import export_database_to_files


# 匹配 choices 环境内的 \item 行上的非标号标点
# 覆盖：A. / A、/ A，/ A: / A： → 需要改为 A．
_CHOICE_LABEL_NONSTANDARD_RE = re.compile(
    r"\\begin\{choices\}(.*?)\\end\{choices\}",
    re.DOTALL,
)

_ITEM_LABEL_RE = re.compile(
    r"(\\item\s+)([A-H])([\.、，,:：])",
)


def scan_content(content: str) -> dict[str, int]:
    """扫描一段 content，返回各非标号类型的出现次数。"""
    counts = {
        "dot": 0,      # .
        "dunhao": 0,   # 、
        "comma": 0,    # ，
        "colon_half": 0,  # :
        "colon_full": 0,  # ：
    }
    for env_match in _CHOICE_LABEL_NONSTANDARD_RE.finditer(content):
        env_body = env_match.group(1)
        for item_match in _ITEM_LABEL_RE.finditer(env_body):
            punct = item_match.group(3)
            if punct == ".":
                counts["dot"] += 1
            elif punct == "、":
                counts["dunhao"] += 1
            elif punct == "，":
                counts["comma"] += 1
            elif punct == ":":
                counts["colon_half"] += 1
            elif punct == "：":
                counts["colon_full"] += 1
    return counts


def normalize_content(content: str) -> str:
    """把 content 里 choices 环境内的选项标号统一为全角点。"""
    def _replace_env(match: re.Match) -> str:
        prefix = match.group(1)
        body = match.group(2)
        suffix = match.group(3)
        normalized_body = _ITEM_LABEL_RE.sub(r"\1\2．", body)
        return f"{prefix}{normalized_body}{suffix}"

    return _CHOICE_LABEL_NONSTANDARD_RE.sub(_replace_env, content)


def main():
    parser = argparse.ArgumentParser(description="全库选择题选项标号格式统一")
    parser.add_argument("--apply", action="store_true", help="实际写库（默认只扫描）")
    args = parser.parse_args()

    db_path = DATABASE_FILE
    if not db_path.exists():
        print(f"❌ 数据库不存在: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 获取所有选择题
    cursor.execute("SELECT id, content FROM questions WHERE question_type IN ('single_choice', 'multi_choice')")
    rows = cursor.fetchall()
    conn.close()

    total_choices = len(rows)
    print(f"=== 全库选择题选项标号格式扫描 ===")
    print(f"总题数: {total_choices}")

    # 统计
    affected_ids = []
    samples = []
    total_counts = {
        "dot": 0,
        "dunhao": 0,
        "comma": 0,
        "colon_half": 0,
        "colon_full": 0,
    }

    for row in rows:
        qid = row["id"]
        content = row["content"] or ""
        counts = scan_content(content)
        has_nonstandard = sum(counts.values()) > 0

        if has_nonstandard:
            affected_ids.append(qid)
            for key in total_counts:
                total_counts[key] += counts[key]
            if len(samples) < 20:  # 最多展示 20 个样例
                samples.append((qid, content[:200]))

    affected_count = len(affected_ids)
    print(f"含非标号题目的题目: {affected_count} ({affected_count / total_choices * 100:.1f}%)" if total_choices else "含非标号题目的题目: 0")
    print()

    if affected_count == 0:
        print("✅ 全库选择题选项标号已全部统一为标准全角点 A．")
        return

    print("不规范分布:")
    label_map = {
        "dot": "半角点 (.)",
        "dunhao": "顿号 (、)",
        "comma": "全角逗号 (，)",
        "colon_half": "半角冒号 (:)",
        "colon_full": "全角冒号 (：)",
    }
    for key, label in label_map.items():
        count = total_counts[key]
        if count > 0:
            print(f"  - {label}: {count} 处")
    print()

    print(f"样例（前 {len(samples)} 题）:")
    for qid, preview in samples:
        # 提取第一个 choices 环境作为样例
        env_match = _CHOICE_LABEL_NONSTANDARD_RE.search(preview)
        if env_match:
            env_snippet = env_match.group(0)[:150]
            print(f"  #{qid}: {env_snippet}...")
    print()

    print(f"预计修改题数: {affected_count}")

    if not args.apply:
        print("\n⚠️  当前为 dry-run 模式，未做任何修改。")
        print("确认无误后，运行以下命令正式洗库：")
        print(f"  python3 -m scripts.{Path(__file__).stem} --apply")
        return

    # --- Apply 模式 ---
    print("\n🔄 开始正式洗库...")

    # 1. 创建完整备份
    print("  [1/4] 创建完整备份...")
    try:
        backup_zip = create_full_backup(DATA_BACKUP_DIR)
        print(f"  ✅ 备份完成: {backup_zip.name}")
    except Exception as e:
        print(f"  ❌ 备份失败: {e}")
        sys.exit(1)

    # 2. 逐题更新
    print("  [2/4] 逐题更新 content...")
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    cursor = conn.cursor()

    updated_count = 0
    for qid in affected_ids:
        cursor.execute("SELECT content FROM questions WHERE id=?", (qid,))
        row = cursor.fetchone()
        if not row:
            continue
        old_content = row[0] or ""
        new_content = normalize_content(old_content)
        if new_content != old_content:
            cursor.execute("UPDATE questions SET content=? WHERE id=?", (new_content, qid))
            updated_count += 1

    conn.commit()
    print(f"  ✅ 已更新 {updated_count} 题")

    # 3. 刷新指纹（跳过，因为这是 ORM 层操作；用户可在服务重启后由后台维护线程自动处理）
    print("  [3/4] 跳过指纹刷新（服务重启后自动处理）")

    # 4. 刷新 JSON 同步导出
    print("  [4/4] 刷新 JSON 同步导出...")
    try:
        export_database_to_files()
        print("  ✅ JSON 同步导出完成")
    except Exception as e:
        print(f"  ⚠️  JSON 同步导出失败: {e}")

    conn.close()
    print(f"\n✅ 洗库完成！共修改 {updated_count} 题。")
    print(f"备份文件: {backup_zip}")


if __name__ == "__main__":
    main()
