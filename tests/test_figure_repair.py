"""插图归属修复（mathbank/figure_repair）回归：以 2024 大庆卷第 4 题为标准脏形。

覆盖 R1 空选项回填、R2 解析重复图剔除、R3 缺失标号补全、image_layouts 播种，
以及「没把握就不动」的保守边界——修复器宁可不修，也绝不能把干净的题改坏。
"""

import re

from mathbank import figure_repair
from mathbank.pdf_local_split import plan_pdf_local_split

import pathlib

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "mineru_daqing_q4_full.md"


def _staged_markdown():
    """模拟 stage_images：images/<sha>.jpg 按首次出现顺序改写为 mineru_<task>_<n>.jpg。"""
    text = FIXTURE.read_text(encoding="utf-8")
    order = []

    def _repl(m):
        name = m.group(1)
        if name not in order:
            order.append(name)
        return "/static/uploads/mineru_1792417091_%d.jpg" % (order.index(name) + 1)

    return re.sub(r"images/([0-9a-f]{64}\.jpg)", _repl, text)


def _q(content, answer, qtype="single_choice"):
    return {"content": content, "answer_markdown": answer, "question_type": qtype}


def test_daqing_q4_repair_end_to_end():
    """真实卷面：B 补标号、解析首图回填 D、四张选项图全部播种布局。"""
    plan = plan_pdf_local_split(_staged_markdown(), None, False)
    assert plan.trustworthy
    blk = next(b for b in plan.all_blocks if "大致图象" in (b.content or ""))
    q = blk.to_parsed_question({})
    stray = re.search(
        r"(?m)^\s*!\[[^\]]*\]\((\S+)\)\s*$", q["answer_markdown"]
    ).group(1)

    notes = figure_repair.repair_parsed_questions([q])

    assert {r["rule"] for r in q["_figure_repair"]} == {"R1", "R3"}
    assert notes and any("R1" in n for n in notes)
    # D 回填：幽灵图从解析搬进题干，解析不再有任何整行图
    assert stray in q["content"]
    assert not re.search(r"(?m)^\s*!\[", q["answer_markdown"])
    # B 补标号：浮动图回到选项序列
    assert re.search(r"(?m)^\s*B．\s*$", q["content"])
    # 四张选项图 + 布局播种（值必须是枚举，键必须是正文里真实出现的图）
    assert len(re.findall(r"!\[[^\]]*\]\(", q["content"])) == 4
    assert len(q["image_layouts"]) == 4
    assert all(v == {"align": "center", "size": "small"} for v in q["image_layouts"].values())
    # 幂等：同一张卡再跑一遍不得产生任何新修复
    assert figure_repair.repair_parsed_questions([q]) == []


def test_r1_backfills_empty_option():
    q = _q(
        "函数图象（）\n\nA．\n![](/static/uploads/mineru_9_1.jpg)\n\n"
        "B．\n![](/static/uploads/mineru_9_2.jpg)\n\n"
        "C．\n![](/static/uploads/mineru_9_3.jpg)\n\nD．",
        "【答案】C\n\n【解析】\n\n![](/static/uploads/mineru_9_4.jpg)\n\n【分析】看图",
    )
    figure_repair.repair_parsed_questions([q])
    assert "mineru_9_4.jpg" in q["content"]
    assert not re.search(r"(?m)^\s*!\[", q["answer_markdown"])
    assert "【分析】看图" in q["answer_markdown"]


def test_r1_refuses_when_counts_mismatch():
    """图簇 2 张 vs 空选项 1 个：归属判不准，整题不动（保守红线）。"""
    content = "图（）\n\nA．\n![](/static/uploads/mineru_9_1.jpg)\n\nD．"
    answer = "【解析】\n\n![](/static/uploads/mineru_9_2.jpg)\n\n![](/static/uploads/mineru_9_3.jpg)\n\n【分析】x"
    q = _q(content, answer)
    assert figure_repair.repair_parsed_questions([q]) == []
    assert q["content"] == content and q["answer_markdown"] == answer


def test_r2_removes_duplicate_and_keeps_inline():
    """题干已有的图在解析里整行重复 → 删；嵌在文字行里的行内图不动。"""
    q = _q(
        "图（）\n\nA．\n![](/static/uploads/mineru_9_1.jpg)\n\n"
        "B．\n![](/static/uploads/mineru_9_2.jpg)\n\n"
        "C．\n![](/static/uploads/mineru_9_3.jpg)\n\n"
        "D．\n![](/static/uploads/mineru_9_4.jpg)",
        "【答案】A\n\n![](/static/uploads/mineru_9_1.jpg)\n\n"
        "详见 ![](/static/uploads/mineru_9_9.jpg) 的标注",
    )
    figure_repair.repair_parsed_questions([q])
    assert "mineru_9_1.jpg" not in q["answer_markdown"]
    assert "mineru_9_9.jpg" in q["answer_markdown"]  # 行内图保守保留


def test_r3_fills_single_gap():
    q = _q(
        "图（）\n\nA．\n![](/static/uploads/mineru_9_1.jpg)\n\n"
        "![](/static/uploads/mineru_9_2.jpg)\n\n"
        "C．\n![](/static/uploads/mineru_9_3.jpg)\n\n"
        "D．\n![](/static/uploads/mineru_9_4.jpg)",
        "【答案】A",
    )
    figure_repair.repair_parsed_questions([q])
    assert re.search(r"(?m)^\s*B．\s*$", q["content"])


def test_r3_refuses_two_floating_images():
    """A 与 D 之间浮两张图：谁属于 B/C 判不准，不动（保守红线）。"""
    content = (
        "图（）\n\nA．\n![](/static/uploads/mineru_9_1.jpg)\n\n"
        "![](/static/uploads/mineru_9_2.jpg)\n\n![](/static/uploads/mineru_9_3.jpg)\n\nD．\n"
        "![](/static/uploads/mineru_9_4.jpg)"
    )
    q = _q(content, "【答案】A")
    assert figure_repair.repair_parsed_questions([q]) == []
    assert q["content"] == content


def test_skips_latex_choices_content():
    """老路 AI 产物已是 \begin{choices} 结构：不是本模块的目标形态，整题跳过。"""
    content = (
        "图（）\n\\begin{choices}\n\\item ![](/static/uploads/mineru_9_1.jpg)\n"
        "\\end{choices}\n\nD．"
    )
    q = _q(content, "【解析】\n\n![](/static/uploads/mineru_9_4.jpg)")
    assert figure_repair.repair_parsed_questions([q]) == []


def test_skips_non_mineru_assets():
    """Word/原生 OCR 路线的图不带 mineru_ 前缀：分支闸门直接放行不动。"""
    content = "图（）\n\nA．\n![](/static/uploads/word_1.png)\n\nB．\n![](/static/uploads/word_2.png)\n\nC．\n![](/static/uploads/word_3.png)\n\nD．"
    q = _q(content, "【解析】\n\n![](/static/uploads/word_4.png)")
    assert figure_repair.repair_parsed_questions([q]) == []


def test_skips_non_choice_type():
    q = _q(
        "图（）\n\nA．\n![](/static/uploads/mineru_9_1.jpg)\n\nD．",
        "【解析】\n\n![](/static/uploads/mineru_9_4.jpg)",
        qtype="fill_blank",
    )
    assert figure_repair.repair_parsed_questions([q]) == []


def test_seed_layouts_preserves_existing_entries():
    q = _q(
        "函数图象（）\n\nA．\n![](/static/uploads/mineru_9_1.jpg)\n\n"
        "B．\n![](/static/uploads/mineru_9_2.jpg)\n\n"
        "C．\n![](/static/uploads/mineru_9_3.jpg)\n\nD．",
        "【解析】\n\n![](/static/uploads/mineru_9_4.jpg)\n\n【分析】x",
    )
    q["image_layouts"] = {"mineru_9_1.jpg": {"align": "left", "size": "large"}}
    figure_repair.repair_parsed_questions([q])
    assert q["image_layouts"]["mineru_9_1.jpg"] == {"align": "left", "size": "large"}
    assert q["image_layouts"]["mineru_9_4.jpg"] == {"align": "center", "size": "small"}
