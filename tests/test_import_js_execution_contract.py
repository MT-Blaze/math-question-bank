# -*- coding: utf-8 -*-
"""钉住批1面板的浏览器可用性前提：import.js 必须能整体执行到底。

实测教训（2026-10-03）：并行会话删掉了 openParsedDuplicateReview 的定义却留下导出行，
ReferenceError 让 import.js 尾部全部失效——面板 IIFE 在其后，window.openDisplayOrderPanel
静默 undefined、按钮死透；而 node --check（只查语法）与 vm 桩测试（只跑面板段）都测不出。
本测试扫描全文件：每条 window.X = X; 裸导出必须有可见定义。
"""
import pathlib
import re

IMPORT_JS = pathlib.Path(__file__).resolve().parent.parent / "static" / "js" / "import.js"


def test_every_bare_window_export_has_a_definition():
    src = IMPORT_JS.read_text(encoding="utf-8")
    missing = []
    for m in re.finditer(r"window\.([A-Za-z_$][\w$]*)\s*=\s*\1;", src):
        name = m.group(1)
        defined = re.search(
            r"(?:(?:async\s+)?function\s+" + re.escape(name) + r"\s*\("
            r"|(?:const|let|var)\s+" + re.escape(name) + r"\s*[=:])",
            src,
        )
        if not defined:
            line = src.count("\n", 0, m.start()) + 1
            missing.append((line, name))
    assert not missing, (
        "import.js 存在无定义的裸导出（运行到该行会 ReferenceError，"
        f"导致其后所有模块级代码静默失效）: {missing}"
    )
