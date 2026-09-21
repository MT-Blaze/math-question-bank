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
    assert "第一讲 集合的概念 · 知识梳理" in tex
    assert "集合与常用逻辑用语" not in tex          # 章名已删
    # 教材章节号从所有页眉消失（封面标题是用户手填，不在管辖内）
    assert "1. 集合与常用逻辑用语 · 1.1" not in tex
    assert "· 1.1 集合的概念" not in tex

def test_toc_page_lists_lectures_after_cover():
    tex, _ = build_lecture_latex(_preview_multi(), view="yuan")
    assert "目　录" in tex
    assert "第一讲 集合间的基本关系" in tex[tex.index("目　录"):tex.index("知识梳理")]
    assert "第二讲 集合的概念" in tex[tex.index("目　录"):]
    assert tex.index("目　录") < tex.index("第一讲 集合间的基本关系 · 知识梳理")

def test_lecture_page_counts_matches_compiled_start_pages():
    """目录页码必须与真编译出来的起始页一致：分页宏、标题页占位任何一侧改动
    都会让对照表失准——这条用 XeLaTeX 实测钉住两端口径。"""
    import re

    import pymupdf

    from mathbank.paper_helper import compile_tex_to_pdf

    def norm(s):
        return s.replace(" ", "").replace("\u3000", "")

    for preview in (_preview(), _preview_multi()):
        tex, _ = build_lecture_latex(preview, view="jiexi")
        pdf, log = compile_tex_to_pdf(tex, [])
        assert pdf, str(log)[-400:]
        doc = pymupdf.open(stream=pdf, filetype="pdf")
        toc = {}
        for page in doc:
            t = norm(page.get_text())
            if "目录" not in t[:20]:
                continue
            for m in re.finditer(r"第([一二三四五六七八九十]+)讲.*?第(\d+)页", t):
                toc[m.group(1)] = int(m.group(2))
        actual = {}
        for page in doc:
            t = norm(page.get_text())
            if "知识梳理" not in t:
                continue
            lecture = re.search(r"第([一二三四五六七八九十]+)讲", t)
            foot = re.search(r"数学试题第(\d+)页", t)
            if lecture and foot:
                actual.setdefault(lecture.group(1), int(foot.group(1)))
        assert actual, "PDF 里没找到讲次起始页"
        assert toc == actual, "目录页码与实际起始页不符：%s vs %s" % (toc, actual)

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
    """两节合并 preview：顺序 K2→K1，各自带知识梳理/考向页，实战演练分属两节。"""
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
    assert tex.count("· 知识梳理}") == 2
    assert tex.index("子集与真子集") < tex.index("集合是确定性对象"), "节的顺序被排错了"
    assert tex.count(r"\blockhead{实战演练}") == 1      # 只有 K2 配了课后作业，归本讲
    assert "第一讲 集合间的基本关系 · 实战演练" in tex
    assert "第二讲 集合的概念 · 实战演练" not in tex     # K1 没有 extra_ids
    assert r"\lectsec{答案册}" not in tex
    daan, _ = build_lecture_latex(_preview_multi(), view="daan")
    assert daan.count(r"\lectsec{答案册}") == 1
    # 单节旧载荷行为不变
    single, _ = build_lecture_latex(_preview(), view="jiexi")
    assert single.count("· 知识梳理}") == 1


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
    for needle in ("子集与真子集", "集合是确定性对象", "第一讲集合间的基本关系·实战演练", "目录"):
        assert needle.replace(" ", "") in text, "PDF 里缺少 %s" % needle
