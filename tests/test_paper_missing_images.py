"""组卷导出「缺图红字占位」的前端回归锁。

背景（2026-10-18 导出崩溃事故的第二层）：XeLaTeX 一撞上不存在就崩整卷，
所以后端在准备阶段就把死链改写成卷面红字占位（``paper_helper.patch_missing_latex_images``），
并通过 ``X-Paper-Missing-Images`` / ``X-Paper-Missing-Image-Files`` 两个响应头
把「缺了几张、缺哪些图」交给前端。

这条链路最容易烂在**两端各说各话**：后端加了头、前端没读（用户只看见 PDF 里
凭空多出几行红字）；或前端读了一个后端已经改名的头。所以这里同时锁三件事：
① 助手函数在 node 里真跑，行为与文案符合预期；② 三个导出入口都真的调它；
③ 前后端的头名逐字一致。与 ``tests/test_choices_grid_columns.py`` 同一套路子。
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PAPER_JS = PROJECT_ROOT / "static" / "js" / "paper.js"
ROUTES_PAPER = PROJECT_ROOT / "mathbank" / "routes_paper.py"

COUNT_HEADER = "X-Paper-Missing-Images"
FILES_HEADER = "X-Paper-Missing-Image-Files"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_function(source: str, signature: str) -> str:
    """按行缩进抠出整个函数体（含 ``};`` 收尾的赋值式函数）。

    与 ``test_choices_grid_columns.py`` 同理由：不走花括号配平，源码注释里
    有成对引号会把配平法带偏。
    """

    lines = source.splitlines()
    for idx, line in enumerate(lines):
        if signature in line:
            indent = len(line) - len(line.lstrip())
            end = idx + 1
            while end < len(lines):
                stripped = lines[end].strip()
                if stripped in ("}", "};") and len(lines[end]) - len(lines[end].lstrip()) == indent:
                    break
                end += 1
            assert end < len(lines), "未找到闭合行：%s" % signature
            return "\n".join(lines[idx : end + 1])
    raise AssertionError("未找到函数：%s" % signature)


def _run_node_script(script: str) -> str:
    """把脚本落盘再交给 node（命令行有长度上限，且中文必须走文件而非 argv）。"""

    node = shutil.which("node")
    assert node, "Node.js is required for the missing-image frontend regression"
    handle = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8")
    try:
        with handle:
            handle.write(script)
        result = subprocess.run(
            [node, handle.name],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout
    finally:
        try:
            handle.close()
            import os

            os.unlink(handle.name)
        except OSError:
            pass


_HELPER_SRC = _extract_function(_read(PAPER_JS), "function notifyMissingPaperImages(response)")

# 桩只还原一件事：fetch 响应的 headers.get（不存在返回 null），以及 window.showToast 的收口。
_HARNESS = r"""
%s

var toasts = [];
global.window = { showToast: function (msg, type) { toasts.push({ msg: msg, type: type }); } };
function resp(headers) {
    return {
        headers: {
            get: function (name) {
                return Object.prototype.hasOwnProperty.call(headers, name) ? headers[name] : null;
            },
        },
    };
}
function take(label, value) {
    var out = { label: label, count: value, toasts: toasts.slice() };
    toasts.length = 0;
    return out;
}
var cases = [];
cases.push(take("无响应头", notifyMissingPaperImages(resp({}))));
cases.push(take("计数为0", notifyMissingPaperImages(resp({ "%s": "0" }))));
cases.push(take("两张jpg", notifyMissingPaperImages(resp({
    "%s": "2",
    "%s": encodeURIComponent("mineru_1790251898_1.jpg,mineru_1790251898_2.jpg"),
}))));
cases.push(take("中文文件名", notifyMissingPaperImages(resp({
    "%s": "1",
    "%s": encodeURIComponent("函数图象_缺失.png"),
}))));
cases.push(take("五张只列前三", notifyMissingPaperImages(resp({
    "%s": "5",
    "%s": encodeURIComponent(["a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg"].join(",")),
}))));
cases.push(take("有计数无文件名", notifyMissingPaperImages(resp({ "%s": "3" }))));
cases.push(take("脏值", notifyMissingPaperImages(resp({ "%s": "abc", "%s": "x.jpg" }))));
cases.push(take("负数", notifyMissingPaperImages(resp({ "%s": "-2" }))));
cases.push(take("没有headers", notifyMissingPaperImages({})));
cases.push(take("空响应", notifyMissingPaperImages(null)));
console.log(JSON.stringify(cases));
""" % (
    _HELPER_SRC,
    COUNT_HEADER,
    COUNT_HEADER, FILES_HEADER,
    COUNT_HEADER, FILES_HEADER,
    COUNT_HEADER, FILES_HEADER,
    COUNT_HEADER,
    COUNT_HEADER, FILES_HEADER,
    COUNT_HEADER,
)


def _cases() -> list:
    stdout = _run_node_script(_HARNESS)
    payload = stdout.strip().splitlines()[-1]
    return json.loads(payload)


def test_missing_image_headers_become_warning_toast():
    """响应头 → Toast：文案要说清「几张、哪几张」，且不吵不闹不该出现的场合。"""

    cases = {item["label"]: item for item in _cases()}

    # 缺图为零/无头/脏值/负数/传错对象：一律静默，绝不抛异常打断导出
    for label in ("无响应头", "计数为0", "脏值", "负数", "没有headers", "空响应"):
        item = cases[label]
        assert item["count"] == 0, label
        assert item["toasts"] == [], "%s 不该弹提示：%r" % (label, item["toasts"])

    two = cases["两张jpg"]
    assert two["count"] == 2
    assert len(two["toasts"]) == 1
    toast = two["toasts"][0]
    assert toast["type"] == "warning", "缺图必须按警告级提示，不能混进成功提示里"
    assert "有 2 张图片缺失" in toast["msg"], toast["msg"]
    assert "红字占位" in toast["msg"], toast["msg"]
    assert "mineru_1790251898_1.jpg" in toast["msg"] and "mineru_1790251898_2.jpg" in toast["msg"]

    chinese = cases["中文文件名"]["toasts"][0]["msg"]
    assert "函数图象_缺失.png" in chinese, "后端按百分号编码回传，前端必须解回原文：%s" % chinese
    assert "%2C" not in chinese and "%_" not in chinese, chinese

    five = cases["五张只列前三"]["toasts"][0]["msg"]
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        assert name in five, five
    assert "d.jpg" not in five and "e.jpg" not in five, "文件名只列前三，别把 Toast 撑爆：%s" % five
    assert "等 5 张" in five, five

    bare = cases["有计数无文件名"]["toasts"][0]["msg"]
    assert "有 3 张图片缺失" in bare and "：" not in bare, bare


def test_every_paper_export_entry_reports_missing_images():
    """三个导出入口都要消费响应头：漏一个就等于那个入口继续「静默甩锅」。"""

    source = _read(PAPER_JS)
    entries = {
        "组卷导出 PDF": "window.exportPaperPdf = async function",
        "LaTeX 打包导出": "window.exportPaperBundle = async function",
        "历史试卷快速导出": "window.quickExportPaperPdf = async function",
    }
    for label, signature in entries.items():
        body = _extract_function(source, signature)
        assert "notifyMissingPaperImages(" in body, "%s 没有消费缺图响应头" % label


def test_missing_image_header_names_match_the_backend():
    """头名逐字对齐：后端改名而前端没跟上时，这条必须当场响。"""

    routes = _read(ROUTES_PAPER)
    for header in (COUNT_HEADER, FILES_HEADER):
        assert header in routes, "后端不再发 %s" % header
        assert header in _HELPER_SRC, "前端还在读 %s" % header
    assert "safe=\"\"" in routes, "文件名头必须百分号编码，否则非 ASCII 文件名会被头值截断"
