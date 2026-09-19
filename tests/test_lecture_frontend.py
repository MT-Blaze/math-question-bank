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


def test_cover_title_save_race_guard():
    """标题 blur 保存未完成时点「刷新预览」，必须等 PUT 落库后再重建 srcdoc，
    否则旧标题会复活（用户感知=改了保存不上）。"""
    src = _read(LECTURE_JS)
    assert "ST._coverSave =" in src  # 保存句柄登记
    # 刷新预览入口串接未完成的保存：先等 PUT 完成再拉 preview
    assert "(ST._coverSave || Promise.resolve())" in src


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
    assert "function buildTopicNoMap(" in src
    assert "topicNoById" in src
    assert "'考向' + (idx + 1)" not in src
