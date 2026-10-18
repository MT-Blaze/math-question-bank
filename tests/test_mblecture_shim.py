# -*- coding: utf-8 -*-
r"""讲义书本体垫片 ``mblecture.sty`` 的编译冒烟测试（待办 #8 P0 附带落地）。

背景：题库正文与试卷导出都按 exam-zh 方言写作（``\begin{choices}``、``\fillin``、
``\paren``），ElegantBook 不提供这些。用户拍板「排版口径并存」，于是需要一层垫片，
让**同一份 TeX 源码**在试卷体与书本体里都能编译——避免为讲义另起一条渲染链
（两条链迟早分叉，2026-10-18 的缺图事故已经演示过分叉的代价）。

这里不测「文件在不在」，直接真跑 XeLaTeX：垫片里任何一条宏包缺失、宏名冲突、
汉字被吞，都会在这里当场炸出来，而不是等用户点导出。
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SHIM = PROJECT_ROOT / "mathbank" / "resources" / "mblecture.sty"

# 垫片必须「只补不覆盖」：宿主类（ElegantBook / exam-zh）哪天自己提供了这些宏，
# 让位即可，绝不出现同名冲突或静默改写宿主排版。
SHIM_MACROS = ("fillin", "paren", "lectsec", "lectsub", "blockhead", "lectpage")


def _kpsewhich(name):
    finder = shutil.which("kpsewhich")
    if not finder:
        return None
    proc = subprocess.run([finder, name], capture_output=True, text=True, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


def test_shim_file_exists():
    assert SHIM.is_file(), "讲义垫片必须随仓库发布，否则书本体导出在别的机器上必挂"


@pytest.mark.parametrize("macro", SHIM_MACROS)
def test_shim_only_provides_macros(macro):
    source = SHIM.read_text(encoding="utf-8")

    assert r"\providecommand{\%s}" % macro in source, "%s 必须用 \\providecommand" % macro
    forbidden = (r"\newcommand{\%s}" % macro, r"\renewcommand{\%s}" % macro,
                 r"\def\%s{" % macro)
    for form in forbidden:
        assert form not in source, "垫片不该用 %s 覆盖宿主定义" % form


@pytest.mark.skipif(not shutil.which("xelatex"), reason="xelatex not installed")
@pytest.mark.skipif(not _kpsewhich("elegantbook.cls"), reason="ElegantBook not installed")
def test_shim_compiles_inside_elegantbook(tmp_path):
    r"""最小讲义片段在 ElegantBook + 垫片下真出 PDF，选项环境确实成环。

    这里刻意不断言中文正文：ElegantBook 默认中文字体是方正系列（FZShuSong-Z01 等），
    本机 TeX Live 没装。实测编译 rc=0，但 PDF 字体表里一个 CJK 字体都没有，汉字全是
    豆腐块（pymupdf 抽出来是一串 NUL）。把这台机器的字体状态焊进测试没有意义，但它是
    书本体导出（P1）的硬前提：不先定中文字体方案，导出的就是一张废纸。

    抽文本真正要钉的是 \paren 紧跟 \begin{choices} 这种常见写法：垫片里 \paren 一旦
    声明必选参数，就会把 \begin 当参数吃掉，选择题当场散架。
    """

    import pymupdf

    shutil.copyfile(SHIM, tmp_path / "mblecture.sty")
    body = (
        "\\documentclass[12pt,doccolor=red]{elegantbook}\n"
        "\\usepackage{mblecture}\n"
        "\\begin{document}\n"
        "\\blockhead{考点自测}\n"
        "已知集合 $A=\\{x\\mid x<1\\}$ ，则 $A\\cup B$ 中有\\fillin 个元素。\n"
        "设命题 $p$ 是 $q$ 的\\paren\n"
        "\\begin{choices}[columns=2]\n"
        "\\item $(-\\infty,1)$\n"
        "\\item $[1,+\\infty)$\n"
        "\\item $\\varnothing$\n"
        "\\item $\\mathbb{R}$\n"
        "\\end{choices}\n"
        "\\end{document}\n"
    )
    tex = tmp_path / "shim.tex"
    tex.write_text(body, encoding="utf-8")

    command = ["xelatex", "-no-shell-escape", "-interaction=nonstopmode",
               "-halt-on-error", "-file-line-error", "shim.tex"]
    for _round in range(2):
        proc = subprocess.run(command, cwd=str(tmp_path), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=300)
        assert proc.returncode == 0, (tmp_path / "shim.log").read_text(
            encoding="utf-8", errors="replace")[-2500:]

    pdf = tmp_path / "shim.pdf"
    assert pdf.is_file() and pdf.stat().st_size > 0
    text = "\n".join(page.get_text() for page in pymupdf.open(str(pdf)))
    compact = re.sub(r"\s+", "", text)
    # choices 真的成环：四个选项标号都在，且 \begin{choices} 没被 \paren 拆散
    for label in ("A", "B", "C", "D"):
        assert label in compact, compact[:400]
    assert "choices" not in compact, "选择题环境被拆开：%s" % compact[:400]
    assert r"\varnothing" not in compact, "宏没展开：%s" % compact[:400]
    assert "∅" in compact, "选项里的数学必须真的排印：%s" % compact[:400]


def test_paren_takes_no_argument():
    r"""\paren 在垫片里必须不带参数。

    入库归一化产出的是「…（　）\paren + 换行 + \begin{choices}」这种裸写法，\paren 一旦
    声明必选参数就会把 \begin 当参数吃掉，选择题环境当场崩。这条静态守卫比编译冒烟更直接：
    改垫片的人当场就能看到为什么不能加参数。
    """

    source = SHIM.read_text(encoding="utf-8")

    assert r"\providecommand{\paren}{" in source, r"必须是无参数写法 \providecommand{\paren}{...}"
    assert r"\providecommand{\paren}[1]" not in source, r"不许给 \paren 声明必选参数"
