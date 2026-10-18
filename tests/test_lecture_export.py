# -*- coding: utf-8 -*-
"""讲义导出（mathbank/lecture_export）回归：HTML/思维导图 → LaTeX、三视图、编译链路。

只覆盖讲义专属逻辑，不动组卷模板。
"""

from __future__ import annotations

import json
import shutil
from urllib.parse import unquote

import pytest

import main as _main
from mathbank.lecture_export import (
    build_lecture_latex,
    collect_lecture_images,
    escape_text,
    html_to_latex,
    mindmap_to_latex,
    note_body_to_latex,
)

SEC = {
    "compulsory": "必修一", "chapter": "1. 集合与常用逻辑用语", "knowledge": "1.1 集合的概念",
}


def _key(kind, topic_id=""):
    parts = [kind, SEC["compulsory"], SEC["chapter"], SEC["knowledge"]]
    if topic_id:
        parts.append(topic_id)
    return ":".join(parts)


def _preview(notes=None, mind=False):
    knowledge = ("@mindmap\n" + json.dumps({
        "title": "集合",
        "branches": [{"label": "概念", "items": [{"label": "互异性"}, {"label": "嵌套", "items": [{"label": "子集"}]}]}],
    }, ensure_ascii=False)) if mind else "<p>集合是<span class=\"key\">确定性</span>对象的整体。</p>"
    return {
        "settings": {"cover_title": "1.1 集合的概念", "cover_subtitle": "同步讲义"},
        "section": dict(SEC, name="1.1 集合的概念", topics=[
            {"id": "k_a", "name": "集合判断", "no": 1, "example": "例：判断{-1<x<3} 的整数元素。"},
            {"id": "k_b", "name": "元素关系", "no": 7, "example": ""},
        ]),
        "notes": dict({_key("knowledge"): knowledge}, **(notes or {})),
        "topics": [
            {"topic_id": "k_a", "name": "集合判断", "items": [
                {"question_id": 1, "role": "example"}, {"question_id": 2, "role": "variant"},
            ]},
            {"topic_id": "k_b", "name": "元素关系", "items": [{"question_id": 3, "role": "practice"}]},
        ],
        "extra_ids": [4],
        "questions": [
            {"id": 1, "content": "已知集合 $A=\\{x\\mid x^2<4\\}$，求 $A$。",
             "question_type": "detailed_answer", "answer": "由 $x^2<4$ 得 $-2<x<2$。"},
            {"id": 2, "content": "则 $A\\cap B=$（   ）\n\\begin{choices}\n\\item $\\{1\\}$\n\\item $\\{2,3\\}$\n\\end{choices}",
             "question_type": "single_choice", "answer": "$\\{2,3\\}$"},
            {"id": 3, "content": "元素个数是\\fillin。", "question_type": "fill_in_blank", "answer": "3 个"},
            {"id": 4, "content": "写出 $\\{a,b\\}$ 的全部子集。", "question_type": "detailed_answer", "answer": "$\\varnothing$ 等 4 个"},
        ],
    }


# ============ 转义与数学区 ============


def test_escape_text_leaves_math_untouched_and_escapes_prose():
    """数学区里的下划线/百分号是合法语法，普通文本里的必须转义。"""
    out = escape_text("50% 的 $x_1$ 满足 a_b #1 且 $y\\%z$")
    assert r"50\%" in out
    assert "$x_1$" in out                      # 数学区原样
    assert r"a\_b" in out
    assert r"\#" in out
    assert "$y\\%z$" in out


def test_escape_text_handles_unbalanced_dollar():
    """落单的 $ 不该把后半段全部吞成数学区。"""
    out = escape_text("价格 $5 与 20% 折扣")
    assert r"20\%" in out


def test_angle_brackets_are_escaped_outside_math():
    out = escape_text("集合 {x| -1<x<3} 与 $-1<x<3$")
    assert r"\textless{}" in out
    assert "$-1<x<3$" in out


# ============ 知识块 HTML ============


def test_html_note_becomes_balanced_latex():
    body, warns = html_to_latex(
        "<p>忽视<strong>互异性</strong>要检验。</p>"
        "<ul><li>含参数讨论</li><li>空集 $\\varnothing$ 易漏</li></ul>")
    assert r"\textbf{互异性}" in body
    assert "\\begin{itemize}" in body and "\\end{itemize}" in body
    assert body.count("\\begin{itemize}") == body.count("\\end{itemize}")
    assert "$\\varnothing$" in body
    assert warns == []


def test_unclosed_inline_tag_gets_its_brace_back():
    """HTML 片段缺 </strong> 时若不补右括号，整份 PDF 会因为未闭合分组崩掉。"""
    body, _ = html_to_latex("<p><strong>未闭合的强调文字")
    assert body.count("{") == body.count("}")


def test_table_goes_through_the_shared_converter():
    body, warns = html_to_latex('<table class="def"><tr><td>子集</td><td>$A\\subseteq B$</td></tr></table>')
    assert "\\begin{tabular}" in body
    assert "$A\\subseteq B$" in body
    assert warns == []


def test_complex_table_degrades_to_visible_text_not_nothing():
    """rowspan 表格 _convert_one_table 返回 None：必须降级成可读文字并上报，不得整块消失。"""
    html = '<table><tr><td rowspan="2">合并</td><td>甲</td></tr><tr><td>乙</td></tr></table>'
    body, warns = html_to_latex(html)
    assert "合并" in body and "甲" in body and "乙" in body
    assert any("表格" in w for w in warns)


def test_unknown_tag_keeps_its_text():
    body, warns = html_to_latex("<p>正常</p><figure>图注文字</figure>")
    assert "图注文字" in body
    assert any("figure" in w for w in warns)


# ============ 思维导图 ============


def test_mindmap_becomes_nested_lists_and_reports_layout_loss():
    data = {"title": "集合", "branches": [{"label": "概念", "items": [
        {"label": "互异性"}, {"label": "深层", "items": [{"label": "子项"}]}]}]}
    body, warns = mindmap_to_latex(data)
    assert "\\begin{itemize}" in body and "\\end{itemize}" in body
    assert "互异性" in body and "子项" in body
    assert body.count("{") == body.count("}")
    assert any("配色" in w or "布局" in w for w in warns)


def test_mindmap_table_node_renders_as_tabular():
    data = {"branches": [{"label": "分支", "items": [{"label": "表", "table": [["名", "记号"], ["自然数", "$N$"]]}]}]}
    body, _ = mindmap_to_latex(data)
    assert "\\begin{tabular}" in body and "$N$" in body


def test_broken_mindmap_is_reported_not_rendered_as_source():
    """解析失败绝不能把原始 JSON 当正文吐进 PDF。"""
    body, warns = note_body_to_latex("@mindmap\n{\"branches\": [")
    assert body == ""
    assert warns and "无法解析" in warns[0]


def test_note_body_dispatches_plain_html():
    body, warns = note_body_to_latex("<p>普通段落</p>")
    assert "普通段落" in body and warns == []


# ============ 整份文档 ============


def test_views_decide_answer_visibility():
    yuan, _ = build_lecture_latex(_preview(), view="yuan")
    jiexi, _ = build_lecture_latex(_preview(), view="jiexi")
    daan, _ = build_lecture_latex(_preview(), view="daan")
    assert "【答案】" not in yuan
    assert "【答案】" in jiexi
    assert "知识梳理" not in daan and "答案册" in daan
    for tex in (yuan, jiexi, daan):
        assert "documentclass" in tex
        assert "\\begin{document}" in tex and "\\end{document}" in tex


def test_topic_numbering_follows_outline_no():
    """考向编号取大纲 no（元素关系是 7 → 考向七），不是编排下标；且为中文数字。"""
    tex, _ = build_lecture_latex(_preview(), view="yuan")
    assert "考向七" in tex
    assert "考向一　集合判断" in tex
    assert "考向7" not in tex


def test_cn_numeral_covers_lecture_scale():
    """一轮复习三十几讲：十一/二十/三十五这类进位必须正确，越界值原样回落。"""
    from mathbank.lecture_export import cn_numeral

    assert cn_numeral(1) == "一"
    assert cn_numeral(9) == "九"
    assert cn_numeral(10) == "十"
    assert cn_numeral(11) == "十一"
    assert cn_numeral(20) == "二十"
    assert cn_numeral(35) == "三十五"
    assert cn_numeral(99) == "九十九"
    assert cn_numeral(0) == "0"
    assert cn_numeral("x") == "x"


def test_lecture_headers_drop_chapter_and_use_cn_numerals():
    """页眉「第X讲 <小节名>」：章名与教材章节号都不再出现。"""
    from mathbank.lecture_export import lecture_title

    assert lecture_title(1, "1.1 集合的概念") == "第一讲 集合的概念"
    assert lecture_title(12, "函数的概念") == "第十二讲 函数的概念"
    tex, _ = build_lecture_latex(_preview(), view="jiexi")
    # 板块名不再与大标题同行：「第X讲 …」后紧跟独立的 \lectsub{知识梳理}
    assert r"\lectsec{第一讲 集合的概念}" in tex
    assert r"\lectsub{知识梳理}" in tex
    assert "集合与常用逻辑用语" not in tex          # 章名已删
    # 教材章节号从所有页眉消失（封面标题是用户手填，不在管辖内）
    assert "1. 集合与常用逻辑用语 · 1.1" not in tex
    assert "· 1.1 集合的概念" not in tex


def test_toc_page_lists_lectures_after_cover():
    """多节才出目录；目录仅列讲级（子级已全部去掉）。"""
    tex, _ = build_lecture_latex(_preview_multi(), view="yuan")
    assert "目　录" in tex
    head = tex[tex.index("目　录"):tex.index("\\lectpage", tex.index("目　录"))]
    for needle in ("第一讲 集合间的基本关系", "第二讲 集合的概念"):
        assert needle in head
    # 单节旧载荷不出目录页
    single, _ = build_lecture_latex(_preview(), view="yuan")
    assert "目　录" not in single


def test_lecture_page_counts_matches_compiled_start_pages():
    """目录页码必须与真编译出来的起始页一致：分页宏、标题页占位任何一侧改动
    都会让对照表失准——这条用 XeLaTeX 实测钉住两端口径。仅多节导出有目录。"""
    import re

    import pymupdf

    from mathbank.paper_helper import compile_tex_to_pdf

    def norm(s):
        return s.replace(" ", "").replace("\u3000", "")

    tex, _ = build_lecture_latex(_preview_multi(), view="jiexi")
    pdf, log = compile_tex_to_pdf(tex, [])
    assert pdf, str(log)[-400:]
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    toc = {}
    for page in doc:
        t = norm(page.get_text())
        if "目录" not in t[:20]:
            continue
        # 讲级行以「第X讲」开头（\noindent\textbf），子级行以缩进空格开头；
        # PDF 文本层会吃掉 CJK 间空格，按原始文本的换行切行才分得清层级
        for line in page.get_text().splitlines():
            m = re.match(r"^第([一二三四五六七八九十]+)讲.*?(\d+)\s*页\s*$", line.strip())
            if m and not line.startswith(" "):
                toc[m.group(1)] = int(m.group(2))
    actual = {}
    for page in doc:
        t = norm(page.get_text())
        # 只认「知识梳理」独立成行的板块头（目录里的子条目行不算起始页）
        if not re.search(r"知识梳理\s*\n", page.get_text()):
            continue
        lecture = re.search(r"第([一二三四五六七八九十]+)讲", t)
        foot = re.search(r"数学试题第(\d+)页", t)
        if lecture and foot:
            actual.setdefault(lecture.group(1), int(foot.group(1)))
    assert actual, "PDF 里没找到讲次起始页"
    assert toc == actual, "目录页码与实际起始页不符：%s vs %s" % (toc, actual)


def test_outline_example_used_as_unrevised_method_default():
    """已有修订用修订；无修订的「解题方法」落到大纲 example 并标注未修订。"""
    notes = {
        _key("pit", "k_a"): "<p>已修订的易错点</p>",
        _key("method", "k_a"): "<p>已修订的方法</p>",
    }
    tex, _ = build_lecture_latex(_preview(notes=notes), view="yuan")
    assert "已修订的易错点" in tex and "已修订的方法" in tex
    assert "大纲默认，未修订" not in tex          # k_a 已修订，不该再提默认

    only_pit = {_key("pit", "k_a"): "<p>已修订的易错点</p>"}
    tex2, _ = build_lecture_latex(_preview(notes=only_pit), view="yuan")
    assert "大纲默认，未修订" in tex2             # 方法无修订 → 落到大纲 example
    assert "（待补充）" in tex2                    # k_b 既无修订也无 example


def test_question_labels_number_per_topic():
    """新口径：例<考向>-<序>；变式/趁热打铁纯裸号按考向归零；板块头带字。"""
    tex, _ = build_lecture_latex(_preview(), view="yuan")
    assert "例一-1" in tex
    assert "变式1" in tex and "变式1.1" not in tex
    assert r"\blockhead{典型例题}" in tex and r"\blockhead{趁热打铁}" in tex
    # 实战演练挪到本讲末尾后仍是裸号，不再冠「实战」前缀
    assert r"\blockhead{实战演练}" in tex and "实战1" not in tex


def test_answer_book_entries_carry_lecture_prefix():
    daan, _ = build_lecture_latex(_preview(), view="daan")
    assert "第一讲　考向一　例一-1" in daan
    assert "第一讲　考向一　变式1" in daan
    assert "第一讲　考向七　1" in daan          # 趁热打铁裸号 + 定位前缀
    assert "第一讲　1" in daan                  # 实战演练裸号 + 定位前缀
    multi_daan, _ = build_lecture_latex(_preview_multi(), view="daan")
    assert "第二讲　考向一　例一-1" in multi_daan
    # 实战演练的题（id4）属于第一讲：前缀按归属讲次，不是位置序号
    assert "第一讲　1" in multi_daan and "第二讲　1" not in multi_daan


def test_preamble_is_shared_not_copied():
    """讲义导言区必须来自 paper_helper.build_exam_preamble，不得自带第二份宏包清单。"""
    from pathlib import Path
    source = Path(__file__).resolve().parents[1] / "mathbank" / "lecture_export.py"
    text = source.read_text(encoding="utf-8")
    assert "build_exam_preamble" in text
    assert r"\documentclass[fontset=fandol]{exam-zh}" not in text


def test_collect_images_resolves_both_reference_forms(tmp_path):
    (tmp_path / "a.png").write_bytes(b"\x89PNG")
    (tmp_path / "b.png").write_bytes(b"\x89PNG")
    questions = [
        {"content": "如图 ![插图](static/uploads/a.png)", "answer": "见 $\\includegraphics[width=2cm]{b.png}$"},
        {"content": "无图", "answer": ""},
    ]
    paths = collect_lecture_images(questions, str(tmp_path), "static/uploads")
    assert any(p.endswith("a.png") for p in paths)
    assert any(p.endswith("b.png") for p in paths)


def test_collect_images_skips_missing_files(tmp_path):
    """不存在的引用不进编译清单（资产层会挡掉），也不报错。"""
    questions = [{"content": "![x](static/uploads/missing.png)", "answer": ""}]
    assert collect_lecture_images(questions, str(tmp_path), "static/uploads") == []


def test_dangling_image_reference_does_not_kill_export():
    """存量题可能引用已被清理的图片：collect 会静默剔除，但正文里的
     \\includegraphics 若原样进 PDF 会让整本讲义编译失败。导出侧必须把
    「解析不到文件」的图片引用降级成可见占位文字，不得让死链炸掉编译。"""
    import shutil

    import pymupdf

    from mathbank.paper_helper import compile_tex_to_pdf

    preview = _preview()
    # 真实存储链会把字面 \n 还原成换行；夹具直接用真换行，避免 LaTeX 里
    # 出现字面 ``\n``（未定义控制序列）这种只有夹具才有的编译噪声
    preview["questions"][0]["content"] = "如图 $A\\\\cap B$。\n![](static/uploads/gone_file.png)"
    tex, warns = build_lecture_latex(preview, view="jiexi")
    from mathbank.lecture_export import sanitize_dead_image_references

    clean, notes = sanitize_dead_image_references(tex, str(_main.UPLOAD_DIR))
    assert "gone_file" not in clean                   # 死链原样 includegraphics 不允许残留
    assert notes and any("图片" in n for n in notes)
    if shutil.which("xelatex") is None:
        pytest.skip("本机无 XeLaTeX")
    pdf, log = compile_tex_to_pdf(clean + "\n% probe\n", [])
    assert pdf, "含死图引用的讲义必须仍能编译：%s" % str(log)[-400:]
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    text = "".join(page.get_text() for page in doc)
    assert "图片待补" in text


def test_collect_images_rejects_path_traversal():
    """越界路径必须被资产层挡掉，不能把任意外部文件塞进编译目录。"""
    questions = [{"content": "![x](static/uploads/../../.env)", "answer": ""}]
    assert collect_lecture_images(questions, "/tmp/uploads", "static/uploads") == []


# ============ 路由 ============


def _headers():
    return {"X-Local-Token": _main.LOCAL_TOKEN}


def test_export_route_streams_pdf_and_surfaces_degradations(client, monkeypatch):
    captured = {}

    def fake_compile(tex, image_paths):
        captured["tex"] = tex
        return b"%PDF-1.4 fake", ""

    monkeypatch.setattr(_main, "compile_tex_to_pdf", fake_compile)
    r = client.post("/api/lecture/export/pdf", headers=_headers(),
                    json=dict(SEC, view="jiexi", preview=_preview(mind=True)))
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert "考向一" in captured["tex"]
    notes = r.headers.get("X-MathBank-Export-Notes")
    assert notes and "布局" in unquote(notes)


def test_export_route_reports_compile_failure_with_local_diagnostic(client, monkeypatch):
    monkeypatch.setattr(_main, "compile_tex_to_pdf", lambda tex, images: (None, "! Undefined control sequence.\n! Emergency stop."))
    r = client.post("/api/lecture/export/pdf", headers=_headers(), json=dict(SEC, view="yuan", preview=_preview()))
    assert r.status_code == 400
    body = r.json()
    assert body["status"] == "error" and body["message"]
    assert body["diagnostic"]


def test_export_route_validates_input(client):
    assert client.post("/api/lecture/export/pdf", headers=_headers(),
                       json={"compulsory": "", "chapter": "x", "knowledge": "y"}).status_code == 400
    assert client.post("/api/lecture/export/pdf", headers=_headers(),
                       json=dict(SEC, view="bogus")).status_code == 400


def test_export_route_without_token_is_refused(client):
    assert client.post("/api/lecture/export/pdf", json=dict(SEC, view="yuan")).status_code in (401, 403)


# ============ 真实编译守卫 ============


def _preview_multi():
    """两节合并 preview：顺序 K2→K1，各自带知识梳理/考向页，实战演练分属两讲。"""
    kn2 = "1.2 集合间的基本关系"
    sec1 = dict(SEC)
    sec2 = dict(SEC, knowledge=kn2)
    key2 = "knowledge:必修一:%s:%s" % (SEC["chapter"], kn2)
    return {
        "settings": {"cover_title": "第一章 集合", "cover_subtitle": "同步讲义"},
        "sections": [
            {"section": dict(sec2, name=kn2, topics=[{"id": "k_c", "name": "子集判断", "no": 1, "example": ""}]),
             "notes": {key2: "<p>子集与真子集。</p>"},
             "topics": [{"topic_id": "k_c", "name": "子集判断",
                         "items": [{"question_id": 3, "role": "example"}]}],
             "extra_ids": [4]},
            {"section": dict(sec1, name=SEC["knowledge"], topics=[{"id": "k_a", "name": "集合判断", "no": 1, "example": ""}]),
             "notes": {_key("knowledge"): "<p>集合是确定性对象的整体。</p>"},
             "topics": [{"topic_id": "k_a", "name": "集合判断",
                         "items": [{"question_id": 1, "role": "example"}]}],
             "extra_ids": []},
        ],
        "questions": _preview()["questions"],
    }


def test_merged_export_orders_sections_and_keeps_single_answer_book():
    """多节导出：每讲各有一组知识梳理/考向页且顺序跟随载荷；实战演练归各讲、
    答案册整本只有一份。"""
    tex, _ = build_lecture_latex(_preview_multi(), view="jiexi")
    assert tex.count(r"\lectsub{知识梳理}") == 2
    assert tex.index("子集与真子集") < tex.index("集合是确定性对象"), "节的顺序被排错了"
    assert tex.count(r"\blockhead{实战演练}") == 1      # 只有 K2 配了课后作业，归本讲
    assert r"\lectsec{第一讲 集合间的基本关系}" in tex   # K2 的实战演练页大标题
    assert "第二讲 集合的概念 · 实战演练" not in tex     # K1 没有 extra_ids
    assert r"\lectsec{答案册}" not in tex
    daan, _ = build_lecture_latex(_preview_multi(), view="daan")
    assert daan.count(r"\lectsec{答案册}") == 1
    # 单节旧载荷行为不变
    single, _ = build_lecture_latex(_preview(), view="jiexi")
    assert single.count(r"\lectsub{知识梳理}") == 1


@pytest.mark.skipif(shutil.which("xelatex") is None, reason="本机无 XeLaTeX")
def test_generated_lecture_latex_actually_compiles():
    """静态断言挡不住 LaTeX 语法错：真编一次，并核对内容确实进了 PDF。"""
    import pymupdf

    from mathbank.paper_helper import compile_tex_to_pdf

    tex, _degradations = build_lecture_latex(_preview(mind=True), view="jiexi")
    pdf, log = compile_tex_to_pdf(tex, [])
    assert pdf, "讲义 LaTeX 编译失败：%s" % str(log)[-1500:]
    assert pdf[:4] == b"%PDF"
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    # PDF 文本层会吃掉 CJK 间空格（「第一讲 集合」→「第一讲集合」），比对前统一剥空白
    text = "".join(page.get_text() for page in doc).replace(" ", "").replace("\u3000", "")
    for needle in ("知识梳理", "集合判断", "互异性", "【答案】", "第一讲集合的概念", "考向七"):
        assert needle.replace(" ", "") in text, "PDF 里缺少 %s" % needle
    assert doc.page_count >= 1


@pytest.mark.skipif(shutil.which("xelatex") is None, reason="本机无 XeLaTeX")
def test_merged_lecture_latex_actually_compiles():
    """多节合并必须真编得过：跨节拼接最容易在 \begin{document} 之外或环境配对上出事。"""
    import pymupdf

    from mathbank.paper_helper import compile_tex_to_pdf

    tex, _ = build_lecture_latex(_preview_multi(), view="jiexi")
    pdf, log = compile_tex_to_pdf(tex, [])
    assert pdf, "合并讲义 LaTeX 编译失败：%s" % str(log)[-1500:]
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    text = "".join(page.get_text() for page in doc).replace(" ", "").replace("\u3000", "")
    for needle in ("子集与真子集", "集合是确定性对象", "第一讲集合间的基本关系\n【实战演练】", "目录"):
        assert needle.replace(" ", "") in text, "PDF 里缺少 %s" % needle


# ============ 书本体（待办 #8 P1） ============


def test_book_preamble_swaps_class_and_inlines_shim():
    """书本体：导言区换 ElegantBook + 垫片内联，exam-zh 痕迹清零，页码样式让位给类。"""
    import re as _re

    tex, _ = build_lecture_latex(_preview(), view="yuan", fmt="elegantbook")
    assert r"\documentclass[12pt,lang=cn,chinesefont=nofont]{elegantbook}" in tex
    assert r"\setCJKmainfont{FandolSong-Regular.otf}" in tex      # 垫片正文已内联
    assert r"\newenvironment{choices}" in tex
    assert r"{exam-zh}" not in tex and r"\examsetup" not in tex
    # 包文件专属三行绝不能进 .tex：\endinput 会把整份讲义后半截当文件尾吞掉
    assert r"\NeedsTeXFormat" not in tex and r"\ProvidesPackage" not in tex
    assert r"\endinput" not in tex
    # 注释行也不能进：TeX 不读注释，进了反而被导出预检层误「修复」成假告警
    preamble = tex.split("\\begin{document}")[0]
    assert not _re.search(r"^\s*%", preamble, _re.M), preamble[:400]
    # ElegantBook 自带页眉页脚，\pagestyle{plain} 会把书的版式顶掉
    assert r"\pagestyle{plain}" not in tex
    # 版式宏与垫片同名：书本体必须 provide，不能 new 出「已定义」报错
    assert r"\providecommand{\lectsec}" in tex and r"\newcommand{\lectsec}" not in tex


def test_exam_default_branch_is_untouched_by_book_flag():
    """默认口径逐字不变：书本体是加分支，不是改老路（历史断言全部继续有效）。"""
    tex, _ = build_lecture_latex(_preview(), view="yuan")
    assert r"\documentclass[fontset=fandol]{exam-zh}" in tex
    assert r"\newcommand{\lectsec}" in tex and r"\pagestyle{plain}" in tex
    assert "elegantbook" not in tex


def test_book_choice_column_estimation():
    r"""Python 侧列数估算：短选项 4 列、中等 2 列、长/含图 1 列、显式值不重猜。"""
    from mathbank.lecture_export import _book_fit_choice_columns, _estimate_choice_columns

    def _block(items):
        return "\\begin{choices}\n" + "".join("\\item %s\n" % it for it in items) + "\\end{choices}"

    short = _block(["$1$", "$2$", "$3$", "$4$"])
    assert _estimate_choice_columns(short) == 4
    assert r"[columns=4]" in _book_fit_choice_columns(short)

    medium = _block([u"充分不必要条件且需要课堂讨论", u"必要不充分条件且需要课堂讨论",
                     u"充要条件且需要课堂讨论", u"既不充分也不必要条件"])
    assert _estimate_choice_columns(medium) == 2

    long = _block([u"存在实数使得该结论在全体实数范围内都不成立需要进一步讨论验证其真伪" * 2,
                   u"$x$ 的取值范围如解析所示", u"$y$ 的取值范围如解析所示", u"以上都不对"])
    assert _estimate_choice_columns(long) == 1
    assert r"[columns=1]" in _book_fit_choice_columns(long)

    img = _block([r"$1$", r"$2$", r"$3$", r"\includegraphics[width=3cm]{a.png}"])
    assert _estimate_choice_columns(img) == 1

    explicit = r"\begin{choices}[columns=2]" + "\n\\item $1$\n\\item $2$\n\\end{choices}"
    assert _book_fit_choice_columns(explicit) == explicit

    assert _estimate_choice_columns(r"\begin{choices}" + "\n无 item 的坏块\n" + r"\end{choices}") == 0


def test_book_respects_explicit_option_columns_field():
    r"""题目字段显式标了 2 列：即使选项短到能塞 4 列，也绝不覆盖老师的手动口径。"""
    p = _preview()
    p["questions"][1]["content"] = (
        "则 $A\\cap B=$（　）\n\\begin{choices}\n\\item $1$\n\\item $2$\n\\item $3$\n\\item $4$\n\\end{choices}")
    p["questions"][1]["option_columns"] = 2
    tex, _ = build_lecture_latex(p, view="yuan", fmt="elegantbook")
    assert r"\begin{choices}[columns=2]" in tex
    assert r"[columns=4]" not in tex


def test_export_tex_route_carries_format(client):
    r"""TEX 出口：format 决定源码口径，文件名带 book/exam 后缀让用户一眼分辨。"""
    r = client.post("/api/lecture/export/tex", headers=_headers(),
                    json=dict(SEC, view="yuan", format="elegantbook", preview=_preview()))
    assert r.status_code == 200
    body = r.text
    assert "elegantbook" in body and "exam-zh" not in body
    assert "lecture_yuan_book.tex" in r.headers["content-disposition"]

    r2 = client.post("/api/lecture/export/tex", headers=_headers(),
                     json=dict(SEC, view="yuan", preview=_preview()))
    assert r2.status_code == 200
    assert "lecture_yuan_exam.tex" in r2.headers["content-disposition"]


def test_export_pdf_route_book_skips_headless_chrome(client, monkeypatch):
    r"""书本体 + print_html 同车：必须无视网页打印支路，逼 XeLaTeX 出 ElegantBook。"""
    captured = {}

    def fake_compile(tex, image_paths):
        captured["tex"] = tex
        return b"%PDF-1.4 fake", ""

    monkeypatch.setattr(_main, "compile_tex_to_pdf", fake_compile)
    r = client.post("/api/lecture/export/pdf", headers=_headers(), json=dict(
        SEC, view="jiexi", format="elegantbook", preview=_preview(),
        print_html="<html><body>网页预览</body></html>"))
    assert r.status_code == 200
    assert captured.get("tex") and "elegantbook" in captured["tex"]
    assert "_book_" in r.headers["content-disposition"]


def test_export_routes_reject_unknown_format(client):
    r"""未知口径一律 400：静默换排版等于给用户发一份他没点过的讲义。"""
    bad = dict(SEC, view="yuan", format="kindle", preview=_preview())
    assert client.post("/api/lecture/export/pdf", headers=_headers(), json=bad).status_code == 400
    assert client.post("/api/lecture/export/tex", headers=_headers(), json=bad).status_code == 400


def test_frontend_format_lock():
    r"""前端下拉取值与后端白名单逐字一致（和 X-MathBank-Export-Notes 头同一招数）。"""
    from pathlib import Path

    from mathbank import routes_lecture

    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index.html").read_text(encoding="utf-8")
    js = (root / "static" / "js" / "lecture.js").read_text(encoding="utf-8")

    import re as _re
    m = _re.search(r'<select id="lectureExportFormat".*?</select>', html, _re.S)
    assert m, "讲义工具条必须有 lectureExportFormat 下拉"
    values = set(_re.findall(r'<option value="([^"]+)"', m.group(0)))
    assert values == {"exam-zh", "elegantbook"}
    assert values <= set(routes_lecture._FORMAT_ALIASES), "下拉取值必须都在后端白名单里"

    assert "format !== 'elegantbook'" in js, "书本体必须跳过 print_html 支路"
    assert "format: format" in js and "format: currentExportFormat()" in js
    assert "'_book' : '_exam'" in js, "TEX 下载名要能分辨口径"


def test_strip_leading_answer_label_forms():
    r"""答案正文开头的各种答案标记形态都必须被剥掉，正文中间的绝不碰。"""
    from mathbank.lecture_export import strip_leading_answer_label

    assert strip_leading_answer_label("【答案】C") == "C"
    assert strip_leading_answer_label("【答案】：$(0,2)$") == "$(0,2)$"
    assert strip_leading_answer_label(r"\textbf{【答案】}D") == "D"
    assert strip_leading_answer_label("**【答案】** B") == "B"
    assert strip_leading_answer_label("【参考答案】：2") == "2"
    # 正文中间的标记是引用，不是板块头，必须原样留着
    assert strip_leading_answer_label("【解析】见【答案】栏") == "【解析】见【答案】栏"
    assert strip_leading_answer_label("故选 A。") == "故选 A。"


def test_lecture_export_does_not_double_the_answer_prefix():
    """历史数据自带【答案】前缀 + 导出再补一次 = PDF 里两遍前缀（遗留 bug 回归）。"""
    from mathbank.lecture_export import _question_block

    tex = _question_block(
        {
            "id": 1,
            "content": "已知 $x^2<4$，则（   ）",
            "question_type": "single_choice",
            "answer": "【答案】C\n\n【解析】由 $x^2<4$ 得 $-2<x<2$。",
        },
        "例 1",
        True,
    )
    assert tex.count("【答案】") == 1, tex
    assert "【答案】C" not in tex
    assert "【解析】" in tex  # 只剥答案标记，解析板块不动


def test_lecture_preview_answer_block_has_single_prefix():
    """讲义预览（lecture.js）与导出口径一致：标签由版式层贴，正文自带的前缀要剥掉。"""
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "static" / "js" / "lecture.js").read_text(
        encoding="utf-8"
    )
    assert "function stripLeadingAnswerLabel(" in js
    helper_start = js.index("function stripLeadingAnswerLabel(")
    helper_end = js.index("function buildQuestion(", helper_start)
    helper = js[helper_start:helper_end]
    assert "(?:参考|标准)?答" in helper
    # 答案块必须用剥过前缀的正文，而不是原始 q.answer
    body = js[helper_end:js.index("function circledBreaks(", helper_end)]
    assert "stripLeadingAnswerLabel(q.answer)" in body
    assert "parseMarkdownWithMath(answerBody)" in body
