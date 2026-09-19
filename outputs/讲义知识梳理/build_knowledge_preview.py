# -*- coding: utf-8 -*-
"""从本机服务读取已入库的知识梳理，生成 A4 预览 HTML（含 KaTeX 渲染）。

预览使用与讲义工作台一致的版式（封面 + 知识梳理页），KaTeX 引用本地
static/lib/katex（相对路径 ../../static/lib/katex），可离线打开。
"""
import json
import pathlib
import urllib.request
import urllib.parse

BASE = "http://127.0.0.1:27182"
OUT_DIR = pathlib.Path(r"E:\math-question-bank-main\outputs\讲义知识梳理")
OUT_FILE = OUT_DIR / "知识梳理预览_函数的概念与性质.html"

COMPULSORY = "必修一"
CHAPTER = "3. 函数的概念与性质"
SECTIONS = [
    "3.1.1 函数的表示与函数值", "3.1.2 解析式", "3.1.3 定义域",
    "3.1.4 值域1（方法）", "3.1.5 值域2（形式）", "3.2.1 单调性",
    "3.2.2 最值", "3.2.3 奇偶性", "3.2.4 单调性与奇偶性综合",
    "3.3 幂函数", "3.4 函数的应用（一）",
]


def api_get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_notes() -> dict:
    notes = {}
    for sec in SECTIONS:
        q = urllib.parse.urlencode({
            "note_type": "knowledge",
            "compulsory": COMPULSORY,
            "chapter": CHAPTER,
            "knowledge": sec,
        })
        rows = api_get("/api/lecture/notes?" + q)
        if not rows:
            raise RuntimeError("未找到小节：" + sec)
        notes[sec] = rows[0]["content"]
    return notes


def esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


CSS = """
@page{size:A4;margin:0}
*{box-sizing:border-box}
body{margin:0;background:#e5e7eb;font-family:"CangErJinKai","仓耳今楷","KaiTi","SimSun",serif;color:#1e293b}
.sheet{width:210mm;min-height:297mm;margin:14px auto;padding:18mm 18mm 16mm;background:#fff;box-shadow:0 4px 18px rgba(0,0,0,.15);position:relative;page-break-after:always;overflow:hidden}
@media print{.sheet{margin:0;box-shadow:none;page-break-after:always}}
.cover{display:flex;flex-direction:column;align-items:center;justify-content:space-between;padding:22mm 24mm}
.cover svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}
.cover-brand{position:absolute;top:23mm;left:28mm;font-size:20px;font-weight:700;color:#1f3a5f;z-index:2}
.cover-subtitle{position:absolute;top:57mm;left:0;right:0;text-align:center;font-size:20px;color:#334155;z-index:2}
.main-title{position:absolute;top:99mm;left:0;right:0;text-align:center;font-size:46px;font-weight:700;color:#0f2440;letter-spacing:4px;z-index:2}
.cover-author{position:absolute;bottom:99mm;left:0;right:0;text-align:center;font-size:20px;font-weight:700;color:#000;z-index:2}
.cover-ver{position:absolute;bottom:20mm;left:0;right:0;text-align:center;font-size:16px;color:#64748b;z-index:2}
.chapter-info{font-size:24px;font-weight:700;color:#1f3a5f;text-align:center;margin-bottom:6mm}
.section-title{font-size:22px;font-weight:700;color:#1e3a8a;text-align:center;margin:4mm 0 6mm;position:relative}
.knowledge{font-size:14px;line-height:1.9}
.knowledge .key{font-weight:700;color:#1e3a8a}
.knowledge p{margin:2mm 0}
.knowledge table.def{width:100%;border-collapse:collapse;margin:2mm 0;font-size:13px}
.knowledge table.def th,.knowledge table.def td{border:1px solid #cbd5e1;padding:1.6mm 2mm;text-align:left;vertical-align:top}
.knowledge table.def th{background:#f1f5f9;font-weight:700}
.page-foot{position:absolute;bottom:5mm;left:0;right:0;text-align:center;font-size:12px;color:#64748b}
"""


def build(notes: dict, cover_bg: dict) -> str:
    bg = cover_bg.get("函数", cover_bg.get("default", ""))
    parts = []
    parts.append('<div class="sheet cover">' + bg +
                 '<div class="cover-brand">MT-Blaze 的数学空间</div>' +
                 '<div class="cover-subtitle">同步讲义 · 知识梳理</div>' +
                 '<div class="main-title">函数的概念与性质</div>' +
                 '<div class="cover-author">必修一 第三章</div>' +
                 '<div class="cover-ver">知识梳理预览 · 共 ' + str(len(SECTIONS)) + ' 个小节</div></div>')
    for i, sec in enumerate(SECTIONS, 1):
        content = notes[sec]
        parts.append('<div class="sheet">' +
                     '<div class="chapter-info">必修一 · 函数的概念与性质 · ' + esc(sec) + '</div>' +
                     '<div class="section-title">知识梳理</div>' +
                     '<div class="knowledge">' + content + '</div>' +
                     '<div class="page-foot">第 ' + str(i) + ' 页 / 共 ' + str(len(SECTIONS)) + ' 页</div></div>')
    body = "".join(parts)
    return ('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">' +
            '<title>知识梳理预览 · 函数的概念与性质</title>' +
            '<link rel="stylesheet" href="../../static/lib/katex/katex.min.css">' +
            '<style>' + CSS + '</style></head><body>' + body +
            '<script src="../../static/lib/katex/katex.min.js"></script>' +
            '<script src="../../static/lib/katex/contrib/auto-render.min.js"></script>' +
            '<script>document.addEventListener("DOMContentLoaded",function(){' +
            'if(window.renderMathInElement){renderMathInElement(document.body,{' +
            'delimiters:[{left:"$$",right:"$$",display:true},{left:"\\\\[",right:"\\\\]",display:true},{left:"\\\\(",right:"\\\\)",display:false}],' +
            'throwOnError:false});}' +
            '});</script></body></html>')


def main() -> None:
    notes = fetch_notes()
    print("read back notes:", len(notes))
    for sec, content in notes.items():
        print(" -", sec, "len=", len(content), "has_math=", ("\\(" in content))
    cover_bg = api_get("/api/lecture/cover-bg").get("cover_bg", {})
    html = build(notes, cover_bg)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(html, encoding="utf-8")
    print("preview written:", OUT_FILE, "bytes=", len(html.encode("utf-8")))


if __name__ == "__main__":
    main()
