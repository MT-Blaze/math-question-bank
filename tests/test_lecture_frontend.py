# -*- coding: utf-8 -*-
"""讲义工作台前端回归：打印出口、三视图切换入口、缺失题警示。

只覆盖讲义专属文件（lecture.js / index.html 讲义工具栏区段），
不涉及录题界面脚本，避免与其他并行修改冲突。
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LECTURE_JS = PROJECT_ROOT / "static" / "js" / "lecture.js"
INDEX_HTML = PROJECT_ROOT / "static" / "index.html"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _lecture_toolbar_section() -> str:
    html = _read(INDEX_HTML)
    start = html.index('id="lectureGrade"')
    end = html.index('id="lectureTopicPanel"')
    return html[start:end]


def test_lecture_toolbar_exposes_print_and_three_view_switches():
    """讲义工具栏必须有打印入口和学生版/教师版/答案册三个视图切换按钮。"""
    section = _lecture_toolbar_section()
    assert "LectureManager.printLecture()" in section
    for view in ("yuan", "jiexi", "daan"):
        assert f"LectureManager.switchView('{view}')" in section


def test_lecture_js_implements_print_and_view_dispatch():
    """lecture.js 必须暴露 printLecture/switchView：打印走 iframe，
    视图切换经 postMessage 通知 iframe 内已定义的 switchView。"""
    src = _read(LECTURE_JS)
    assert "printLecture" in src
    assert "switchView" in src
    # 打印目标必须是讲义预览 iframe
    assert ".contentWindow.print()" in src
    # 视图切换必须通过 postMessage 送达 iframe（srcdoc 内脚本监听）
    assert "mathbank-set-view" in src


def test_iframe_view_listener_present():
    """注入 iframe 的脚本必须监听视图切换消息并调用 switchView。"""
    src = _read(LECTURE_JS)
    assert "mathbank-set-view" in src
    assert "d.view" in src or "data.view" in src


def test_preview_missing_ids_warning():
    """preview 返回 missing_ids 非空时，前端必须给出可见警示而不是静默吞掉。"""
    src = _read(LECTURE_JS)
    assert "missing_ids" in src
    assert "已删除" in src


def test_cover_title_editable_in_iframe():
    """封面大标题 iframe 内点击直改：blur 回库、Enter 结束编辑，父窗口 PUT settings。"""
    src = _read(LECTURE_JS)
    assert 'data-cover-edit="title"' in src
    assert "contenteditable" in src
    assert "mathbank-cover-title" in src
    # blur 写回 + Enter 阻止换行防止标题内出现 <div>/<br>
    assert '"blur"' in src or "'blur'" in src
    assert "preventDefault" in src



def test_cover_title_save_retry_on_500():
    """偶发 5xx 不能静默吞：自动重试一次，仍失败要 toast 报"保存失败"。"""
    src = _read(LECTURE_JS)
    assert "r.ok" in src  # 显式检查 HTTP 状态（r.json() 会把 500 体当成功解析）
    assert "保存失败" in src


def test_mindmap_connector_is_tree_bracket():
    """思维导图连线 = XMind 逻辑图式树括号：标签垂直居中、短接头、
    内容列左侧竖向主干、条目刻度线；悬空长线+圆点方案已废除。"""
    src = _read(LECTURE_JS)
    assert "mind-dot" not in src  # 悬空圆点彻底移除
    assert ".mind-branch{display:flex;align-items:center" in src  # 标签与内容垂直居中
    assert ".mind-branch-line{width:5mm;flex:none;border-top:2px solid var(--mc)}" in src  # 短线固定，不再 flex:1 拉伸穿白
    assert ".mind-items{flex:1;display:flex;flex-direction:column;gap:2mm;min-width:0;border-left:2px solid var(--mc);padding-left:4mm}" in src  # 竖向主干
    assert ".mind-items > *::before" in src  # 每条目从主干分出刻度线


def test_cover_author_fallback_keeps_space():
    """署名兜底文案保持「崔老师 编著」带空格。"""
    src = _read(LECTURE_JS)
    assert "崔老师 编著" in src
    assert "崔老师编著" not in src


def test_cover_version_text_black_above_author():
    """封面版本角标（学生版/教师版/答案册）：黑色、位于编著署名上方、不加粗、字号不变。"""
    import re

    src = _read(LECTURE_JS)
    ver = re.search(r"\.cover-ver\{([^}]*)\}", src).group(1)
    author = re.search(r"\.cover-author\{([^}]*)\}", src).group(1)
    assert "color:#000" in ver
    assert "font-weight:700" not in ver
    assert "font-size:16px" in ver  # 字号保持原样
    ver_bottom = float(re.search(r"bottom:([\d.]+)mm", ver).group(1))
    author_bottom = float(re.search(r"bottom:([\d.]+)mm", author).group(1))
    assert ver_bottom > author_bottom  # bottom 越大越靠上 → 角标在署名上方


# ============ 草稿树：系列抽屉 + 年份筛选 + 复制为新版 ============


def test_lecture_aside_has_tree_and_controls():
    """左侧栏必须有草稿树容器、届次筛选条、系列/轮次/年份三个控件。"""
    html = _read(INDEX_HTML)
    for element_id in (
        "lectureDraftTree",      # 两级树容器（替代旧平铺列表）
        "lectureYearBar",        # 顶部年份筛选 chips
        "lectureSeries",         # 讲义系列（同步/复习）
        "lectureDrawer",         # 抽屉（同步=册次，复习=轮次）
        "lectureYearSelect",     # 新版届次
    ):
        assert f'id="{element_id}"' in html, f"缺少 {element_id}"
    assert 'id="lectureDraftList"' not in html  # 旧平铺列表已退役


def test_draft_tree_two_series_and_drawers():
    """树结构：同步讲义/复习讲义两大类；同步抽屉=册次，复习抽屉=一轮/二轮/三轮。"""
    src = _read(LECTURE_JS)
    assert "同步讲义" in src and "复习讲义" in src
    assert "一轮复习" in src and "二轮复习" in src and "三轮复习" in src


def test_draft_tree_year_filter_and_archived():
    """年份筛选按 config.year/created_at 推导；archived 草稿收进「历史版本」折叠，
    选中具体届次时整树切换到该年份架子。"""
    src = _read(LECTURE_JS)
    assert "yearFilter" in src
    assert "archived" in src
    assert "历史版本" in src
    # 推导链：config.year 优先，缺失回退 created_at
    assert "created_at" in src


def test_clone_as_new_version_flow():
    """复制为新版：新行写系列/抽屉/届次并把旧行 status 置 archived，可恢复。"""
    src = _read(LECTURE_JS)
    assert "cloneDraft" in src
    assert "cloned_from" in src
    # 旧版收起 = PUT status archived；恢复 = PUT status draft
    assert re.search(r'"status"\s*:\s*"archived"', src) or "status: 'archived'" in src or 'status: "archived"' in src
    assert "'draft'" in src or '"draft"' in src


def test_save_draft_writes_series_into_config():
    """保存草稿必须把系列/抽屉/届次写进 config JSON（零数据库迁移方案）。"""
    src = _read(LECTURE_JS)
    for field in ("series", "drawer", "year"):
        assert re.search(field + r"\s*:", src), f"config 缺少 {field} 写入"


# ============ 知识块修订稿拼接 ============


def test_note_key_has_single_builder():
    """修订稿键必须只由 noteKey() 一处构造并与后端 note_key 同口径：
    散落的字符串拼接曾因 topic_id 为 undefined 拼出「...:undefined」而读不回内容。"""
    src = _read(LECTURE_JS)
    assert "function noteKey(" in src
    assert src.count("function noteKey(") == 1
    # 旧的裸拼接口径不得残留
    assert "':' + topicCfg.topic_id" not in src
    assert "'knowledge:' + compulsory" not in src
    # 转义用字符串拆分，不得引入后行断言
    assert "(?<!" not in src


def test_missing_topic_id_cannot_reach_or_silently_overwrite():
    """考点标识缺失时：编辑入口拒绝、预览如实标注，而不是渲染成「待补充」诱使用户重写。"""
    src = _read(LECTURE_JS)
    assert "该考向缺少考点标识" in src
    assert "if (!topicCfg.topic_id)" in src
    assert "topicCfg.topic_id || ''" in src


def test_unparsable_mindmap_does_not_leak_source():
    """@mindmap 前缀的内容解析失败时不得回落到把原始 JSON 当正文渲染。"""
    src = _read(LECTURE_JS)
    assert "src.indexOf('@mindmap') === 0" in src
    assert "思维导图数据无法解析" in src


def test_preview_warns_when_section_missing():
    """后端 section_found=False 时前端必须可见警示（与 missing_ids 同一口径）。"""
    src = _read(LECTURE_JS)
    assert "section_found" in src
    assert "当前教材树中未找到该小节" in src


def test_note_save_checks_http_status():
    """修订稿 PUT 的 4xx/5xx 错误体同样是合法 JSON，必须先看 r.ok 再谈成功。"""
    src = _read(LECTURE_JS)
    assert "function putNote(" in src
    # 保存路径不得再用裸 fetch + r.json()
    assert "if (!ST.noteTarget.topic_id) payload.topic_id" not in src
    assert "修订稿未读取成功" in src


def test_topic_number_follows_outline():
    """讲义正文与答案册的考向编号跟随大纲 t.no，与考向地图/选题面板同一口径。"""
    src = _read(LECTURE_JS)
    assert "function buildTopicMetaMap(" in src
    assert "topicMetaById" in src
    assert "'考向' + (idx + 1)" not in src


def test_outline_example_is_the_unrevised_default():
    """LectureNote 一直承诺「无修订则用大纲默认」：example 必须作为解题方法默认值
    渲染并标注未修订，且编辑弹窗以同一份内容为起点。"""
    src = _read(LECTURE_JS)
    assert "function outlineExampleOf(" in src
    assert "大纲默认 · 未修订" in src
    assert "method || methodDefault || pitEmpty" in src
    assert "（起点：大纲默认）" in src
    # example 是含 < 、∈ 等符号的纯文本，进 innerHTML 前必须转义
    assert "esc(outlineExample)" in src


# ============ 请求错误处理与草稿状态 ============


def test_all_lecture_requests_share_one_http_checked_exit():
    """讲义模块所有请求必须只走 apiCall 一个出口（apiJson / apiBlob 两个门面）：
    后端 4xx/5xx 的错误体同样是合法 JSON，裸 fetch + r.json() 会把「请求被拒绝」
    当成功继续往下执行。"""
    src = _read(LECTURE_JS)
    assert "function apiCall(" in src
    assert "function apiJson(" in src and "function apiBlob(" in src
    assert src.count("fetch(") == 1, "除 apiCall 内部不得再有裸 fetch"
    assert src.count("return r.json();") == 1
    assert "err.status = r.status" in src


def test_export_pdf_compiles_via_pipeline_and_reports_degradations():
    """导出 PDF 走组卷引擎（十秒级编译）：必须互斥防连点、置 aria-busy，
    且后端经响应头回传的排版降级要可见，不得只说「成功」。"""
    src = _read(LECTURE_JS)
    html = _read(INDEX_HTML)
    assert "LectureManager.exportPdf()" in html
    assert "X-MathBank-Export-Notes" in src
    assert "排版降级" in src
    assert "aria-busy" in src
    assert "ST.exporting" in src


def test_print_and_latex_export_are_two_distinct_exits():
    """浏览器打印与 exam-zh 高清 PDF 是两条出口，按钮要分得清。"""
    html = _read(INDEX_HTML)
    assert "浏览器打印" in html
    assert "导出 PDF" in html
    assert "打印 / PDF" not in html


def test_delete_draft_goes_through_checked_exit():
    """删除失败（Token 过期/已不存在）时不得清空编辑区。"""
    src = _read(LECTURE_JS)
    assert "apiJson('/api/lecture/drafts/' + id, { method: 'DELETE' })" in src
    assert "fetch('/api/lecture/drafts/' + id, { method: 'DELETE' })" not in src


def test_question_picker_does_not_silently_cap_at_100():
    """后端 page_size 上限是 100，请求 200 会被静默裁剪成 100：
    必须按 total 逐页取完，取不满时如实提示而不是假装这就是全部。"""
    src = _read(LECTURE_JS)
    assert "page_size=200" not in src
    assert "Q_PAGE_SIZE = 100" in src
    assert "first.total" in src
    assert "仅载入前" in src


def test_draft_title_not_rewritten_on_plain_save():
    """标题只在首次创建、或小节/届次确实变化时重写；
    【复制为新版】要生成新届次标题，而不是沿用旧版同名。"""
    src = _read(LECTURE_JS)
    assert "function draftTitle(" in src
    assert "stamp !== ST.draftStamp" in src
    assert re.search(r"body\.title\s*=\s*draftTitle", src)
    assert "title: d.title" not in src


def test_draft_load_cascades_without_timers():
    """载入草稿不得再用 setTimeout 等 DOM 级联刷新——慢机器上会加载到错误小节。"""
    src = _read(LECTURE_JS)
    assert re.search(r"setTimeout\(", src) is None
    assert "function selectHasValue(" in src
    assert "已不在当前教材树" in src


# ============ 批次 2：注入面、未保存编排、共享助手 ============


def test_year_bar_uses_delegation_not_inline_handlers():
    """届次取自草稿 config.year，拼进内联 onclick 或裸插值属性都是注入点。"""
    src = _read(LECTURE_JS)
    assert 'onclick="LectureManager.setYearFilter' not in src
    assert r"setYearFilter(\'" not in src
    assert "function bindYearBar(" in src
    assert "hasAttribute('data-year')" in src
    assert 'data-year="\' + esc(y) + \'"' in src


def test_orchestration_marks_dirty_and_snapshots_locally():
    """编排变更必须标脏并落本地快照：过去全部留在内存里，刷新即丢且毫无提示。"""
    src = _read(LECTURE_JS)
    assert "function commitOrchestration(" in src
    assert src.count("commitOrchestration();") >= 5, "五个编排入口都要收敛"
    assert "mathbank_lecture_autosave" in src
    assert "beforeunload" in src
    assert src.count("ST.dirty = false;") >= 3  # 保存/新建/载入/丢弃 后清脏


def test_autosave_bar_never_uses_innerhtml():
    """快照提示条只改 textContent 与类名：小节名等来自草稿 JSON，不得拼进 HTML。"""
    src = _read(LECTURE_JS)
    start = src.index("function renderAutosaveBar(")
    body = src[start:start + src[start:].index("\n  }")]
    assert "innerHTML" not in body
    assert "textContent" in body


def test_note_modal_exposes_restore_default():
    """已保存的修订稿必须能删掉回到默认，且只在库里确有内容时才给这个入口。"""
    src = _read(LECTURE_JS)
    html = _read(INDEX_HTML)
    assert 'id="lectureNoteDelete"' in html
    assert "function deleteNote(" in src
    assert "setNoteDeleteVisible(!!String(content).trim())" in src


def test_escape_helper_delegates_to_shared_source():
    """转义唯一来源是 api.js 的 window.escapeHtml，讲义不得再自带第二套实现。"""
    src = _read(LECTURE_JS)
    assert "function esc(s) { return window.escapeHtml(s); }" in src
    assert "replace(/&/g, '&amp;')" not in src


def _extract_js_function(src: str, name: str) -> str:
    """按 lecture.js 的固定缩进（函数体 4 空格、闭合 `}` 在 2 空格列）切出完整函数。"""
    start = src.index("function " + name + "(")
    end = src.index("\n  }\n", start)
    return src[start:end + len("\n  }")]


# lecture.js 里这两个纯小函数以 `\n  }\n\n` 结尾（后面紧跟空行而非嵌套块），
# _extract_js_function 的截断点会吃掉收尾换行，单独抽取时按整函数补齐。
_JS_FUNCTION_FULL = {
    "secKey": (
        "  function secKey(compulsory, chapter, knowledge) {\n"
        "    return [compulsory, chapter, knowledge].join('|');\n"
        "  }\n"
    ),
    "sectionOf": (
        "  function sectionOf(entry) {\n"
        "    return entry ? { compulsory: entry.compulsory, chapter: entry.chapter,"
        " knowledge: entry.knowledge } : null;\n"
        "  }\n"
    ),
    "ensureActiveEntry": (
        "  function ensureActiveEntry() {\n"
        "    if (!ST.compulsory || !ST.chapter || !ST.knowledge) return null;\n"
        "    return findSectionEntry(secKey(ST.compulsory, ST.chapter, ST.knowledge));\n"
        "  }\n"
    ),
}


def _preview_render_prelude() -> str:
    """渲染链真跑所需的最小宿主：esc/题目解析器/思维导图分支表 + DOM 桩。"""
    src = _read(LECTURE_JS)
    start = src.index("  var MIND_COLORS")
    end = src.index("\n  }\n", src.index("function outlineExampleOf"))
    chain = src[start:end + len("\n  }")]
    return (
        "var commits = 0; function commitOrchestration() { commits++; }\n"
        "function __noActiveEntry() { return null; }\n"
        "global.__dom = {};\n"
        "function $(id) {\n"
        "  if (!global.__dom[id]) global.__dom[id] = { value: '', classList: { toggle: function () {}, add: function () {}, remove: function () {} }, appendChild: function () {}, removeChild: function () {}, firstChild: null };\n"
        "  return global.__dom[id];\n"
        "}\n"
        "var document = {\n"
        "  createElement: function () { return { setAttribute: function () {},"
        " classList: { toggle: function () {} }, appendChild: function () {}, style: {} }; },\n"
        "  getElementById: function (id) { return $(id); },\n"
        "};\n"
        "function renderTopicPanel() {}\nfunction refreshPreview() {}\n"
        "var window = global;\n"
        "window.escapeHtml = function (s) { return String(s == null ? '' : s)"
        " .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')"
        " .replace(/\"/g, '&quot;'); };\n"
        "window.parseMarkdownWithMath = function (s) { return 'MD:' + String(s).slice(0, 12); };\n"
        + chain + "\n"
    )


def test_topic_configs_are_materialized_not_invented_per_card():
    """考向配置必须落进 ST.topics：面板把下标直接传给 addItem/editNote，
    只在渲染时临时造对象会让新建讲义的选题与易错点入口静默失效。"""
    src = _read(LECTURE_JS)
    assert "function ensureTopicConfigs(" in src
    panel = _extract_js_function(src, "renderTopicPanel")
    assert "ensureTopicConfigs();" in panel
    assert "ST.topics[idx] || {" not in src, "不得再就地伪造考向配置"



def _run_extracted_lecture_js(
    names: tuple[str, ...],
    st_literal: str,
    body: str,
    helpers: str = "",
    prelude: str | None = None,
    aliases: tuple[tuple[str, str], ...] = (("ensureActiveEntry", "__noActiveEntry"),),
    names_out: dict[str, str] | None = None,
) -> None:
    """把 lecture.js 里的若干函数抽出来在 node 里真跑一遍。

    抽取的函数必须包进闭包：顶层 `function foo(){}` 会在模块作用域重新绑定同名符号，
    把外层预置的桩（如 commitOrchestration）遮掉，桩就白写了。
    ``helpers`` 放依赖的纯小函数（secKey/sectionOf 等），``prelude`` 可覆盖默认桩，
    ``aliases`` 把模块作用域里必然存在、但未被抽取的引用指到替身函数上。
    """
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    src = _read(LECTURE_JS)
    if prelude is None:
        prelude = (
            "var commits = 0;\n"
            "function commitOrchestration() { commits++; }\n"
            # lecture.js 的模块作用域里总有这些 DOM/工具引用；抽出单函数真跑时给最小替身，
            # 桩不得与抽出的同名（hoist 会互相覆盖）
            "function __noActiveEntry() { return null; }\n"
            "global.__dom = {};\n"
            "function $(id) {\n"
            "  if (!global.__dom[id]) global.__dom[id] = { value: '', classList: { toggle: function () {}, add: function () {}, remove: function () {} }, appendChild: function () {}, removeChild: function () {}, firstChild: null };\n"
            "  return global.__dom[id];\n"
            "}\n"
            "var window = global;\n"
            # lecture.js 的渲染函数一律走模块内 `$(id)` 助手；桩必须同时喂到 document，
            # 否则 getElementById 返回 null 会让渲染函数整段短路。
            "var document = {\n"
            "  createElement: function () { return { setAttribute: function () {},"
            " classList: { toggle: function () {} }, appendChild: function () {}, style: {} }; },\n"
            "  getElementById: function (id) { return $(id); },\n"
            "};\n"
            # 面板/预览重绘属于 DOM 层（本文件的面板测试覆盖），这里只计数
            "function renderTopicPanel() { global.__panelRenders = (global.__panelRenders || 0) + 1; }\n"
            "function refreshPreview() { global.__previewRefreshes = (global.__previewRefreshes || 0) + 1; }\n"
        )
    script = "".join((
        prelude,
        "var ST = ", st_literal, ";\n",
        helpers,
        "var api = (function () {\n",
        *(name + ":" + alias + "," for name, alias in aliases),
        *(_extract_js_function(src, name) + "\n" for name in names),
        "return {", *(k + ":" + v + "," for k, v in (names_out or {n: n for n in names}).items()), "};\n",
        "})();\n",
        "function eq(a, b, msg) { if (JSON.stringify(a) !== JSON.stringify(b))"
        " throw new Error(msg + ': ' + JSON.stringify(a)); }\n",
        body,
    ))
    handle = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8")
    with handle:
        handle.write(script)
    try:
        done = subprocess.run(
            [node, handle.name], capture_output=True, text=True,
            check=False, encoding="utf-8", errors="replace")
    finally:
        Path(handle.name).unlink()
    assert done.returncode == 0, (done.stdout or "") + (done.stderr or "")


def test_ensure_topic_configs_keeps_orchestration_and_order():
    """真跑 reconcile：对齐小节顺序、保留已选题、不丢大纲里已消失的旧编排。"""
    _run_extracted_lecture_js(
        ("secKey", "sectionOf", "findSectionEntry", "ensureActiveEntry", "ensureTopicConfigs"),
        "{ sections: [], activeKey: '', compulsory: '', chapter: '', knowledge: '',"
        " section: { topics: [{id:'k_a',name:'A'},{id:'k_b',name:'B'},{id:'k_c',name:'C'}] },"
        " topics: [{topic_id:'k_z',name:'旧考向Z',items:[{question_id:7,role:'example'}]},"
        "{topic_id:'k_b',name:'B旧',items:[{question_id:3,role:'variant'}]}] }",
        "".join((
            "api.ensureTopicConfigs();\n",
            "eq(ST.topics.map(function (t) { return t.topic_id; }),"
            " ['k_a','k_b','k_c','k_z'], '顺序或保留错误');\n",
            "eq(ST.topics[1].items, [{question_id:3, role:'variant'}], '已选题目被丢弃');\n",
            "eq(ST.topics[3].items, [{question_id:7, role:'example'}], '旧编排丢失');\n",
            "eq(ST.topics[0].items, [], '空槽未初始化');\n",
            "if (ST.topics[0].name !== 'A') throw new Error('考向名未跟随大纲');\n",
            "api.ensureTopicConfigs();\n",
            "eq(ST.topics.length, 4, '重复调用不幂等');\n",
            "console.log('OK');\n",
        )),
        helpers="", aliases=(),
    )


def test_orchestration_edits_land_on_the_selected_topic_only():
    """选题/改角色/移除必须落在传入的考向下标上。

    本机题库只有一道未分类题，浏览器里点不到真实选题，所以把函数抽出来真跑，
    补上「0 题可选」时验不到的那一半。
    """
    _run_extracted_lecture_js(
        ("ensureActiveEntry", "findSectionEntry", "secKey", "sectionOf",
         "ensureTopicConfigs", "addItem", "setRole", "removeItem", "addExtra", "removeExtra"),
        "{ sections: [], activeKey: 'M|C1|K1', compulsory: 'M', chapter: 'C1', knowledge: 'K1',"
        " section: { topics: [{id:'k_a',name:'A'},{id:'k_b',name:'B'}] },"
        " topics: [], extraIds: [], questions: [] }",
        "".join((
            "api.ensureTopicConfigs();\n",
            "ST.__anchor = ST.topics;\n",
            "eq(ST.topics.map(function (t) { return t.topic_id; }), ['k_a','k_b'], '未对齐');\n",
            "if (ST.topics !== ST.__anchor) throw new Error('reconcile 后数组脱钩，面板改动会丢');\n",
            "api.addItem(1, 42);\n",
            "eq(ST.topics[1].items, [{question_id:42, role:'example'}], '选题落错考向');\n",
            "eq(ST.topics[0].items, [], '题目串到了别的考向');\n",
            "if (commits !== 1) throw new Error('编排未提交: ' + commits);\n",
            "api.addItem(1, 42);\n",
            "eq(ST.topics[1].items.length, 1, '重复选题未去重');\n",
            "if (commits !== 1) throw new Error('重复选题还标脏: ' + commits);\n",
            "api.setRole(1, 0, 'variant');\n",
            "eq(ST.topics[1].items[0].role, 'variant', '改角色无效');\n",
            "api.removeItem(1, 0);\n",
            "eq(ST.topics[1].items, [], '移除无效');\n",
            "if (commits !== 3) throw new Error('提交次数不对: ' + commits);\n",
            "api.addExtra(7); api.addExtra(7);\n",
            "eq(ST.extraIds, [7], '实战演练去重失败');\n",
            "api.removeExtra(0);\n",
            "eq(ST.extraIds, [], '实战演练移除失败');\n",
            "console.log('OK');\n",
        )),
        helpers=_section_helpers(), aliases=(),
    )


def _section_helpers() -> str:
    return "".join(_JS_FUNCTION_FULL[name] for name in ("secKey", "sectionOf"))


def test_multi_section_orchestration_is_scoped_per_section():
    """多节合并：编排落在激活节上，切节不丢别的节的题，移除节后预览重算。"""
    st = ("{ sections: [], activeKey: '', compulsory: 'M', chapter: 'C1', knowledge: 'K1', section: null,"
          " topics: [], extraIds: [], questions: [], knowledgeMind: null }")
    body = "".join((
        # 第一节：ensureTopicConfigs 造出的槽位必须锚回节对象
        "api.ensureSectionEntry('M','C1','K1');\n",
        "var e1 = api.findSectionEntry('M|C1|K1');\n",
        "e1.section = { topics: [{id:'k_a',name:'A'},{id:'k_b',name:'B'}] };\n",
        "api.activateSection(e1);\n",
        "api.ensureTopicConfigs();\n",
        "if (e1.topics !== ST.topics) throw new Error('reconcile 后数组脱钩');\n",
        "api.addItem(0, 11);\n",
        "eq(e1.topics[0].items, [{question_id:11, role:'example'}], '选题未落激活节');\n",
        # 第二节：切过去后第一节保持原样
        "api.ensureSectionEntry('M','C1','K2');\n",
        "var e2 = api.findSectionEntry('M|C1|K2');\n",
        "e2.section = { topics: [{id:'k_c',name:'C'}] };\n",
        "api.activateSection(e2);\n",
        "api.ensureTopicConfigs();\n",
        "api.addItem(0, 22);\n",
        "eq(e2.topics[0].items, [{question_id:22, role:'example'}], '第二节选题失败');\n",
        "eq(e1.topics[0].items, [{question_id:11, role:'example'}], '切节污染第一节');\n",
        # 载荷顺序 = 勾选顺序，不是激活顺序
        "eq(api.collectSectionsPayload().map(function (s) { return s.knowledge; }), ['K1','K2'], '节顺序错误');\n",
        "eq(api.collectSectionsPayload()[0].topics.length, 2, '第一节考向数不对');\n",
        # 移除激活节 → 自动落到剩下的节
        "api.removeSectionEntry('M|C1|K2');\n",
        "eq(ST.activeKey, 'M|C1|K1', '移除后未回落到剩余节');\n",
        "eq(ST.sections.map(function (s) { return s.key; }), ['M|C1|K1'], '移除失败');\n",
        "if (!global.__previewRefreshes) throw new Error('移除节后预览未重算');\n",
        "console.log('OK');\n",
    ))
    _run_extracted_lecture_js(
        ("findSectionEntry", "ensureSectionEntry", "activateSection", "secKey", "sectionOf",
         "ensureActiveEntry", "collectSectionsPayload", "removeSectionEntry",
         "renderSectionChips", "renderTopicPanelFromState",
         "ensureTopicConfigs", "addItem"),
        st, body, helpers="", aliases=(),
    )


def _preview_render_prelude() -> str:
    """渲染链真跑所需的最小宿主：esc/题目解析器 + 整段渲染函数（noteKey → buildLectureHtml）。

    lecture.js 的渲染函数全部以 2 空格缩进闭合，从 MIND_COLORS 到 buildLectureHtml
    是连续区间，整段截取比逐个抽取少两类脆弱性（漏依赖、截断点错位）。
    """
    src = _read(LECTURE_JS)
    start = src.index("  function matchCoverKey")
    end = src.index("\n  }\n", src.index("function buildLectureHtml"))
    chain = src[start:end + len("\n  }")]
    return (
        "var commits = 0; function commitOrchestration() { commits++; }\n"
        "function __noActiveEntry() { return null; }\n"
        "global.__dom = {};\n"
        "function $(id) {\n"
        "  if (!global.__dom[id]) global.__dom[id] = { value: '', classList: { toggle: function () {}, add: function () {}, remove: function () {} }, appendChild: function () {}, removeChild: function () {}, firstChild: null };\n"
        "  return global.__dom[id];\n"
        "}\n"
        "var document = {\n"
        "  createElement: function () { return { setAttribute: function () {},"
        " classList: { toggle: function () {} }, appendChild: function () {}, style: {} }; },\n"
        "  getElementById: function (id) { return $(id); },\n"
        "};\n"
        "function renderTopicPanel() {}\nfunction refreshPreview() {}\n"
        "var window = global;\n"
        "window.escapeHtml = function (s) { return String(s == null ? '' : s)"
        " .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')"
        " .replace(/\"/g, '&quot;'); };\n"
        "window.parseMarkdownWithMath = function (s) { return 'MD:' + String(s).slice(0, 12); };\n"
        # buildLectureHtml 会把修订稿回落到各节的编排容器上（思维导图编辑入口），需要查节助手
        + _JS_FUNCTION_FULL["secKey"]
        + "  function esc(s) { return window.escapeHtml(s); }\n"
        + "  var ROLE_LABELS = { example: '例题', variant: '变式', practice: '趁热打铁', homework: '实战演练' };\n"
        + "  function roleVal(role) { return ROLE_LABELS[role] || ROLE_LABELS.example; }\n"
        + _JS_FUNCTION_FULL["sectionOf"]
        + "  function findSectionEntry(key) {\n"
        "    for (var i = 0; i < ST.sections.length; i++) {\n"
        "      if (ST.sections[i].key === key) return ST.sections[i];\n"
        "    }\n"
        "    return null;\n"
        "  }\n"
        + _JS_FUNCTION_FULL["ensureActiveEntry"]
        # buildMindmapSheets 按节存放思维导图状态：桩保留同一落点，只省掉排版
        + "  function buildMindmapSheets(data, compulsory, chapter, knowledge, raw) {\n"
        "    var entry = findSectionEntry(secKey(compulsory, chapter, knowledge));\n"
        "    if (entry) { entry.knowledgeMind = { data: data, raw: raw }; ST.knowledgeMind = entry.knowledgeMind; }\n"
        "    return '<div class=\"sheet\"><div class=\"mind-branch-label\">'\n"
        "      + esc((data.branches[0] || {}).label || '') + '</div></div>';\n"
        "  }\n"
        # CSS 文本与本次断言无关，桩掉避免把整段样式带进沙箱
        + "  function LECTURE_CSS() { return '.stub{}'; }\n"
        + chain + "\n"
    )


def test_merged_preview_html_orders_sections_once_each():
    """真跑 buildLectureHtml：封面 → 目录 → 逐讲（知识梳理/考向页/本讲实战演练）→ 答案册；
    每节各一次且顺序跟随载荷——这是合并讲义「拼得上」的前端半边。"""
    st = ("{ sections: [], activeKey: '', compulsory: '', chapter: '', knowledge: '', section: null,"
          " topics: [], extraIds: [], questions: [], knowledgeMind: null, qIndex: {}, previewData: null, coverBg: {} }")
    body = "".join((
        # 预置一节容器：buildLectureHtml 会把修订稿/思维导图状态回落到节上，
        # 而「加载小节」在真实流程里必然先建好容器（多节测试已覆盖该路径）
        "ST.sections.push({key:'M|C1|K2', compulsory:'M', chapter:'C1', knowledge:'K2', topics:[], extraIds:[]});\n",
        "ST.sections.push({key:'M|C1|K1', compulsory:'M', chapter:'C1', knowledge:'K1', topics:[], extraIds:[]});\n",
        "var data = {\n",
        "  settings: { cover_title: '第一章 集合', cover_subtitle: '同步讲义' },\n",
        "  sections: [\n",
        # K1 用 ASCII 标记：@mindmap 的 JSON.stringify 会把中文转义成 \\uXXXX，
        # indexOf 按字面量找会假失败；ASCII 标签绕开转义又不影响顺序断言
        "    { section: { compulsory:'M', chapter:'C1', knowledge:'K2', name:'K2', topics:[{id:'k_c',name:'子集判断',no:1}] },\n",
        "      notes: { 'knowledge:M:C1:K2': '<p>ZIJISECTION</p>' },\n",
        "      topics: [{ topic_id:'k_c', name:'子集判断', items:[{question_id:3, role:'example'}] }],\n",
        "      extra_ids: [4], missing_ids: [], invalid_ids: [] },\n",
        "    { section: { compulsory:'M', chapter:'C1', knowledge:'K1', name:'K1', topics:[{id:'k_a',name:'集合判断',no:1}] },\n",
        "      notes: { 'knowledge:M:C1:K1': '@mindmap' + String.fromCharCode(10) + JSON.stringify({title:'T',branches:[{label:'MINDK1',items:[{label:'HUYI'}]}]}) },\n",
        "      topics: [{ topic_id:'k_a', name:'集合判断', items:[{question_id:1, role:'example'}] }],\n",
        "      extra_ids: [], missing_ids: [], invalid_ids: [] }\n",
        "  ],\n",
        "  questions: [\n",
        "    { id:1, content:'题一', question_type:'detailed_answer', answer:'答一', sources: [] },\n",
        "    { id:3, content:'题三', question_type:'detailed_answer', answer:'', sources: [] },\n",
        "    { id:4, content:'题四', question_type:'fill_in_blank', answer:'答四', sources: [] }\n",
        "  ]\n",
        "};\n",
        "ST.topics = data.sections[1].topics;\n",
        "var html = api.buildLectureHtml(data);\n",
        "if (typeof html !== 'string') throw new Error('preview 未产出 HTML');\n",
        # 封面一份 + 紧随其后的目录页（多节才有；仅讲级，讲次中文数字）
        "if ((html.match(/class=\"sheet cover\"/g) || []).length !== 1) throw new Error('封面不止一份');\n",
        "if (html.indexOf('目　录') < 0) throw new Error('目录页缺失');\n",
        "if (html.indexOf('第一讲 K2') > html.indexOf('第二讲 K1')) throw new Error('目录顺序被排错');\n",
        "if (html.indexOf('目　录') > html.indexOf('第一讲 K2')) throw new Error('目录没排在正文前');\n",
        "if (html.indexOf('toc-sub') >= 0) throw new Error('目录里还混着子级条目');\n",
        # 每节各一次考向页（考向级已不进目录，故仅正文 1 处）；页眉不带章名
        "if ((html.match(/考向一　子集判断/g) || []).length !== 1) throw new Error('K2 考向页缺失');\n",
        "if ((html.match(/考向一　集合判断/g) || []).length !== 1) throw new Error('K1 考向页缺失');\n",
        "if (html.indexOf('C1 · ') >= 0) throw new Error('页眉还带着章名');\n",
        # 思维导图节照常渲染且按节存状态；实战演练归各自讲、裸号
        "if (html.indexOf('ZIJISECTION') > html.indexOf('MINDK1')) throw new Error('节顺序被排错了');\n",
        "if (html.indexOf('mind-branch-label') < 0) throw new Error('思维导图节未渲染');\n",
        "if (!ST.sections[1].knowledgeMind) throw new Error('思维导图状态未按节存放');\n",
        "if (ST.sections[0].knowledgeMind) throw new Error('思维导图状态串到了别的节');\n",
        "if (html.indexOf('【实战演练】') < 0) throw new Error('实战演练缺失');\n",
        "if (html.indexOf('实战1') >= 0) throw new Error('实战演练还在冠整本编号');\n",
        "if (html.indexOf('例一-1') < 0) throw new Error('例题未按 例<考向>-<序> 编号');\n",
        # 答案册整本一份、带「第X讲」定位前缀且覆盖两节的题
        "if ((html.match(/id=\"page-answer-book\"/g) || []).length !== 1) throw new Error('答案册不是一份');\n",
        "if (html.indexOf('第一讲　考向一　例一-1') < 0) throw new Error('答案册缺 K2 的定位前缀条目');\n",
        "if (html.indexOf('第二讲　考向一　例一-1') < 0) throw new Error('答案册缺 K1 的定位前缀条目');\n",
        # 单节讲义不出目录页（用户规则：一节一本，薄得不需要）
        "var single = { settings: {}, section: { compulsory:'M', chapter:'C1', knowledge:'K1', name:'K1',\n",
        "    topics:[{id:'k_a',name:'集合判断',no:1}] }, notes: {},\n",
        "  topics: [{ topic_id:'k_a', name:'集合判断', items:[{question_id:1, role:'example'}] }],\n",
        "  extra_ids: [], questions: [{ id:1, content:'题一', question_type:'detailed_answer', answer:'', sources: [] }] };\n",
        "var sHtml = api.buildLectureHtml(single);\n",
        "if (sHtml.indexOf('目　录') >= 0) throw new Error('单节讲义不该出目录页');\n",
        "console.log('OK');\n",
    ))
    _run_extracted_lecture_js(
        ("buildLectureHtml",), st, body,
        helpers="", prelude=_preview_render_prelude(), aliases=(),
    )


def test_lecture_numbering_resets_per_topic_and_block_heads():
    """真跑渲染链钉住新口径：变式跨母题连续、趁热打铁按考向归零、板块头只在首题前出一次。"""
    st = ("{ sections: [], activeKey: '', compulsory: '', chapter: '', knowledge: '', section: null,"
          " topics: [], extraIds: [], questions: [], knowledgeMind: null, qIndex: {}, previewData: null, coverBg: {} }")
    body = "".join((
        "var data = {\n",
        "  settings: { cover_title: 'T', cover_subtitle: 'S' },\n",
        "  section: { compulsory:'M', chapter:'C1', knowledge:'K1', name:'K1',\n",
        "    topics:[{id:'k_a',name:'甲',no:1},{id:'k_b',name:'乙',no:2}] },\n",
        "  notes: {},\n",
        "  topics: [\n",
        # 考向甲的题 id 用 1x、考向乙用 2x：内容串里不出现「变式2」等子串的假命中
        "    { topic_id:'k_a', name:'甲', items:[\n",
        "      {question_id:11, role:'example'},  {question_id:12, role:'variant'},\n",
        "      {question_id:13, role:'example'},  {question_id:14, role:'variant'},\n",
        "      {question_id:15, role:'practice'}, {question_id:16, role:'practice'}]},\n",
        "    { topic_id:'k_b', name:'乙', items:[\n",
        "      {question_id:21, role:'variant'},  {question_id:22, role:'practice'}]}\n",
        "  ],\n",
        "  extra_ids: [31, 32],\n",
        "  questions: [11,12,13,14,15,16,21,22,31,32].map(function (i) {\n",
        "    return { id:i, content:'Q'+i, question_type:'detailed_answer', answer:'A'+i, sources: [] };\n",
        "  })\n",
        "};\n",
        "var html = api.buildLectureHtml(data);\n",
        # 考向一：例一-1 → 变式1（跨母题连续）→ 例一-2 → 变式2；趁热打铁 1、2 裸号
        "for (var i = 0; i < html.length; i++) {}\n",
        "['例一-1','变式1','例一-2','变式2'].forEach(function (t) {\n",
        "  if ((html.match(new RegExp(t.replace(/[.*+?^${}()|[\\]\\\\]/g,'\\\\$&'),'g'))||[]).length < 1)"
        " throw new Error('缺 '+t);\n",
        "});\n",
        # 答案册里考向乙的孤儿变式必须从 变式1 重排（每考向归零）：
        # 「考向二　变式2」若出现，说明跨考向没归零或编号串了槽
        "if (html.indexOf('考向二　变式2') >= 0) throw new Error('变式没有按考向归零');\n",
        # 钉住正文侧标签序列本身（沙箱里 api 只导出 buildLectureHtml，裸函数不可见）：
        # 考向一应为 例一-1 → 变式1 → 例一-2 → 变式2（跨母题连续、不按母题归零）
        "var tagRe = /<span class=\"tag\">([^<]*)<\\/span>/g; var seq = [], m;\n",
        "while ((m = tagRe.exec(html))) { if (/^(例|变式)/.test(m[1])) seq.push(m[1]); }\n",
        "eq(seq.slice(0, 4), ['例一-1','变式1','例一-2','变式2'], '考向一题号序列不对');\n",
        # 答案册侧同一份口径：定位前缀 + 标签，按出现顺序排（实战演练裸号穿插其间）
        "var ansRe = /<span class=\"ans-tag\">([^<]*)<\\/span>/g; var aseq = [], am;\n",
        "while ((am = ansRe.exec(html))) aseq.push(am[1]);\n",
        "if (aseq.join('|').indexOf('第一讲　考向一　例一-1|第一讲　考向一　变式1|"
        "第一讲　考向一　例一-2|第一讲　考向一　变式2') < 0) throw new Error('答案册题号序列不对');\n",
        "eq(seq.filter(function (t) { return t === '变式3'; }).length, 0, '变式不该越过考向内的 N 继续排');\n",
        # 「变式2」必须出现在「例一-2」之后、「例一-1」之前（跨母题连续，不按母题归零）
        "if (!(html.indexOf('例一-1') < html.indexOf('变式1')"
        " && html.indexOf('变式1') < html.indexOf('例一-2')"
        " && html.indexOf('例一-2') < html.indexOf('变式2'))) throw new Error('变式编号没有跨母题连续排');\n",
        "if (html.indexOf('>1<') < 0) throw new Error('趁热打铁没出裸号');\n",
        "if ((html.match(/【实战演练】/g)||[]).length !== 2) throw new Error('正文+答案册应各出一块实战演练');\n",
        "if ((html.match(/【趁热打铁】/g)||[]).length !== 4) throw new Error('趁热打铁板块头数量不对（正文两考向+答案册两处）');\n",
        # 孤儿变式（考向乙没配例题）不再冠「典型例题」头，避免空板块头；答案册同口径
        "if ((html.match(/【典型例题】/g)||[]).length !== 2) throw new Error('典型例题板块头数量不对（正文+答案册各一处）');\n",
        # 考向二无例题：孤儿变式挂到考向二名下从 变式1 起、裸号趁热打铁从 1 起
        "if (html.indexOf('第一讲　考向二　变式1') < 0) throw new Error('答案册缺考向二孤儿变式条目');\n",
        "console.log('OK');\n",
    ))
    _run_extracted_lecture_js(
        ("buildLectureHtml",), st, body,
        helpers="", prelude=_preview_render_prelude(), aliases=(),
    )


def test_pit_method_bare_lt_survives_rendering():
    """修订稿 \(0<a<1\) 里的裸 < 曾把 KaTeX 段整段吃掉（浏览器当未知标签）：
    渲染前数学段必须走正文同款管线转成 \lt/\gt；真标签保留，script 注入堵死。"""
    st = ("{ sections: [], activeKey: '', compulsory: '', chapter: '', knowledge: '', section: null,"
          " topics: [], extraIds: [], questions: [], knowledgeMind: null, qIndex: {}, previewData: null, coverBg: {} }")
    body = "".join((
        "var data = {\n",
        "  settings: { cover_title: 'T' },\n",
        "  section: { compulsory:'M', chapter:'C1', knowledge:'K1', name:'K1',\n",
        "    topics:[{id:'k_a',name:'甲',no:1}] },\n",
        "  notes: {\n",
        "    'pit:M:C1:K1:k_a': '\\(0<a<1\\) 递减。',\n",
        "    'method:M:C1:K1:k_a': '\\(2^{x}<8\\)。'\n",
        "      + '<scr' + 'ipt>alert(2)<\\/scr' + 'ipt>'\n",
        "  },\n",
        "  topics: [{ topic_id:'k_a', name:'甲', items:[{question_id:1, role:'example'}] }],\n",
        "  extra_ids: [], questions: [{ id:1, content:'Q', question_type:'detailed_answer', answer:'', sources: [] }]\n",
        "};\n",
        "var rr = api.renderRevisionHtml;\n",
        # 数学段内 < > 换成 \lt \gt（KaTeX 认、HTML 不切标签）；已有 \lt 不二次转义
        "eq(rr('\\\\(2^{x}<8\\\\)'), '\\\\(2^{x}\\\\lt 8\\\\)', 'method 数学段没走同款转义');\n",
        "eq(rr('\\\\(0<a<1\\\\)'), '\\\\(0\\\\lt a\\\\lt 1\\\\)', 'pit 的 0<a<1 没有完整存活');\n",
        "eq(rr('\\\\(a>0\\\\)'), '\\\\(a\\\\gt 0\\\\)', '裸 > 没转 \\\\gt');\n",
        "eq(rr('\\\\(x\\\\lt y\\\\)'), '\\\\(x\\\\lt y\\\\)', '\\\\lt 被二次转义');\n",
        # 真标签保留；`<b</p>` 这种畸形尾巴浏览器本就当文本（与宿主渲染一致）
        "eq(rr('<p>a<b</p>'), '<p>a<b</p>', '合法标签开头的 < 不该动');\n",
        "eq(rr('x<a1y'), 'x&lt;a1y', '段外裸 < 没转义');\n",
        "if (rr('<scr'+'ipt>alert(2)<\\\\/scr'+'ipt>').indexOf('<script>') === 0)"
        " throw new Error('script 注入未堵');\n",
        "console.log('OK');\n",
    ))
    _run_extracted_lecture_js(
        ("renderRevisionHtml",), st, body,
        helpers="", prelude=_preview_render_prelude(), aliases=(),
    )


def test_autosave_guard_counts_questions_not_topic_slots():
    """小节一加载就有空考向槽，快照判空必须数题；多节时总数要跨节累加。"""
    src = _read(LECTURE_JS)
    body = _extract_js_function(src, "snapshotOrchestration")
    assert "snapshotTotal(" in body
    assert "if (!ST.topics.length" not in body
    st = ("{ sections: [{key:'M|C1|K1', compulsory:'M', chapter:'C1', knowledge:'K1',"
          " topics:[{topic_id:'k_a',items:[{question_id:1,role:'example'}]}], extraIds:[]}],"
          " activeKey:'M|C1|K1', compulsory:'M', chapter:'C1', knowledge:'K1',"
          " topics:[{topic_id:'k_a',items:[{question_id:1,role:'example'}]}], extraIds:[], questions: [] }")
    prelude = (
        "var commits = 0;\nfunction commitOrchestration() { commits++; }\n"
        "var __store = {};\n"
        "global.__dom = { lectureGrade: { value: 'g1' } };\n"
        "function $(id) { return global.__dom[id] || null; }\n"
        "var AUTOSAVE_KEY = 'mathbank_lecture_autosave';\n"
        "var localStorage = { setItem: function (k, v) { __store[k] = v; },"
        " getItem: function (k) { return __store[k] || null; }, removeItem: function (k) { delete __store[k]; } };\n"
        "function toast() {}\n"
    )
    body_js = "".join((
        "api.snapshotOrchestration();\n",
        "var snap = JSON.parse(__store['mathbank_lecture_autosave']);\n",
        "eq(snap.sections.map(function (s) { return s.knowledge; }), ['K1'], '快照未含全部节');\n",
        "if (api.snapshotTotal(snap) !== 1) throw new Error('跨节计数错误');\n",
        "ST.sections.push({key:'M|C1|K2', compulsory:'M', chapter:'C1', knowledge:'K2', topics:[], extraIds:['7']});\n",
        "api.snapshotOrchestration();\n",
        "var snap2 = JSON.parse(__store['mathbank_lecture_autosave']);\n",
        "if (api.snapshotTotal(snap2) !== 2) throw new Error('第二节的实战演练没进快照');\n",
        "console.log('OK');\n",
    ))
    _run_extracted_lecture_js(
        ("collectConfig", "secKey", "sectionOf", "findSectionEntry", "ensureActiveEntry",
         "collectSectionsPayload", "snapshotCount", "snapshotTotal", "clearAutosave",
         "snapshotOrchestration"),
        st, body_js, helpers="", prelude=prelude, aliases=(),
        names_out={"snapshotOrchestration": "snapshotOrchestration",
                   "snapshotTotal": "snapshotTotal", "collectSectionsPayload": "collectSectionsPayload"},
    )


def test_orphan_notes_have_a_visible_list_and_explicit_delete():
    """孤儿修订稿必须看得见、逐条删得掉：只报告，绝不自动清理。"""
    src = _read(LECTURE_JS)
    html = _read(INDEX_HTML)
    for element in ('id="lectureOrphanModal"', 'id="lectureOrphanList"', 'id="lectureOrphanCount"'):
        assert element in html
    assert "LectureManager.openOrphans()" in _lecture_toolbar_section()
    assert "/api/lecture/orphan-notes" in src
    assert "{ method: 'DELETE' }" in src
    assert "createElement" in _extract_js_function(src, "orphanLine")
    for name in ("orphanCard", "renderOrphanList", "deleteOrphan"):
        body = _extract_js_function(src, name)
        assert "innerHTML" not in body, name + "：行内容来自 note_key，不得拼进 HTML"
    assert "addEventListener" in _extract_js_function(src, "orphanCard")
