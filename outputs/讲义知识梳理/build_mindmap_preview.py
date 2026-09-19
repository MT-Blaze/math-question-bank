# -*- coding: utf-8 -*-
"""由应用内真实渲染结果组装「知识梳理（思维导图）」整章离线预览 HTML。

输入：
  extracted_mindmap.json      —— 11 小节在应用预览 iframe 中渲染出的 .sheet(mind-wrap) 片段
  extracted_iframe_style.css  —— 应用预览 iframe 内的样式（LECTURE_CSS）
输出：
  知识梳理预览_函数的概念与性质_思维导图.html （KaTeX 用本地 /static/lib 渲染结果已内联，离线可看）
"""
import json
import pathlib

OUT = pathlib.Path(r"E:\math-question-bank-main\outputs\讲义知识梳理")
data = json.loads((OUT / "extracted_mindmap.json").read_text(encoding="utf-8"))
style = (OUT / "extracted_iframe_style.css").read_text(encoding="utf-8")

KATEX_CSS = "file:///E:/math-question-bank-main/static/lib/katex/katex.min.css"

section_titles = [
    "3.1.1 函数的表示与函数值", "3.1.2 解析式", "3.1.3 定义域",
    "3.1.4 值域1（方法）", "3.1.5 值域2（形式）", "3.2.1 单调性",
    "3.2.2 最值", "3.2.3 奇偶性", "3.2.4 单调性与奇偶性综合",
    "3.3 幂函数", "3.4 函数的应用（一）",
]

body_parts = []
for st in section_titles:
    item = data.get(st) or {}
    html = item.get("html", "")
    if not html:
        body_parts.append(f'<div class="missing">未渲染：{st}</div>')
        continue
    body_parts.append(
        f'<div class="sec-divider">必修一 · 3. 函数的概念与性质 · {st}</div>'
    )
    body_parts.append(html)

wrapper_css = """
  body{margin:0;background:#e2e8f0;padding:6mm 0;}
  .sec-divider{width:190mm;margin:0 auto 5mm;background:#0f172a;color:#fff;
    font-size:15px;font-weight:700;padding:3mm 6mm;border-radius:2mm;
    box-shadow:0 2px 8px rgba(15,23,42,.3);}
  .sheet{margin:0 auto 8mm;box-shadow:0 4px 16px rgba(0,0,0,.18);}
  .missing{width:190mm;margin:0 auto;background:#fee2e2;color:#991b1b;padding:6mm;border-radius:2mm;}
"""

html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>知识梳理预览（思维导图）· 函数的概念与性质</title>
<link rel="stylesheet" href="{KATEX_CSS}">
<style>
{style}
{wrapper_css}
</style>
</head>
<body>
{''.join(body_parts)}
</body>
</html>"""

target = OUT / "知识梳理预览_函数的概念与性质_思维导图.html"
target.write_text(html_doc, encoding="utf-8")
print("written:", target, "bytes:", len(html_doc.encode("utf-8")))
