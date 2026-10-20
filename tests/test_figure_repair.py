"""插图归属修复（mathbank/figure_repair）回归：以 2024 大庆卷第 4 题为标准脏形。

两条判定路线都要钉住：
- **GEO 坐标仲裁（优先）**：用 ``content_list.json`` 的 bbox 判归属，覆盖大庆卷端到端
  死数据（第 4 题 2×2 选项拼图、第 2 题韦恩图、跨题泄漏的裸标号）与「判不准必须降级」；
- **R1-R4 文本规则（后备）**：空选项回填、解析重复图剔除、缺失标号补全、原卷配图回补，
  以及「没把握就不动」的保守边界——修复器宁可不修，也绝不能把干净的题改坏。
"""

import re
import json

from mathbank import figure_repair
from mathbank.pdf_local_split import plan_pdf_local_split

import pathlib

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "mineru_daqing_q4_full.md"
CONTENT_LIST = pathlib.Path(__file__).parent / "fixtures" / "mineru_daqing_content_list.json"


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


# ---------------------------------------------------------------------------
# GEO 坐标仲裁：有 bbox 就先按几何判归属，文本规则退居后备
# ---------------------------------------------------------------------------

# 2024 大庆卷的图（images/<sha>.jpg 的 sha），死数据来自
# tests/fixtures/mineru_daqing_content_list.json（与 mineru_daqing_q4_full.md 同源逐字节对齐）。
# 第 4 题 2×2 选项拼图（页 1，坐标是 MinerU 自己的尺度）：
#   题干块 y 545~580；TL[117,590,315,715] TR[524,590,722,715]
#   BL[115,721,315,846] BR[524,721,726,847]；【答案】块起点 y=856。
# BR 那片在 md 里被排到【答案】之后（阅读顺序错），但中心 y=784 明显在解析带之上。
SHA_TL = "06a42bdfcf16b12b0f0d175ffb7966d6ce57e611564e46e0986b1ea9defa107f"
SHA_TR = "1f57db3410a2d5c476f2d40938bc41430356bc7fca60f53fbca2c8d2212a302f"
SHA_BL = "d8bb2f97d54305b2dbbd20799dfaa68f0887a6018c2f64e80b363bfaf5856c5c"
SHA_BR = "5227f2e8ec8540497ec6dfdefd0081bf328f5bbe41095b116386c8961baabb8e"
SHA_VENN = "9f4fe36cfcf46d5fb5171bf11a5c95a47582d41d657507949f06284b3be1fbfb"


def _daqing_case():
    """真实清单 + 真实 markdown：返回 (卡片, layout, ``{images/<sha>.jpg: 落盘 URL}``)。

    编号口径与 ``_staged_markdown`` 一致（按 md 首次出现顺序），所以落盘名可以直接对照。
    """
    raw = FIXTURE.read_text(encoding="utf-8")
    order = []
    for m in re.finditer(r"images/([0-9a-f]{64}\.jpg)", raw):
        if m.group(1) not in order:
            order.append(m.group(1))
    mapping = {"images/" + sha: "/static/uploads/mineru_1792417091_%d.jpg" % i
               for i, sha in enumerate(order, start=1)}
    markdown = raw
    for key, url in mapping.items():
        markdown = markdown.replace("(" + key + ")", "(" + url + ")")
    layout = figure_repair.build_figure_layout(
        json.loads(CONTENT_LIST.read_text(encoding="utf-8")), mapping
    )
    assert layout and layout["figures"], "真实清单必须编出坐标索引"
    plan = plan_pdf_local_split(markdown, None, False)
    assert plan.trustworthy
    return [b.to_parsed_question({}) for b in plan.all_blocks], layout, mapping


def _card_of(cards, keyword):
    return next(c for c in cards if keyword in (c.get("content") or ""))


def _option_slots(content):
    """题干里每个标号下的第一张图：``{标号: URL}``。"""
    slots = {}
    current = None
    for line in content.split("\n"):
        m = re.match(r"^\s*(?:[-*]\s*)?([A-D])\s*[.．、,，:：)）]", line)
        if m:
            current = m.group(1)
            continue
        img = re.match(r"^\s*!\[[^\]]*\]\(\s*([^)\s]+)", line)
        if img and current and current not in slots:
            slots[current] = img.group(1)
    return slots


def _txt(text, y0, y1, page=0, x0=80, x1=900):
    """合成清单里的正文块。"""
    return {"type": "text", "text": text, "bbox": [x0, y0, x1, y1], "page_idx": page}


def _img(name, y0, y1, page=0, x0=100, x1=300):
    """合成清单里的图形块（``name`` 是 sha 位置上的短名）。"""
    return {"type": "image", "img_path": "images/%s.jpg" % name,
            "bbox": [x0, y0, x1, y1], "page_idx": page}


def _layout(blocks, names):
    """``{短名: 落盘 URL}`` → 与 stage_images 同口径的映射，再编译成坐标索引。"""
    mapping = dict(("images/%s.jpg" % k, v) for k, v in names.items())
    return figure_repair.build_figure_layout(blocks, mapping)


def test_geo_apply_drops_every_url_form_of_the_same_asset():
    r"""搬图删旧引用必须按**文件名**：同一张图的整串 URL 与裸文件名两种形态都要清掉。

    按整串 URL 比时，删不掉另一种写法的那一条，再补写一条新的 → 解析里同一张图渲染两次
    （2026-10 组卷实测：【分析】和【详解】里各一张）。
    """
    card = {
        "content": "如图，求阴影部分面积\nA．1\nB．2\nC．3\nD．4",
        "answer_markdown": (
            "【答案】B\n\n【解析】\n\n【分析】见下图\n\n![](/static/uploads/mineru_5_1.jpg)\n\n"
            "【详解】$S=2$ .\n\n![](mineru_5_1.jpg)"
        ),
        "question_type": "single_choice",
        "image_paths": ["/static/uploads/mineru_5_1.jpg"],
    }
    figure_repair._geo_apply(
        card,
        {"verdicts": {"mineru_5_1.jpg": {
            "url": "/static/uploads/mineru_5_1.jpg", "zone": "answer", "label": None}}},
        [],
    )
    assert card["answer_markdown"].count("mineru_5_1") == 1, card["answer_markdown"]
    assert "【分析】见下图" in card["answer_markdown"], card["answer_markdown"]
    assert "【详解】" in card["answer_markdown"], card["answer_markdown"]

    # 幂等：搬完再跑一遍不得二次改动
    before = dict(card)
    figure_repair._geo_apply(
        card,
        {"verdicts": {"mineru_5_1.jpg": {
            "url": "/static/uploads/mineru_5_1.jpg", "zone": "answer", "label": None}}},
        [],
    )
    assert card["answer_markdown"] == before["answer_markdown"], card["answer_markdown"]


def test_geo_pins_daqing_option_grid_by_coordinate():
    """第 4 题：四片按 bbox 各归 A/B/C/D，解析里的幽灵图清零——坐标口径 100% 判准。"""
    cards, layout, mapping = _daqing_case()
    notes = figure_repair.repair_parsed_questions(cards, figure_layout=layout)
    assert notes and all("GEO" in n for n in notes), notes

    q4 = _card_of(cards, "大致图象")
    assert _option_slots(q4["content"]) == {
        "A": mapping["images/%s.jpg" % SHA_TL],
        "B": mapping["images/%s.jpg" % SHA_TR],
        "C": mapping["images/%s.jpg" % SHA_BL],
        "D": mapping["images/%s.jpg" % SHA_BR],
    }, q4["content"]
    # B 的标号行是几何判定补出来的；BR 那片从【解析】开头搬回 D 之后
    assert re.search(r"(?m)^\s*B．\s*$", q4["content"])
    assert not re.search(r"(?m)^\s*!\[", q4["answer_markdown"]), q4["answer_markdown"]
    assert len(re.findall(r"!\[[^\]]*\]\(", q4["content"])) == 4
    assert {rec["rule"] for rec in q4["_figure_repair"]} == {"GEO"}
    assert {rec["zone"] for rec in q4["_figure_repair"]} == {"option"}
    # 四张选项图播种成两列小图（题干图不参与播种）
    assert len(q4["image_layouts"]) == 4
    assert all(v == {"align": "center", "size": "small"} for v in q4["image_layouts"].values())
    # 幂等：同一批卡再跑一遍不得产生任何新修复
    assert figure_repair.repair_parsed_questions(cards, figure_layout=layout) == []


def test_geo_keeps_daqing_venn_in_stem_and_purges_leaked_label():
    """第 2 题：韦恩图中心 y=681 在选项行（745）之上 -> 题干配图；顺带剔除漏进来的「B.」。"""
    cards, layout, mapping = _daqing_case()
    figure_repair.repair_parsed_questions(cards, figure_layout=layout)
    q2 = _card_of(cards, "阴影部分表示的集合")
    url = mapping["images/%s.jpg" % SHA_VENN]
    assert url in q2["content"]
    assert q2["content"].index(url) < q2["content"].index("A. $(-2,0]$")
    # 第 4 题的选项标号被切成独立小块、按 md 顺序掉进第 2 题解析中间：按坐标剔除
    assert not re.search(r"(?m)^\s*B\.\s*$", q2["answer_markdown"]), q2["answer_markdown"]
    assert "【详解】" in q2["answer_markdown"]      # 只删那一行，解析正文一个字不动
    assert any(rec.get("zone") == "stray_label" for rec in q2["_figure_repair"])
    # 题干配图不是选项图，绝不能播种成 small（会把韦恩图缩到看不清）
    assert not q2.get("image_layouts")


def test_geo_backfills_both_figures_where_r4_refuses():
    """坐标口径下题干图不再受「题干必须提到图 + 候选恰好一张」限制。

    R4 遇到两张候选图会整题不动（判不准归属）；bbox 却能分别指出：一张在题干带、
    一张在解析带 —— 于是两边各补一处引用。
    """
    stem_url = "/static/uploads/mineru_9_1.jpg"
    ans_url = "/static/uploads/mineru_9_2.jpg"

    def card():
        q = _q("求 $a$ 的值（）\n\nA. 1 B. 2 C. 3 D. 4",
               "【答案】B\n\n【详解】两点距离为 2")
        q["image_paths"] = [stem_url, ans_url]
        return q

    # 没有坐标时：两张候选 + 题干没提图，R4 保守红线一次都不触发
    plain = card()
    assert figure_repair.repair_parsed_questions([plain]) == []

    layout = _layout([
        _txt("求 $a$ 的值（）", 100, 140),
        _img("s", 105, 135, x0=700, x1=880),      # 题干句子右侧：题干带内
        _txt("A. 1 B. 2 C. 3 D. 4", 150, 180),
        _txt("【答案】B", 190, 210),
        _img("a", 220, 270),                      # 【答案】与【详解】之间：解析带内
        _txt("【详解】两点距离为 2", 280, 300),
    ], {"s": stem_url, "a": ans_url})
    q = card()
    notes = figure_repair.repair_parsed_questions([q], figure_layout=layout)
    assert notes and all("GEO" in n for n in notes), notes
    assert {rec["zone"] for rec in q["_figure_repair"]} == {"stem", "answer"}, q["_figure_repair"]
    assert q["content"].index(stem_url) < q["content"].index("A. 1")   # 补在选项区之前
    assert ans_url in q["answer_markdown"]                              # 挂进解析
    assert stem_url not in q["answer_markdown"]
    assert not q.get("image_layouts"), "题干图/解析图都不是选项图"


def test_geo_vetoes_text_rules_when_coordinate_confirms():
    """几何证据否决文本规则：解析开头的图簇若坐标上真属于解析，R1 一张都不许搬。"""
    url1 = "/static/uploads/mineru_9_1.jpg"
    url2 = "/static/uploads/mineru_9_2.jpg"

    def card():
        return _q(
            "函数图象如图，则（）\n\nA．\n\nB．\n\nC．\n\nD．",
            "【答案】A\n\n![](%s)\n\n![](%s)\n\n【详解】看图可知" % (url1, url2),
        )

    plain = card()
    assert figure_repair.repair_parsed_questions([plain]) != [], "文本规则本来会搬图"

    blocks = [
        _txt("函数图象如图，则（）", 100, 140),
        _txt("A．", 150, 165), _txt("B．", 170, 185),
        _txt("C．", 190, 205), _txt("D．", 210, 225),
        _txt("【答案】A", 230, 245),
        _img("i", 250, 300), _img("j", 310, 360),
        _txt("【详解】看图可知", 370, 390),
    ]
    q = card()
    notes = figure_repair.repair_parsed_questions(
        [q], figure_layout=_layout(blocks, {"i": url1, "j": url2})
    )
    assert notes == [], notes
    assert q == card(), "坐标确认归属无误时必须一字不改"


def test_geo_downgrades_when_option_grid_has_no_label_anchor():
    """2×2 网格但页内没有任何裸标号可证：行优先/列优先判不准 -> 整题降级走 R1+R3。"""
    urls = ["/static/uploads/mineru_9_%d.jpg" % i for i in (1, 2, 3, 4)]
    q = _q(
        "函数图象（）\n\nA.\n![](%s)\n\n![](%s)\n\nC.\n![](%s)\n\nD." % tuple(urls[:3]),
        "【答案】A\n\n【解析】\n\n![](%s)\n\n【分析】看图" % urls[3],
    )
    blocks = [
        _txt("函数图象（）", 100, 140),
        _txt("A.", 150, 165), _txt("C.", 170, 185), _txt("D.", 190, 205),
        _img("a", 250, 320, x0=100, x1=300), _img("b", 250, 320, x0=520, x1=720),
        _img("c", 330, 400, x0=100, x1=300), _img("d", 330, 400, x0=520, x1=720),
        _txt("【答案】A", 500, 520), _txt("【解析】", 530, 545),
        _txt("【分析】看图", 600, 620),
    ]
    layout = _layout(blocks, dict(zip("abcd", urls)))
    figure_repair.repair_parsed_questions([q], figure_layout=layout)
    assert {rec["rule"] for rec in q["_figure_repair"]} == {"R1", "R3"}


def test_geo_refuses_when_label_anchor_contradicts_reading_order():
    """锚点与阅读序冲突（「C.」贴在右上格）：判不准，一张都不搬。"""
    urls = ["/static/uploads/mineru_9_%d.jpg" % i for i in (1, 2, 3, 4)]
    content = ("函数图象（）\n\nA．\n![](%s)\n\nB．\n![](%s)\n\n"
               "C．\n![](%s)\n\nD．\n![](%s)" % tuple(urls))
    q = _q(content, "【答案】A\n\n【详解】看图")
    blocks = [
        _txt("函数图象（）", 100, 140),
        _img("a", 250, 320, x0=100, x1=300), _img("b", 250, 320, x0=520, x1=720),
        _img("c", 330, 400, x0=100, x1=300), _img("d", 330, 400, x0=520, x1=720),
        _txt("【答案】A", 500, 520), _txt("【详解】看图", 530, 560),
        # 「C.」贴在右上格左边：行优先会说那是 B，冲突 -> 判不准
        {"type": "text", "text": "C.", "bbox": [495, 275, 515, 290], "page_idx": 0},
    ]
    before = (q["content"], q["answer_markdown"])
    notes = figure_repair.repair_parsed_questions(
        [q], figure_layout=_layout(blocks, dict(zip("abcd", urls)))
    )
    assert notes == [], notes
    assert (q["content"], q["answer_markdown"]) == before


def _two_col_blocks(two_col):
    """同一道题的单栏 / 双栏两版块序列（只差左右两栏那 10 个窄正文块）。"""
    blocks = [_txt("已知 $a > 0$，且 $a$ 为整数，则 $a$ 的值为（）", 100, 140)]
    if two_col:
        for i in range(5):
            blocks.append(_txt("左栏第 %d 行正文内容若干字" % i, 150 + i * 20,
                               165 + i * 20, x0=60, x1=460))
            blocks.append(_txt("右栏第 %d 行正文内容若干字" % i, 150 + i * 20,
                               165 + i * 20, x0=520, x1=920))
    blocks.append(_img("s", 105, 135, x0=600, x1=800))   # 题干句右侧
    blocks.append(_txt("A. 1 B. 2 C. 3 D. 4", 150, 180))
    blocks.append(_txt("【答案】B", 190, 210))
    return blocks


def test_geo_refuses_on_two_column_page():
    """双栏页：纵向区带在这里失效，判不准一律不动（坐标仲裁的版式红线）。

    同一道题的单栏版本能判、双栏版本必须放弃——这道闸门不许「猜」，
    因为把别栏的图按 y 坐标挂到本题是双栏卷最常见的错法。
    """
    url = "/static/uploads/mineru_9_1.jpg"

    def card():
        q = _q("已知 $a > 0$，且 $a$ 为整数，则 $a$ 的值为（）\n\nA. 1 B. 2 C. 3 D. 4",
               "【答案】B")
        q["image_paths"] = [url]
        return q

    single = card()
    assert figure_repair.repair_parsed_questions(
        [single], figure_layout=_layout(_two_col_blocks(False), {"s": url})
    ) != [], "单栏同一份坐标必须判得出题干配图"

    q = card()
    before = (q["content"], q["answer_markdown"])
    notes = figure_repair.repair_parsed_questions(
        [q], figure_layout=_layout(_two_col_blocks(True), {"s": url})
    )
    assert notes == [], notes
    assert (q["content"], q["answer_markdown"]) == before


def test_geo_downgrades_when_card_text_does_not_align():
    """慢路径改写过正文（对不上清单）-> 没有几何证据 -> 照旧走 R1-R4。"""
    q = _q(
        "函数 $f(x)$ 的图象大致是（ ）\n\nA．\n![](/static/uploads/mineru_9_1.jpg)\n\n"
        "B．\n![](/static/uploads/mineru_9_2.jpg)\n\n"
        "C．\n![](/static/uploads/mineru_9_3.jpg)",
        "【答案】A\n\n【解析】\n\n![](/static/uploads/mineru_9_4.jpg)\n\n【分析】看图",
    )
    layout = _layout(
        [_txt("这份清单压根没有这道题的任何一句原文", 100, 140),
         _img("x", 200, 260)],
        {"x": "/static/uploads/mineru_9_9.jpg"},
    )
    figure_repair.repair_parsed_questions([q], figure_layout=layout)
    assert {rec["rule"] for rec in q["_figure_repair"]} == {"R1"}
    assert not any(rec["rule"] == "GEO" for rec in q["_figure_repair"])


def test_build_figure_layout_rejects_unusable_input():
    """清单缺失 / 坐标畸形 / 映射接不上：一律返回 None，调用方整批降级。"""
    assert figure_repair.build_figure_layout(None, None) is None
    assert figure_repair.build_figure_layout([], {}) is None
    # 正文块没有 img_path，等于没有可仲裁的图
    assert figure_repair.build_figure_layout([_txt("题干", 0, 10)], {}) is None
    # 零面积 bbox（MinerU 偶发脏数据）不可用
    dead = [{"type": "image", "img_path": "images/x.jpg",
             "bbox": [10, 10, 10, 10], "page_idx": 0}]
    assert figure_repair.build_figure_layout(dead, {"images/x.jpg":
                                          "/static/uploads/mineru_1_1.jpg"}) is None
    # 图形块没有页码 -> 无法定位区带
    no_page = [{"type": "image", "img_path": "images/x.jpg", "bbox": [0, 0, 10, 10]}]
    assert figure_repair.build_figure_layout(no_page, {"images/x.jpg":
                                            "/static/uploads/mineru_1_1.jpg"}) is None
    # 清单里的图没进 stage_images 映射（被装饰图过滤器丢掉）-> 不参与仲裁
    unmapped = [{"type": "image", "img_path": "images/x.jpg",
                 "bbox": [0, 0, 10, 10], "page_idx": 0}]
    assert figure_repair.build_figure_layout(unmapped, {}) is None


def test_daqing_coordinate_arbitration_after_step6_strip():
    """线上链路终局对账：step 6 把四片全剥进 image_paths 之后，坐标仍要按 bbox 归位。

    这是 R1-R4 最怕的形态——正文里连一张图都不剩，「解析开头图簇 == 空选项数」这类
    计数规则无从下手；而 bbox 与标号锚点不受剥离影响，照样钉得出 2×2 的行优先。
    """
    from mathbank.parsing_pipeline import _strip_image_reference, apply_storage_normalization

    raw = FIXTURE.read_text(encoding="utf-8")
    order = []
    for m in re.finditer(r"images/([0-9a-f]{64}\.jpg)", raw):
        if m.group(1) not in order:
            order.append(m.group(1))
    mapping = {"images/" + sha: "/static/uploads/mineru_1792417091_%d.jpg" % i
               for i, sha in enumerate(order, start=1)}
    markdown = raw
    for key, url in mapping.items():
        markdown = markdown.replace("(" + key + ")", "(" + url + ")")
    layout = figure_repair.build_figure_layout(
        json.loads(CONTENT_LIST.read_text(encoding="utf-8")), mapping
    )
    q = next(b for b in plan_pdf_local_split(markdown, None, False).all_blocks
             if "大致图象" in (b.content or "")).to_parsed_question({})
    refs = re.findall(r"!\[[^\]]*\]\(([^)\s]+)",
                      (q["content"] or "") + "\n" + (q["answer_markdown"] or ""))
    q["image_paths"] = list(dict.fromkeys(refs))
    stripped = q["content"]
    for url in q["image_paths"]:
        stripped = _strip_image_reference(stripped, url)
    q["content"] = stripped.strip()
    # step 6 的实际语义（兄弟测试已钉死）：只剥走本轮那一张，其余三张留在正文里
    assert len(re.findall(r"!\[[^\]]*\]\(", q["content"])) == 3, q["content"]

    notes = figure_repair.repair_parsed_questions([q], figure_layout=layout)
    assert notes and all("GEO" in n for n in notes), notes
    assert _option_slots(q["content"]) == {
        "A": mapping["images/%s.jpg" % SHA_TL],
        "B": mapping["images/%s.jpg" % SHA_TR],
        "C": mapping["images/%s.jpg" % SHA_BL],
        "D": mapping["images/%s.jpg" % SHA_BR],
    }, q["content"]
    apply_storage_normalization([q])
    content = q["content"]
    assert content.count(r"\begin{choices}") == 1, content
    assert len(re.findall(r"(?m)^\s*\\item\b", content)) == 4, content
    assert content.count(r"\includegraphics") == 4, content
    assert not re.search(r"(?m)^\s*!\[", q["answer_markdown"]), q["answer_markdown"]


def test_step7_45_threads_figure_layout_into_repair(monkeypatch):
    """坐标索引必须真的进到第 7.45 步：接缝上钉住 kwarg 原样传下去。

    只在 figure_repair 单测里证明「它能工作」不够——routes_paper 建的索引要是漏在
    ``post_process_pdf_parsed_questions`` 的签名上，线上会静默退回文本规则。
    """
    from mathbank import parsing_pipeline

    seen = {}

    def fake_repair(cards, figure_layout=None):
        seen["cards"] = cards
        seen["layout"] = figure_layout
        return []

    monkeypatch.setattr(parsing_pipeline, "repair_parsed_questions", fake_repair)
    layout = {"blocks": [], "figures": {"mineru_1_1.jpg": {}}, "pages": {}}
    parsing_pipeline.post_process_pdf_parsed_questions(
        [{"content": "已知 $x > 0$，求 $x$ 的值。", "answer_markdown": "【答案】1",
          "question_type": ""}],
        "接缝测试卷", "task-seam", None, "", True,
        figure_layout=layout,
    )
    assert seen["layout"] is layout
    assert len(seen["cards"]) == 1
