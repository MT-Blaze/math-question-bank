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


def test_r1_forces_missing_label_when_no_empty_slot():
    """R1 强制补标号：A/B/C 都带图、第 4 片躺在解析里 -> 补出 D 标号行，凑齐 4 个选项。

    只把图搬回「已有空选项」是不够的：MinerU 丢标号时选项区压根没有第 4 行，
    图搬回去仍然凑不成四选项（线上第 4 题实测就是 3 个 \\item + 1 张无主图）。
    """
    q = _q(
        "函数图象（）\n\nA．\n![](/static/uploads/mineru_9_1.jpg)\n\n"
        "B．\n![](/static/uploads/mineru_9_2.jpg)\n\n"
        "C．\n![](/static/uploads/mineru_9_3.jpg)",
        "【答案】A\n\n【解析】\n\n![](/static/uploads/mineru_9_4.jpg)\n\n【分析】看图",
    )
    notes = figure_repair.repair_parsed_questions([q])
    assert any(rec.get("forced_label") for rec in q["_figure_repair"])
    assert any("补出标号行" in n for n in notes)
    assert re.search(r"(?m)^\s*D．\s*$", q["content"])
    assert "mineru_9_4.jpg" in q["content"]
    assert not re.search(r"(?m)^\s*!\[", q["answer_markdown"])
    assert len(re.findall(r"!\[[^\]]*\]\(", q["content"])) == 4
    # 归一化之后必须是同一个 choices 环境里的 4 个 \item
    from mathbank.latex_normalize import normalize_for_storage
    stored = normalize_for_storage(q["content"], q.get("question_type") or "single_choice")
    assert stored.count(r"\item") == 4
    assert stored.count(r"\begin{choices}") == 1


def test_r1_refuses_tail_synthesis_when_labels_have_a_hole():
    """标号缺中间那个（A、D 有，B、C 没有）：补哪个位置判不准，整题不动（保守红线）。"""
    content = (
        "图（）\n\nA．\n![](/static/uploads/mineru_9_1.jpg)\n\n"
        "D．\n![](/static/uploads/mineru_9_2.jpg)"
    )
    answer = "【解析】\n\n![](/static/uploads/mineru_9_3.jpg)\n\n【分析】x"
    q = _q(content, answer)
    assert figure_repair.repair_parsed_questions([q]) == []
    assert q["content"] == content and q["answer_markdown"] == answer


def test_r4_backfills_stem_figure_from_image_paths():
    """R4 原卷配图回补：题干写着「图中阴影部分」，正文却没有任何引用 -> 补回选项区之前。

    不补的话这张图只活在 image_paths 里，卡片预览把它当题干配图吊在选项下方，
    成一张正文查无此图的「幽灵图」（2024 大庆卷第 2 题的韦恩图）。
    """
    url = "/static/uploads/mineru_9_11.jpg"
    q = _q(
        "已知全集 $U = \\mathbf{R}$，则图中阴影部分表示的集合为（）\n\n"
        "A. $(-2,0]$ B. $(2,5)$ C. $[-2,0]$ D. $(-2,5)$",
        "【答案】D\n\n【详解】由图知阴影部分为 $\\complement_{R}(A\\cap B)$",
    )
    q["image_paths"] = [url]
    notes = figure_repair.repair_parsed_questions([q])
    assert any("R4" in n for n in notes)
    assert url in q["content"]
    # 补在题干文字之后、选项行之前
    assert q["content"].index(url) < q["content"].index("A. $(-2,0]$")
    # 题干配图不是选项图，不能被播种成 small（会把韦恩图缩到看不清）
    assert not q.get("image_layouts")


def test_r4_refuses_when_ambiguous():
    """R4 的三条保守边界：题干没提图 / 候选不止一张 / 图已被解析用过，都不动。"""
    url = "/static/uploads/mineru_9_11.jpg"
    no_word = _q("求集合 $A$ 的子集个数。\n\nA. 2 B. 4 C. 8 D. 16", "【答案】B")
    no_word["image_paths"] = [url]
    assert figure_repair.repair_parsed_questions([no_word]) == []

    two_candidates = _q("则图中阴影部分为（）\n\nA. 1 B. 2 C. 3 D. 4", "【答案】A")
    two_candidates["image_paths"] = [url, "/static/uploads/mineru_9_12.jpg"]
    assert figure_repair.repair_parsed_questions([two_candidates]) == []

    used_in_answer = _q("则图中阴影部分为（）\n\nA. 1 B. 2 C. 3 D. 4", "【详解】见图 " + url)
    used_in_answer["image_paths"] = [url]
    assert figure_repair.repair_parsed_questions([used_in_answer]) == []


def test_daqing_q4_step6_strip_keeps_every_option_image():
    """钉死 2026-10-19 线上链路：step 6 剥图不许把第二张选项图剥走，否则 R3 看不见 B。

    实测那次重传只剩 3 个 \\item + 一张无主幽灵图，根因不在 figure_repair，而在
    ``_strip_image_reference`` 的 last_meaningful：它只剥本轮那一个 url 的图片语法，
    于是处理第二张选项图时，上一张图被当成「有意义内容」，标号依据消失，图被剥进
    image_paths —— R3 再保守也修不到一张正文里已经看不见的图。
    """
    from mathbank.parsing_pipeline import _strip_image_reference, apply_storage_normalization

    plan = plan_pdf_local_split(_staged_markdown(), None, False)
    blk = next(b for b in plan.all_blocks if "大致图象" in (b.content or ""))
    q = blk.to_parsed_question({})
    refs = re.findall(
        r"!\[[^\]]*\]\(([^)\s]+)", (q["content"] or "") + "\n" + (q["answer_markdown"] or "")
    )
    q["image_paths"] = list(dict.fromkeys(refs))

    stripped = q["content"]
    for url in q["image_paths"]:
        stripped = _strip_image_reference(stripped, url)
    assert len(re.findall(r"!\[[^\]]*\]\(", stripped)) == 3, "题干里的三张选项图必须一张都不丢: " + stripped
    q["content"] = stripped.strip()

    figure_repair.repair_parsed_questions([q])
    assert {rec["rule"] for rec in q["_figure_repair"]} == {"R1", "R3"}
    apply_storage_normalization([q])

    content = q["content"]
    assert content.count(r"\begin{choices}") == 1, content
    assert len(re.findall(r"(?m)^\s*\\item\b", content)) == 4, content
    assert content.count(r"\includegraphics") == 4, content
    assert not re.search(r"(?m)^\s*!\[", q["answer_markdown"]), q["answer_markdown"]
    # 四张图都在正文里被显式引用 -> 前端配图条不该再挂任何一张（归一化后按 basename 比）
    names = {str(u).rsplit("/", 1)[-1] for u in (q.get("image_paths") or [])}
    assert names and all(name in content for name in names), (names, content)
