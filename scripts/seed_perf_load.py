"""向独立压测库注入海量合成题，用于组卷/列表接口的架构级压测。

用法（从项目根目录）：
    python -m scripts.seed_perf_load --target 10000

安全红线：**永远不碰教师真实题库**。脚本只写 `--db` 指定的压测库；该库不存在时，
用 SQLite 备份 API 从 `--source`（默认真实库）整库复制一份——备份 API 会连带 WAL
里尚未落盘的页一起搬走，比 `shutil.copy` 半截 `.db` 文件可靠得多。

合成题刻意贴真实形态：四种题型、题干均值 ~225 字（长尾到 1000 字）、答案 ~335 字、
选择题走 `\begin{choices}` 环境而不是 options JSON、章节/难度/年级取值直接从真实库
抽样——否则压出来的数不能代表线上。
"""

from __future__ import annotations

import argparse
import datetime as dt
import random
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path

SEED_SOURCE_MARKER = "压测合成题库"
DEFAULT_STRESS_DB = "math_question_bank_stress.db"

# 与 mathbank.paths 同名的环境变量：必须在导入 mathbank 之前设好，
# 因为 DATABASE_FILE 是在模块导入期一次定值的。
DATABASE_ENV_VAR = "MATHBANK_DATABASE_FILE"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _resolve_path(name: str) -> Path:
    path = Path(name)
    return path if path.is_absolute() else _project_root() / path


@contextmanager
def closing_pair(source_db: Path, stress_db: Path):
    src = sqlite3.connect(source_db)
    dst = sqlite3.connect(stress_db)
    try:
        yield src, dst
    finally:
        src.close()
        dst.close()


def _clone_source_into(stress_db: Path, source_db: Path) -> None:
    """整库复制真实库，给压测库一个结构、教材树、既有真题都在的起点。"""

    if not source_db.exists():
        raise SystemExit(f"源库不存在：{source_db}")
    with closing_pair(source_db, stress_db) as (src, dst):
        src.backup(dst)


# 合成题模板用 «name» 占位符而不是 str.format：LaTeX 正文里满是 `{}` 与 `\left\{`，
# 任何 `{{`/`}}` 转义都会在几轮改写后静默错位，自定义占位符不用转义、读起来也仍是 LaTeX。
STEM_TEMPLATES = {
    "single_choice": (
        "已知函数 $f(x)=\\dfrac{«a»}{«b»}x^{2}-«c»x+«d»$ 在区间 $[«e»,+\\infty)$ 上单调递增，"
        "则实数 $a$ 的取值范围是\\paren （其中 $a,b\\in\\mathbf{N}^*$ 且 $b\\leqslant 5$）",
        "设集合 $A=\\{x\\mid x^2-«c»x+«d»=0\\}$，$B=\\{x\\mid ax-2=0\\}$，若 $A\\cup B=A$，"
        "则实数 $a$ 的所有取值构成的集合为\\paren",
        "如图, 在正方形 $ABCD$ 中, $AB = «b»$, 点 $M$ 从点 $A$ 出发, 沿 $A \\rightarrow B \\rightarrow C \\rightarrow D \\rightarrow A$ "
        "方向, 以每秒 «c» 个单位的速度在正方形的边上运动; 点 $N$ 从点 $B$ 出发, 沿 $B \\rightarrow C \\rightarrow D \\rightarrow A$ 的方向, "
        "以每秒 «d» 个单位的速度运动. 两点同时出发, 记运动时间为 $t$ (单位: 秒), $\\Delta AMN$ 的面积为 $f(t)$ "
        "(规定 $A, M, N$ 共线时其面积为零), 则 $y = f(t)$ 的图象为\n\\begin{choices}\n    \\item \\includegraphics[width=2.6cm]{uploads/seed_«e».png}\n",
    ),
    "multi_choice": (
        "已知函数 $f(x)=\\log_{«a»}(x^{2}-«c»x+«d»)$，下列说法中正确的有\\paren （多选，全部选对得满分，部分选对得部分分）",
        "已知 $a>b>0$，且 $a+b=«c»$，则下列不等式成立的是\\paren",
    ),
    "fill_in_blank": (
        "定义集合 $P=\\{x\\mid a\\leqslant x\\leqslant b\\}$ 的“长度”是 $b-a$，其中 $a,b\\in\\mathbf{R}$。"
        "已知集合 $M=\\left\\{x\\mid \\dfrac{«a»}{«b»}\\leqslant x\\leqslant \\dfrac{«c»}{«d»}\\right\\}$，"
        "则集合 $M$ 的长度为 \\fillin。",
        "函数 $f(x)=\\sin \\left(«a»x+\\dfrac{\\pi}{«b»}\\right)$ 的最小正周期为 \\fillin，"
        "其图象的对称轴方程为 \\fillin。",
    ),
    "detailed_answer": (
        "牛顿在《流数法》一书中，利用迭代思想给出了一种求高次代数方程近似解的方法：牛顿法。"
        "具体步骤如下：设 $r$ 是函数 $y=f(x)$ 的一个零点，任意选取 $x_0$ 作为 $r$ 的初始近似值，"
        "曲线 $y=f(x)$ 在点 $\\left(x_0,f\\left(x_0\\right)\\right)$ 处的切线为 $l_1$，设 $l_1$ 与 $x$ 轴交点的横坐标为 $x_1$，"
        "并称 $x_1$ 为 $r$ 的 1 次近似值；一般地，曲线在点 $\\left(x_n,f\\left(x_n\\right)\\right)$ 处的切线为 $l_{n+1}$，"
        "记 $l_{n+1}$ 与 $x$ 轴交点的横坐标为 $x_{n+1}$。已知 $f(x)=«a»x^3-x+«b»$。"
        "(1) 当 $a=«a»$ 时，求曲线在点 $\\left(f(0),f(0)\\right)$ 处的切线方程；"
        "(2) 若 $a=2$，且 $x_0=«c»$，求 $x_1$、$x_2$，并估计 $f(x)$ 在 $(0,1)$ 内的零点（精确到 0.01）；"
        "(3) 证明：当 $x_0>«b»$ 时，数列 $\\{x_n\\}$ 单调递减且收敛于 $f$ 的唯一零点。",
        "已知等差数列 $\\{a_n\\}$ 的前 $n$ 项和为 $S_n$，且 $a_3=«a»$，$S_5=«c»$。"
        "(1) 求数列 $\\{a_n\\}$ 的通项公式；(2) 设 $b_n=«b»\\cdot «a»^{n}$，求数列 $\\{b_n\\}$ 的前 $n$ 项和 $T_n$；"
        "(3) 若对任意 $n\\in\\mathbf{N}^*$，不等式 $T_n\\leqslant «d»\\,b_n+«e»$ 恒成立，求实数 $d$ 的取值范围。",
    ),
}

CHOICE_BLOCK = (
    "\\end{choices}\n"
    "A. $\\left[-\\dfrac{«a»}{«b»},0\\right)$\n"
    "B. $\\left(0,\\dfrac{«c»}{«d»}\\right]$\n"
    "C. $\\left[-\\dfrac{«a»}{«b»},0\\right)\\cup\\left(0,\\dfrac{«c»}{«d»}\\right]$\n"
    "D. $\\mathbf{R}$"
)

ANSWER_TEMPLATES = {
    "single_choice": "【答案】«letter»\n\n【解析】\n\n由题意得 $a=«a»$，故 $f(x)=\\dfrac{«a»}{«b»}x^2-«c»x+«d»$ 的对称轴为 $x=\\dfrac{«b»}{2«a»}$。\n要使 $f$ 在 $[«e»,+\\infty)$ 上单调递增，需 $\\dfrac{«b»}{2«a»}\\leqslant «e»$，解得 «letter» 项符合。\n\n【点评】本题考查二次函数单调性与含参讨论，易错点在于漏掉开口方向为 0 的情形。",
    "multi_choice": "【答案】«letter»\n\n【解析】\n\n逐项验证：A 取 $x=«a»$ 成立；B 由 $a>b>0$ 得 $\\dfrac{1}{a}<\\dfrac{1}{b}$，故 B 错误；\nC 由均值不等式 $a+b\\geqslant 2\\sqrt{ab}$ 可得；D 需分类讨论。\n\n【点评】多选题的评分规则要求全部选对，宁缺毋滥。",
    "fill_in_blank": "【答案】$\\left[\\dfrac{«a»}{«b»},\\dfrac{«c»}{«d»}\\right)$\n\n【解析】\n\n由定义直接计算长度 $b-a=\\dfrac{«c»}{«d»}-\\dfrac{«a»}{«b»}=\\dfrac{«e»}{«b»«d»}$，\n再结合数轴可得取值范围。\n\n【点评】新定义型填空题，关键是严格按定义翻译条件。",
    "detailed_answer": "【答案】见解析\n\n【解析】\n\n(1) $f(0)=«b»$，$f'(x)=«a»x^2-1$，故 $f'(0)=-1$，切线方程为 $y=-x+«b»$。\n(2) 牛顿迭代公式 $x_{n+1}=x_n-\\dfrac{f(x_n)}{f'(x_n)}$，代入 $x_0=«c»$ 得 $x_1=\\dfrac{«c»^3+«b»}{3«c»^2-1}\\approx «e».5$，继续迭代得 $x_2\\approx «a»\\!7$。\n(3) 由 $f$ 在 $(«b»,+\\infty)$ 上单调递增且 $f' > 0$，可证 $\\{x_n\\}$ 递减并有下界，故收敛。",
}


def _render(template: str, values: dict) -> str:
    """按 «name» 占位符填充，绕开 str.format 与 LaTeX 花括号的冲突。"""

    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"«{key}»", str(value))
    return rendered


def _realistic_slots(db: sqlite3.Connection) -> list:
    """从真实库抽样 (册次, 章节, 小节, 年级) 组合，让过滤器压测有意义。"""

    rows = db.execute(
        "select category_compulsory, category_chapter, category_knowledge, applicable_grade, "
        "count(*) from questions group by 1,2,3,4 order by 5 desc limit 40"
    ).fetchall()
    slots = [row[:4] for row in rows if row[0]]
    if not slots:
        slots = [("必修一", "1. 集合与常用逻辑用语", "集合的基本运算", "g1")]
    return slots


def _difficulty_values(db: sqlite3.Connection) -> list:
    rows = db.execute("select difficulty, count(*) from questions group by 1 order by 2 desc").fetchall()
    return [row[0] for row in rows if row[0]] or ["L1", "L2", "L3", "L4"]


def _build_question(index: int, slots: list, difficulties: list, rng: random.Random):
    qtype = rng.choice(list(STEM_TEMPLATES))
    compulsory, chapter, knowledge, grade = rng.choice(slots)
    values = {
        "a": rng.randint(1, 5),
        "b": rng.randint(2, 6),
        "c": rng.randint(2, 9),
        "d": rng.randint(1, 7),
        "e": rng.randint(1, 4),
        "letter": rng.choice(["A", "B", "C", "D"]),
    }
    content = _render(rng.choice(STEM_TEMPLATES[qtype]), values)
    if qtype in {"single_choice", "multi_choice"}:
        content = content + _render(CHOICE_BLOCK, values)
    answer = _render(ANSWER_TEMPLATES[qtype], values)
    created = dt.datetime(2024, 1, 1) + dt.timedelta(minutes=index)
    return (
        content,
        answer,
        qtype,
        f"{SEED_SOURCE_MARKER}·{chapter}·第{index}题",
        f"{SEED_SOURCE_MARKER}",
        compulsory,
        chapter,
        knowledge,
        grade,
        rng.choice(difficulties),
        created.strftime("%Y-%m-%d %H:%M:%S"),
    )


def seed(stress_db: Path, source_db: Path, target: int, batch: int = 2000) -> dict:
    if not stress_db.exists():
        _clone_source_into(stress_db, source_db)

    rng = random.Random(20261019)
    started = time.perf_counter()
    with closing_pair(source_db, stress_db) as (_src, db):
        slots = _realistic_slots(db)
        difficulties = _difficulty_values(db)
        columns = {
            row[1]
            for row in db.execute("PRAGMA table_info(questions)").fetchall()
        }
        needed = {
            "content", "answer_markdown", "question_type", "source", "tags",
            "category_compulsory", "category_chapter", "category_knowledge",
            "applicable_grade", "difficulty", "created_at",
        }
        missing = needed - columns
        if missing:
            raise SystemExit(f"压测库缺列，无法注入：{sorted(missing)}")

        db.execute("delete from questions where source like ?", (f"{SEED_SOURCE_MARKER}%",))
        insert_sql = (
            "insert into questions ("
            "content, answer_markdown, question_type, source, tags, "
            "category_compulsory, category_chapter, category_knowledge, "
            "applicable_grade, difficulty, created_at) values (?,?,?,?,?,?,?,?,?,?,?)"
        )
        rows = [_build_question(i, slots, difficulties, rng) for i in range(1, target + 1)]
        for start in range(0, len(rows), batch):
            db.executemany(insert_sql, rows[start:start + batch])
            db.commit()
        db.commit()
        # 每条语句都用独立 cursor 并显式关闭：未取完的 sqlite3 cursor 会把 questions
        # 表钉住，后续 `pragma wal_checkpoint` 直接报 `database table is locked`。
        stats = {}
        for label, statement in (
            ("total", "select count(*) from questions"),
            ("seeded", f"select count(*) from questions where source like '{SEED_SOURCE_MARKER}%'"),
        ):
            cursor = db.cursor()
            cursor.execute(statement)
            stats[label] = int(cursor.fetchone()[0])
            cursor.close()
        analyze_cursor = db.cursor()
        analyze_cursor.execute("analyze")
        analyze_cursor.close()
        db.commit()
        total = stats["total"]
        seeded = stats["seeded"]

    # 索引自愈必须在这条写连接**关闭之后**再做：SQLAlchemy 另开一条连接建索引时，
    # 上面的写事务还没释放，SQLite 直接 `database is locked`。
    from mathbank.db_migrations import ensure_late_tables
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{stress_db.resolve().as_posix()}")
    with engine.begin() as connection:
        ensure_late_tables(connection)
    engine.dispose()

    return {
        "stress_db": str(stress_db),
        "seeded": seeded,
        "total_questions": total,
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "db_bytes": stress_db.stat().st_size,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="注入海量合成题到独立压测库")
    parser.add_argument("--target", type=int, default=10000, help="合成题数量（默认 10000）")
    parser.add_argument("--db", default=DEFAULT_STRESS_DB, help="压测库路径（不存在则从 --source 整库复制）")
    parser.add_argument("--source", default=None, help="源库路径，默认真实题库")
    parser.add_argument(
        "--real-db",
        default=str(_project_root() / "math_question_bank.db"),
        help="真实题库路径（只读，用作复制源）",
    )
    args = parser.parse_args(argv)

    stress_db = _resolve_path(args.db)
    if stress_db.resolve() == _resolve_path(args.real_db).resolve():
        # 防呆：绝不给真实库注题。
        raise SystemExit(f"压测库不能与真实库同一文件：{stress_db}")
    source_db = _resolve_path(args.source or args.real_db)

    report = seed(stress_db, source_db, args.target)
    print("注入完成:")
    for key, value in report.items():
        print(f"  {key:16}: {value}")
    print(f"  环境变量        : {DATABASE_ENV_VAR}={stress_db.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
