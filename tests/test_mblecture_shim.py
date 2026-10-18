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
SHIM_MACROS = ("fillin", "paren", "lectsec", "lectsub", "blockhead", "lectpage", "customcurve")


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

    P1 拍板后垫片强制 Fandol 全家桶（书本体导言区用 lang=cn+chinesefont=nofont 把字体
    权让给垫片）。方正没装的机器上，PDF 字体表必须有 FandolSong、pymupdf 必须抽得到
    真汉字、日志里不许出现 CJK 的 Missing character：三条一起钉死「书本体可交付」。

    抽文本真正要钉的是 \paren 紧跟 \begin{choices} 这种常见写法：垫片里 \paren 一旦
    声明必选参数，就会把 \begin 当参数吃掉，选择题当场散架。
    """

    import pymupdf

    shutil.copyfile(SHIM, tmp_path / "mblecture.sty")
    body = (
        "\\documentclass[12pt,lang=cn,chinesefont=nofont]{elegantbook}\n"
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
    doc = pymupdf.open(str(pdf))
    text = "\n".join(page.get_text() for page in doc)
    compact = re.sub(r"\s+", "", text)
    # choices 真的成环：四个选项标号都在，且 \begin{choices} 没被 \paren 拆散
    for label in ("A", "B", "C", "D"):
        assert label in compact, compact[:400]
    assert "choices" not in compact, "选择题环境被拆开：%s" % compact[:400]
    assert r"\varnothing" not in compact, "宏没展开：%s" % compact[:400]
    assert "∅" in compact, "选项里的数学必须真的排印：%s" % compact[:400]

    # —— P1 硬前提：Fandol 中文真的进了 PDF，汉字不是豆腐块 ——
    font_names = [f[3] for page in doc for f in page.get_fonts()]
    assert any("FandolSong" in n for n in font_names), font_names
    assert any("FandolHei" in n for n in font_names), font_names  # 粗体汉字走黑体
    assert "已知集合" in text and "个元素" in text, text[:300]
    assert "\x00" not in text, "汉字又退化成 NUL 垃圾了"
    log_text = (tmp_path / "shim.log").read_text(encoding="utf-8", errors="replace")
    assert not re.search(r"Missing character: There is no [一-鿿]", log_text)


@pytest.mark.skipif(not shutil.which("xelatex"), reason="xelatex not installed")
@pytest.mark.skipif(not _kpsewhich("elegantbook.cls"), reason="ElegantBook not installed")
def test_gb_symbols_and_math_mode_cjk_keep_glyphs(tmp_path):
    r"""真编译钉死垫片补的两个洞：文本区 GB 符号、数学区汉字/顿号。

    文本区的 Ⅰ—Ⅲ △ ∵ → ⑩ 在 Termes 没字形，靠 charclass 落进 Fandol；数学模式里
    的「或」「，且当」（库内实测脏数据 `$或$`、`$，且当 $`）默认走 cmr 缺字，靠
    CJKmath=true 兜底。编译日志必须一条 Missing character 都没有。⇒/⇔ 不归垫片管
    ——Fandol 也没它们的字形，导出预检 R7 换成 \ensuremath 宏。
    """

    shutil.copyfile(SHIM, tmp_path / "mblecture.sty")
    body = (
        "\\documentclass[12pt,lang=cn,chinesefont=nofont]{elegantbook}\n"
        "\\usepackage{mblecture}\n"
        "\\begin{document}\n"
        "命题Ⅰ：增；命题Ⅱ：减；命题Ⅲ：不变。序号①—⑩，△ABC，∵平行，Ⅱ→Ⅲ。\n"
        "库内脏形态：$0<x$或$x>2$，即 $或$ 与 $，且当 $。顿号、照常。\n"
        "\\(x_1\\lt x_2\\) 作差得 \\(f(x_1)\\gt f(x_2)\\)——EB 没有 unicode-math 的宏。\n"
        "\\end{document}\n"
    )
    tex = tmp_path / "gb.tex"
    tex.write_text(body, encoding="utf-8")

    proc = subprocess.run(
        ["xelatex", "-no-shell-escape", "-interaction=nonstopmode",
         "-halt-on-error", "-file-line-error", "gb.tex"],
        cwd=str(tmp_path), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300)
    log_text = (tmp_path / "gb.log").read_text(encoding="utf-8", errors="replace")
    assert proc.returncode == 0, log_text[-2500:]

    missing = re.findall(r"Missing character:.*", log_text)
    assert not missing, "\n".join(missing) + "\n" + log_text[-1500:]



def test_paren_takes_no_argument():
    r"""\paren 在垫片里必须不带参数。

    入库归一化产出的是「…（　）\paren + 换行 + \begin{choices}」这种裸写法，\paren 一旦
    声明必选参数就会把 \begin 当参数吃掉，选择题环境当场崩。这条静态守卫比编译冒烟更直接：
    改垫片的人当场就能看到为什么不能加参数。
    """

    source = SHIM.read_text(encoding="utf-8")

    assert r"\providecommand{\paren}{" in source, r"必须是无参数写法 \providecommand{\paren}{...}"
    assert r"\providecommand{\paren}[1]" not in source, r"不许给 \paren 声明必选参数"


def test_shim_forces_fandol_and_keeps_one_column_out_of_multicols():
    r"""垫片两条静态硬底线（比编译冒烟更早给改垫片的人看）。

    ① 中文字体必须写死 Fandol：ElegantBook 自带方案要么方正（服务器没装=废纸）
       要么让 ctex 按机器猜（两台机器两种字形）。这是 P1 的拍板点。
    ② columns=1 绝不能进 multicols：multicol 见 1 列先假告警再偷偷变两栏，
       竖排选项被折散（全库压测日志里整屏 "Using `1' columns" 就是它）。
    ③ 宿主没加载 xeCJK（忘带 lang=cn）必须有 PackageWarning，不许静默缺字。
    """
    source = SHIM.read_text(encoding="utf-8")

    assert r"\setCJKmainfont{FandolSong-Regular.otf}" in source
    for font in ("FandolHei-Regular.otf", "FandolHei-Bold.otf",
                 "FandolKai-Regular.otf", "FandolFang-Regular.otf"):
        assert font in source, "Fandol 全家桶缺了 %s" % font
    assert r"\ifnum\mb@cols>1 \begin{multicols}{\mb@cols}\fi" in source
    assert r"\PackageWarning{mblecture}" in source
    # GB 符号区字符类：xeCJK 的区间分隔符是 LaTeX3 箭头 "->"，误写成 "=>" 会
    # "Missing number" 炸掉整条声明，全库静默缺字（2026-10-18 压测的最后一颗雷）。
    cc_call = source.split(r"\xeCJKDeclareCharClass", 1)[1].split("\n", 1)[0]
    assert "=>" not in cc_call and "-> " in cc_call, cc_call
    assert '"2150 -> "21FF' in cc_call and '"25A0 -> "25FF' in cc_call, cc_call
    # 数学模式汉字（库内实测形态 `$或$`）不许走 cmr 缺字，必须开 CJKmath 兜底
    assert r"\xeCJKsetup{CJKmath=true}" in source
    # \lt \gt：unicode-math 有、ElegantBook 没有，垫片必须 provide 补位
    assert r"\providecommand{\lt}" in source
    assert r"\providecommand{\gt}" in source


@pytest.mark.skipif(not shutil.which("xelatex"), reason="xelatex not installed")
@pytest.mark.skipif(not _kpsewhich("elegantbook.cls"), reason="ElegantBook not installed")
def test_mass_choices_with_bare_paren_survive(tmp_path):
    r"""复刻 94 处 Counter too large 的事故形态：40 道题、裸 \paren 紧贴 \begin{choices}，
    columns 在 1/2/4 之间轮转。

    旧实验垫片 v3 的 \paren[1] 会把紧随的 \begin 当必选参数吞掉，choices/enumerate
    当场残缺，enumi 跨题累加、每满 26 个选项溢出一回「Counter too large」——这就是
    当时全库压测 94 处报错的根因。现在的垫片必须整卷零报错，同时 columns=1 不许再
    触发 multicols 的假告警。
    """
    import pymupdf

    shutil.copyfile(SHIM, tmp_path / "mblecture.sty")
    blocks = []
    for i in range(40):
        cols = (1, 2, 4)[i % 3]
        blocks.append(
            "第%d题：设命题 $p$ 是 $q$ 的\\paren\n"
            "\\begin{choices}[columns=%d]\n"
            "\\item $a$\n\\item $b$\n\\item $c$\n\\item $d$\n\\end{choices}\n\n"
            % (i + 1, cols))
    body = (
        "\\documentclass[12pt,lang=cn,chinesefont=nofont]{elegantbook}\n"
        "\\usepackage{mblecture}\n"
        "\\begin{document}\n"
        + "".join(blocks)
        + "\\end{document}\n"
    )
    tex = tmp_path / "mass.tex"
    tex.write_text(body, encoding="utf-8")

    command = ["xelatex", "-no-shell-escape", "-interaction=nonstopmode",
               "-halt-on-error", "-file-line-error", "mass.tex"]
    for _round in range(2):
        proc = subprocess.run(command, cwd=str(tmp_path), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=300)
        assert proc.returncode == 0, (tmp_path / "mass.log").read_text(
            encoding="utf-8", errors="replace")[-2500:]

    log_text = (tmp_path / "mass.log").read_text(encoding="utf-8", errors="replace")
    for needle in ("Counter too large", "doesn't seem a good idea", "lost some text"):
        assert needle not in log_text, "事故形态复现：%s" % needle

    doc = pymupdf.open(str(tmp_path / "mass.pdf"))
    text = "".join(page.get_text() for page in doc)
    # PDF 文本层会在 CJK 与阿拉伯数字间插空白（「第40题」→「第40 题」），比对前统一押空白
    compact = "".join(text.split())
    assert "第40题" in compact, "后半卷整段丢了"
    assert any("FandolSong" in f[3] for page in doc for f in page.get_fonts())
