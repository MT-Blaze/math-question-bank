import json
import os
import re
import shutil
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = PROJECT_ROOT / "static"
STATIC_JS_DIR = STATIC_DIR / "js"
INDEX_PATH = STATIC_DIR / "index.html"
CSS_PATH = STATIC_DIR / "css" / "app.css"
JS_FILES = tuple(
    STATIC_JS_DIR / name
    for name in (
        "tailwind_gate.js",
        "api.js",
        "editor.js",
        "ocr.js",
        "import.js",
        "entry_options.js",
        "paper.js",
        "virtual_list.js",
    )
)


def _run_node_script(script: str) -> subprocess.CompletedProcess:
    """Run a generated Node script through a temporary file.

    Windows caps the command line at roughly 32k characters, so handing a large
    extracted JS payload to ``node -e`` fails with WinError 206 (filename or
    extension too long).  Writing the script to a temp file removes that limit
    without weakening the regression in any way.

    ``encoding="utf-8"`` is not cosmetic: node prints UTF-8, and on a CP936
    console the reader thread dies with UnicodeDecodeError, leaving
    ``result.stdout`` as ``None`` — the assertion then reads like a broken test
    instead of a broken frontend.
    """

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".js", delete=False, encoding="utf-8"
    )
    try:
        with handle:
            handle.write(script)
        return subprocess.run(
            [node, handle.name],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _escape_html_helper_source() -> str:
    """Shared escapeHtml helper that api.js provides before editor.js loads."""
    api_source = _read(STATIC_JS_DIR / "api.js")
    start = api_source.index("function escapeHtml(str)")
    end = api_source.index("window.escapeHtml = escapeHtml;", start)
    return api_source[start:end]


_ESCAPE_TEXT_STUB = (
    "    escapeText(value) { return String(value)"
    ".replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')"
    ".replace(/\"/g, '&quot;').replace(/'/g, '&#039;'); },\n"
)


class _ElementIndex(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.by_id: dict[str, dict[str, str | None]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        element_id = attributes.get("id")
        if element_id:
            self.by_id[element_id] = attributes


def _index_elements() -> dict[str, dict[str, str | None]]:
    parser = _ElementIndex()
    parser.feed(_read(INDEX_PATH))
    return parser.by_id


def test_mobile_viewport_and_five_level_script_order_are_preserved():
    index_source = _read(INDEX_PATH)
    expected_order = (
        "/static/js/tailwind_gate.js",
        "/static/js/api.js",
        "/static/js/editor.js",
        "/static/js/ocr.js",
        "/static/js/import.js",
        "/static/js/virtual_list.js",
        "/static/js/paper.js",
    )

    assert 'name="viewport"' in index_source
    assert "width=device-width, initial-scale=1.0" in index_source
    positions = [index_source.index(script_path) for script_path in expected_order]
    assert positions == sorted(positions)


def test_editor_preview_converts_exam_zh_paren_after_protecting_math_blocks():
    editor_source = _read(STATIC_JS_DIR / "editor.js")
    css_source = _read(CSS_PATH)

    helper_start = editor_source.index("function transformExamZhParenForPreview(text)")
    helper_end = editor_source.index("function preprocessFormulaForKaTeX(text)", helper_start)
    helper_source = editor_source[helper_start:helper_end]

    assert r"/\\paren\b/g" in helper_source
    assert 'class="exam-zh-paren-preview"' in helper_source
    assert 'aria-label="选择题作答括号"' in helper_source

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    script = helper_source + r"""
const rendered = transformExamZhParenForPreview(String.raw`题干 \paren`);
if (rendered.includes(String.raw`\paren`)) {
  throw new Error(`paren macro leaked into preview: ${rendered}`);
}
// 括号内必须是两个**全角空格** U+3000，与导出侧 _render_choice_paren 的「（　　）」同宽
if (!rendered.includes('class="exam-zh-paren-preview"') || !rendered.includes('（\u3000\u3000）')) {
  throw new Error(`paren preview markup missing: ${rendered}`);
}
if (rendered.includes('&nbsp;')) {
  throw new Error(`paren preview fell back to half-width &nbsp;: ${rendered}`);
}
const similarlyNamed = transformExamZhParenForPreview(String.raw`\parent`);
if (similarlyNamed !== String.raw`\parent`) {
  throw new Error(`similarly named command was changed: ${similarlyNamed}`);
}
"""
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr

    protect_position = editor_source.index("let tempText = clean;", helper_end)
    strip_position = editor_source.index(
        "tempText = tempText.replace(/<[^>]*>/g, '');", protect_position
    )
    transform_position = editor_source.index(
        "tempText = transformExamZhParenForPreview(tempText);", strip_position
    )
    assert protect_position < strip_position < transform_position

    assert ".exam-zh-paren-preview" in css_source
    # 作答括号必须**紧跟题干末尾**（导出侧 paren/type=none，见 paper_helper）。
    # 曾经的 float:right 是照抄 exam-zh 默认 hfill，两边口径已统一为紧跟。
    paren_rule = re.search(r"\.exam-zh-paren-preview\s*\{[^}]*\}", css_source, re.DOTALL)
    assert paren_rule, "缺少 .exam-zh-paren-preview 规则"
    assert "float:" not in paren_rule.group(0), "作答括号不应再浮动到行尾右侧"
    assert re.search(r"display:\s*inline", paren_rule.group(0)), "作答括号应内联紧跟题干"
    assert re.search(r"\.choices-grid\s*\{[^}]*clear:\s*both;", css_source, re.DOTALL)


def test_editor_preview_restores_adjacent_math_without_splitting_fillin_lines():
    editor_source = _read(STATIC_JS_DIR / "editor.js")
    helper_start = editor_source.index("function transformFillinMacro(clean)")
    helper_marker = "window.preprocessFormulaForKaTeX = preprocessFormulaForKaTeX;"
    helper_end = editor_source.index(helper_marker, helper_start) + len(helper_marker)
    helper_source = editor_source[helper_start:helper_end]

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    script = r"""
global.window = {
  MathBankSafe: {
    safeImageUrl(value) { return value; },
    escapeAttribute(value) { return value; },
""" + _ESCAPE_TEXT_STUB + r"""  },
};
""" + _escape_html_helper_source() + helper_source + r"""
const fillin = String.raw`\fillin`;
const renderedFillin = String.raw`$\displaystyle \underline{\hspace{1.5cm}}$`;
for (let count = 1; count <= 6; count += 1) {
  const source = `11${fillin.repeat(count)}`;
  const rendered = window.preprocessFormulaForKaTeX(source);
  const expected = `11${renderedFillin.repeat(count)}`;
  if (rendered !== expected) {
    throw new Error(`continuous fillin changed at count ${count}: ${rendered}`);
  }
  if (rendered.includes('@@MATH_PLACEHOLDER_')) {
    throw new Error(`math placeholder leaked at count ${count}: ${rendered}`);
  }
}

for (const [source, expected] of [
  [String.raw`$a$$b$$c$`, String.raw`$\displaystyle a$$\displaystyle b$$\displaystyle c$`],
  [String.raw`$a$$b$$c$$d$$e$`, String.raw`$\displaystyle a$$\displaystyle b$$\displaystyle c$$\displaystyle d$$\displaystyle e$`],
  [String.raw`prefix$$a+b$$suffix`, String.raw`prefix$$a+b$$suffix`],
  [String.raw`text $a$ and $b$`, String.raw`text $\displaystyle a$ and $\displaystyle b$`],
  [String.raw`\(a \lt \frac{1}{2}\)`, String.raw`\(\displaystyle a \lt \frac{1}{2}\)`],
  [String.raw`$\displaystyle \frac{1}{2}$`, String.raw`$\displaystyle \frac{1}{2}$`],
]) {
  const rendered = window.preprocessFormulaForKaTeX(source);
  if (rendered !== expected) {
    throw new Error(`adjacent math changed: ${source} -> ${rendered}`);
  }
  if (window.preprocessFormulaForKaTeX(rendered) !== rendered) {
    throw new Error(`inline displaystyle wrap is not idempotent: ${rendered}`);
  }
  if (rendered.includes('@@MATH_PLACEHOLDER_')) {
    throw new Error(`math placeholder leaked: ${rendered}`);
  }
}

// 裸 < 在 \(..\) 里走的是「占位符恢复时 escapeHtml」这条路（$..$ 那条早在保护前就换成 \lt ），
// 所以第二趟会把 & 再转义一次、逐字并不幂等；这里只钉「照样吃到 \displaystyle」，
// 浏览器把 innerHTML 解回 textContent 后 KaTeX 拿到的仍是 `x < \frac{1}{2}`。
const rawLess = window.preprocessFormulaForKaTeX(String.raw`\(x < \frac{1}{2}\)`);
if (!rawLess.startsWith(String.raw`\(\displaystyle x`)) {
  throw new Error(`裸 < 的行内公式没拿到 displaystyle: ${rawLess}`);
}
if (rawLess.includes('@@MATH_PLACEHOLDER_')) {
  throw new Error(`math placeholder leaked: ${rawLess}`);
}
"""
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr


def test_editor_preview_glues_han_to_inline_math_only():
    r"""汉字↔行内公式的间距归一（待办 #15 · D 档，维持半角空格口径）。

    口径必须跟 XeLaTeX 对齐：本机实测 ctexart 下 `汉$x$汉` 与 `汉 $x$ 汉` **等宽**（xeCJK
    自己补 ecglue 并吸收源里的多余空格），而 `汉，$x$` 的标点旁不加缝。前端此前完全不补，
    于是库里（题干+答案+解析三字段口径）左贴死 87 处/30 题、右贴死 52 处/22 题在预览里把公式
    怼在汉字上；只算题干也有 60 处/14 题与 35 处/10 题。浏览器实测这 15 题的 95 处贴死边界里，
    正文 46 处补缝（缝隙 0.00px → 4.73px），落在 \begin{choices} 里的 49 处整体豁免：选项宽度
    直接决定 .choices-grid 的列数，补缝会让 4 列在 412~588px 容器里凭空掉一档，故一律不动。
    钉住八条：① 贴死补一个半角空格 ② 多个空白折成一个 ③ 全角标点/拉丁与数字一律不动
    ④ 块级（$$ 与 \[）不补行内缝 ⑤ \(..\) 行内形态同样补缝 ⑥ 归一必须幂等且占位符不外漏
    ⑦ \begin{choices} 选项段整体豁免（选项宽度直接决定 .choices-grid 的列数，补缝会让窄栏掉档）
    ⑧ 行首带选项标号（A. / Ｂ．）的行整行豁免，裸标号独占一行时连其正文行一起豁免——判据逐字
    复用 import.js 的解析卡分组器，否则「解析卡补了缝、入库进编辑器又不补」会闪动。
    """
    editor_source = _read(STATIC_JS_DIR / "editor.js")
    helper_start = editor_source.index("function transformFillinMacro(clean)")
    helper_marker = "window.preprocessFormulaForKaTeX = preprocessFormulaForKaTeX;"
    helper_end = editor_source.index(helper_marker, helper_start) + len(helper_marker)
    helper_source = editor_source[helper_start:helper_end]

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    script = r"""
global.window = {
  MathBankSafe: {
    safeImageUrl(value) { return value; },
    escapeAttribute(value) { return value; },
""" + _ESCAPE_TEXT_STUB + r"""  },
};
""" + _escape_html_helper_source() + helper_source + r"""
const cases = [
  [String.raw`已知$x^2$成立`, String.raw`已知 $\displaystyle x^2$ 成立`],
  [String.raw`则  $a^2$  的取值`, String.raw`则 $\displaystyle a^2$ 的取值`],
  [String.raw`计算$x^2$，得$y^2$。`, String.raw`计算 $\displaystyle x^2$，得 $\displaystyle y^2$。`],
  [String.raw`（$a$）与（$b$）`, String.raw`（$\displaystyle a$）与（$\displaystyle b$）`],
  [String.raw`A.$x^2$B`, String.raw`A.$\displaystyle x^2$B`],
  [String.raw`已知 x+y=1$x^2$成立`, String.raw`已知 x+y=1$\displaystyle x^2$ 成立`],
  [String.raw`汉$$x^2$$汉`, String.raw`汉$$x^2$$汉`],
  [String.raw`汉\[x^2\]汉`, String.raw`汉\[x^2\]汉`],
  [String.raw`设\(x^2\)成立`, String.raw`设 \(\displaystyle x^2\) 成立`],
];
for (const [source, expected] of cases) {
  const rendered = window.preprocessFormulaForKaTeX(source);
  if (rendered !== expected) {
    throw new Error('间距归一不符: ' + source + ' -> ' + rendered + '（期望 ' + expected + '）');
  }
  const again = window.preprocessFormulaForKaTeX(rendered);
  if (again !== rendered) {
    throw new Error('间距归一不幂等: ' + rendered + ' -> ' + again);
  }
  if (rendered.indexOf('@@MATH_PLACEHOLDER_') >= 0) {
    throw new Error('数学占位符外漏: ' + rendered);
  }
}

// ⑦ 选项段整体豁免：\begin{choices}…\end{choices} 里一个空格都不许补，题干以及两个选项块
// **之间/之后**的正文照补。这里断言「包含」而不是整体相等：换境会把选项段展开成
// .choices-grid 的 HTML，逐字比对等于把网格模板钉死在测试里；也不做幂等复检——第二遍
// 输入里 \begin{choices} 已变成网格 HTML，环境边界不复存在（预览永远从源码起，不喂 HTML）。
const choicesCases = [
  [String.raw`已知$x^2$成立\begin{choices}\item $-3$或$5$\item $3$或$-3$\end{choices}`,
   [String.raw`已知 $\displaystyle x^2$ 成立`, String.raw`$\displaystyle -3$或$\displaystyle 5$`, String.raw`$\displaystyle 3$或$\displaystyle -3$`], 1],
  [String.raw`若$x^2$成立\begin{choices}\item 选项$a$或$b$\end{choices}且$y^2$成立`
   + String.raw`\begin{choices}\item $c$或$d$\end{choices}解析：由$x^2$得$y^2$。`,
   [String.raw`若 $\displaystyle x^2$ 成立`, String.raw`且 $\displaystyle y^2$ 成立`, String.raw`选项$\displaystyle a$或$\displaystyle b$`,
    String.raw`$\displaystyle c$或$\displaystyle d$`, String.raw`解析：由 $\displaystyle x^2$ 得 $\displaystyle y^2$。`], 2],
];
for (const [source, expectedList, gridCount] of choicesCases) {
  const rendered = window.preprocessFormulaForKaTeX(source);
  for (const expected of expectedList) {
    if (!rendered.includes(expected)) {
      throw new Error('选项豁免不符: ' + source + ' -> ' + rendered + '（缺少 ' + expected + '）');
    }
  }
  if (/或\s|\s或/.test(rendered)) {
    throw new Error('选项段被补了缝: ' + rendered);
  }
  const grids = (rendered.match(/choices-grid/g) || []).length;
  if (grids !== gridCount) {
    throw new Error('选项网格数量不符: ' + grids + ' != ' + gridCount + ' -> ' + rendered);
  }
}

// ⑧ 行首选项标号整行豁免。判据来自 import.js 的解析卡分组器（行首标号 + 裸标号两处，
// 含 [A-D] 边界），所以 E. 开头不在判据内、照补——这一条同时钉住「两边永远同口径」。
const labelCases = [
  ['A.已知$x$成立', [String.raw`A.已知$\displaystyle x$成立`], [String.raw`A.已知 $\displaystyle x$ 成立`]],
  ['已知$x^2$成立\nA.已知$x$成立\nＢ．由$y$得$z$\n解析：由$x$得$y$。',
   [String.raw`已知 $\displaystyle x^2$ 成立`, String.raw`A.已知$\displaystyle x$成立`,
    String.raw`Ｂ．由$\displaystyle y$得$\displaystyle z$`, String.raw`解析：由 $\displaystyle x$ 得 $\displaystyle y$。`],
   [String.raw`A.已知 $\displaystyle x$ 成立`, String.raw`Ｂ．由 $\displaystyle y$ 得 $\displaystyle z$`]],
  ['已知$x$成立\nA.\n$x$或$y$\nB.\n$z$或$w$',
   [String.raw`已知 $\displaystyle x$ 成立`, String.raw`$\displaystyle x$或$\displaystyle y$`, String.raw`$\displaystyle z$或$\displaystyle w$`],
   [String.raw`$\displaystyle x$ 或 $\displaystyle y$`, String.raw`$\displaystyle z$ 或 $\displaystyle w$`]],
  ['已知$x^2$成立\r\nA.\r\n$x$或$y$', [String.raw`已知 $\displaystyle x^2$ 成立`, String.raw`$\displaystyle x$或$\displaystyle y$`],
   [String.raw`$\displaystyle x$ 或 $\displaystyle y$`]],
  ['E.$x$或$y$', [String.raw`E.$\displaystyle x$ 或 $\displaystyle y$`], []],
];
for (const [source, mustInclude, mustExclude] of labelCases) {
  const rendered = window.preprocessFormulaForKaTeX(source);
  for (const expected of mustInclude) {
    if (!rendered.includes(expected)) {
      throw new Error('选项行豁免不符: ' + source + ' -> ' + rendered + '（缺少 ' + expected + '）');
    }
  }
  for (const forbidden of mustExclude) {
    if (rendered.includes(forbidden)) {
      throw new Error('选项行被补了缝: ' + source + ' -> ' + rendered + '（出现了 ' + forbidden + '）');
    }
  }
}"""
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr


def test_editor_preview_indents_circled_subitems_one_level_deeper():
    r"""圈号小小问（①②③…）在预览里必须比一级小问 (1)/(2) 再缩进一级。

    回归来源：AI 的实际形态是**裸的** `①求证：…`（圈号后直接跟汉字），而旧正则把圈号
    写在 `[(（]?(?:…)[)）\.]` 这一支里、要求后面还得跟 `)`/`）`/`.` —— 于是永远匹配不上，
    预览既不换行也不缩进，①② 和 (1) 齐平、看不出层级。
    """
    editor_source = _read(STATIC_JS_DIR / "editor.js")
    css_source = _read(CSS_PATH)

    helper_start = editor_source.index("function transformFillinMacro(clean)")
    helper_marker = "window.preprocessFormulaForKaTeX = preprocessFormulaForKaTeX;"
    helper_end = editor_source.index(helper_marker, helper_start) + len(helper_marker)
    helper_source = editor_source[helper_start:helper_end]

    script = r"""
global.window = {
  MathBankSafe: {
    safeImageUrl(value) { return value; },
    escapeAttribute(value) { return value; },
""" + _ESCAPE_TEXT_STUB + r"""  },
};
""" + _escape_html_helper_source() + helper_source + r"""
const stem = String.raw`已知二次函数 $f(x)=ax^{2}+bx+c$。

(1) 若 $a,b,c$ 满足 $a > b > c$，一次函数 $g(x)=-bx$。
① 求证：两函数的图象交于不同两点 $A$，$B$。
② 求线段 $AB$ 在 $x$ 轴上的射影 $A_{1}B_{1}$ 的长的取值范围。`;
const rendered = window.preprocessFormulaForKaTeX(stem);

const count = (rendered.match(/class="preview-subitem"/g) || []).length;
if (count !== 2) {
  throw new Error('圈号段落应各自缩进成块（期望 2），实际 ' + count + '：' + rendered);
}
for (const marker of ['①', '②']) {
  const at = rendered.indexOf(marker);
  if (at < 0) throw new Error('圈号 ' + marker + ' 丢失：' + rendered);
  const head = rendered.slice(Math.max(0, at - 40), at);
  if (head.indexOf('<div class="preview-subitem">') < 0) {
    throw new Error('圈号 ' + marker + ' 没有套上缩进块：' + head);
  }
}
const oneAt = rendered.indexOf('(1)');
if (oneAt < 0) throw new Error('一级小问 (1) 丢失：' + rendered);
const oneHead = rendered.slice(Math.max(0, oneAt - 40), oneAt);
if (oneHead.indexOf('preview-subitem') >= 0) {
  throw new Error('一级小问 (1) 被误缩进：' + oneHead);
}

// 圈号段前导不再带 <br> 或 <br><br>（修复第6题预览段间距过大：div 块级自带换行，
// 再叠 <br> 会多出一行间距）。段间距完全交给 CSS 的 .preview-subitem margin-top 控制。
if (rendered.indexOf('<br><div class="preview-subitem">') >= 0) {
  throw new Error('圈号段前导仍残留单个 <br>：' + rendered);
}
if (rendered.indexOf('<br><br><div class="preview-subitem">') >= 0) {
  throw new Error('圈号段前导仍残留 <br><br>：' + rendered);
}
"""
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr

    assert 'class="preview-subitem"' in editor_source
    assert ".preview-subitem" in css_source
    assert re.search(r"\.preview-subitem\s*\{[^}]*margin-left\s*:", css_source, re.DOTALL)
    # 间距改由 CSS 控制（2026-09-22 二次修复）：.preview-subitem 必须有 margin-top。
    assert re.search(r"\.preview-subitem\s*\{[^}]*margin-top\s*:", css_source, re.DOTALL)


def test_shared_question_preview_pipeline_renders_fillin_through_local_preprocessor():
    editor_source = _read(STATIC_JS_DIR / "editor.js")
    helper_start = editor_source.index("function transformFillinMacro(clean)")
    helper_marker = "window.renderQuestionPreviewContent = renderQuestionPreviewContent;"
    helper_end = editor_source.index(helper_marker, helper_start) + len(helper_marker)
    helper_source = editor_source[helper_start:helper_end]

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    script = r"""
global.window = {
  MathBankSafe: {
    safeImageUrl(value) { return value; },
    escapeAttribute(value) { return value; },
    sanitizeRichHtml(value) { return value; },
""" + _ESCAPE_TEXT_STUB + r"""  },
};
let katexCalls = 0;
let choicesCalls = 0;
function renderMathInElement(container, options) {
  katexCalls += 1;
  const serialized = options.delimiters.map(item => `${item.left}:${item.right}:${item.display}`);
  const expected = ['$$:$$:true', '$:$:false', '\\(:\\):false', '\\[:\\]:true'];
  if (JSON.stringify(serialized) !== JSON.stringify(expected)) {
    throw new Error(`unexpected KaTeX delimiters: ${JSON.stringify(serialized)}`);
  }
  if (options.throwOnError !== false) throw new Error('question preview must tolerate KaTeX errors');
}
function adaptChoicesGridLayout(container) {
  choicesCalls += 1;
  if (!container) throw new Error('choices layout received no container');
}
""" + _escape_html_helper_source() + helper_source + r"""
const originalPreprocess = preprocessFormulaForKaTeX;
let preprocessCalls = 0;
preprocessFormulaForKaTeX = function(text) {
  preprocessCalls += 1;
  return originalPreprocess(text);
};
const container = { innerHTML: '' };
const source = String.raw`填空：\fillin ![配图](/static/uploads/a.png) <img src="/static/uploads/b.png">`;
const preparedHtml = window.renderQuestionPreviewContent(
  container,
  source,
  { includeImages: false }
);
if (container.innerHTML.includes(String.raw`\fillin`)) {
  throw new Error(`fillin macro leaked into shared preview: ${container.innerHTML}`);
}
if (!container.innerHTML.includes(String.raw`\underline{\hspace{1.5cm}}`)) {
  throw new Error(`local fillin underline was not generated: ${container.innerHTML}`);
}
if (container.innerHTML.includes('<img') || container.innerHTML.includes('/static/uploads/')) {
  throw new Error(`question images leaked into image-free preview: ${container.innerHTML}`);
}
const reusedContainer = { innerHTML: '' };
const reusedHtml = window.renderQuestionPreviewContent(
  reusedContainer,
  String.raw`这段\fillin不应再预处理`,
  { includeImages: false, preparedHtml: preparedHtml }
);
if (reusedHtml !== preparedHtml || reusedContainer.innerHTML !== preparedHtml) {
  throw new Error('prepared question HTML was not reused exactly');
}
if (preprocessCalls !== 1) {
  throw new Error(`preparedHtml path reran preprocessing: ${preprocessCalls}`);
}
if (katexCalls !== 2 || choicesCalls !== 2) {
  throw new Error(`shared pipeline calls were incomplete: katex=${katexCalls}, choices=${choicesCalls}`);
}
"""
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr


def test_paper_preview_separates_choices_before_figure_layout():
    paper_source = _read(STATIC_JS_DIR / "paper.js")
    css_source = _read(CSS_PATH)

    helper_start = paper_source.index("function splitChoiceContentForPaperPreview(raw)")
    helper_end = paper_source.index("function formatQuestionContentHtml(", helper_start)
    helper_source = paper_source[helper_start:helper_end]

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    script = helper_source + r'''
const source = String.raw`题干
\begin{choices}
\item A
\item B
\end{choices}

![](/static/uploads/graph.png)`;
const result = splitChoiceContentForPaperPreview(source);
if (result.stemRaw.includes(String.raw`\begin{choices}`)) {
  throw new Error(`choices leaked into stem: ${result.stemRaw}`);
}
if (!result.stemRaw.includes('/static/uploads/graph.png')) {
  throw new Error(`figure was not retained with stem: ${result.stemRaw}`);
}
if (!result.choicesRaw.startsWith(String.raw`\begin{choices}`) || !result.choicesRaw.endsWith(String.raw`\end{choices}`)) {
  throw new Error(`choices block was not preserved: ${result.choicesRaw}`);
}
'''
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr

    split_position = paper_source.index(
        "const choiceContentParts = isChoiceQuestion"
    )
    stem_render_position = paper_source.index(
        "formatQuestionContentHtml(choiceContentParts.stemRaw", split_position
    )
    choices_render_position = paper_source.index(
        "formatQuestionContentHtml(choiceContentParts.choicesRaw", stem_render_position
    )
    assert split_position < stem_render_position < choices_render_position
    assert 'class="paper-choice-stem-row' in paper_source
    assert 'class="paper-choice-options-row"' in paper_source
    assert ".paper-choice-options-row" in css_source


def test_paper_preview_preserves_images_at_authored_complex_content_positions():
    paper_source = _read(STATIC_JS_DIR / "paper.js")
    # 预览渲染器现在依赖「题末图簇判定 + 逐图尺寸」两段纯函数，抽取范围要一起带上，
    # 否则 node 里跑的是半截脚本，报出来的错和用户看到的完全是两回事。
    helper_start = paper_source.index("function formatQuestionContentHtml(")
    helper_end = paper_source.index("// Init on DOMContentLoaded", helper_start)
    helper_source = (
        _paper_figure_size_source()
        + _paper_image_anchor_source()
        + paper_source[helper_start:helper_end]
    )

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    script = helper_source + r'''
global.window = {
  PaperStore: { questionsMap: {} },
  parseMarkdownWithMath: value => value,
  MathBankSafe: {
    safeImageUrl: value => value,
    escapeAttribute: value => value,
    sanitizeRichHtml: value => value
  }
};

const complex = String.raw`【课本回顾】
![](/static/uploads/fig12.png)

【知识探究】
\begin{tabular}{|c|c|c|}
 & 思路一 & 思路二 \\
\multirow{2}{*}{第一步} & 证明一 & 证明二 \\
 & 推导一 & 推导二 \\
图形表达 & ![](/static/uploads/fig3.png) & ![](/static/uploads/fig4.png) \\
\end{tabular}`;
const rendered = formatQuestionContentHtml(complex, 42, 'right', false, true);
const first = rendered.indexOf('/static/uploads/fig12.png');
const table = rendered.indexOf(String.raw`\begin{tabular}`);
const third = rendered.indexOf('/static/uploads/fig3.png');
const fourth = rendered.indexOf('/static/uploads/fig4.png');
const tableEnd = rendered.indexOf(String.raw`\end{tabular}`);
if (!(first >= 0 && first < table && table < third && third < fourth && fourth < tableEnd)) {
  throw new Error(`complex image anchors changed: ${rendered}`);
}
if (rendered.includes('data-figure-align-qid')) {
  throw new Error(`inline images were converted into a detached figure group: ${rendered}`);
}

const simple = String.raw`普通右图题

![](/static/uploads/graph.png)`;
const simpleRendered = formatQuestionContentHtml(simple, 43, 'right', false, true);
if (!simpleRendered.includes('data-figure-align-qid="43"')) {
  throw new Error(`legacy trailing figure layout was not preserved: ${simpleRendered}`);
}
'''
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr


def test_solution_space_controls_stay_at_the_resizable_zone_top():
    paper_source = _read(STATIC_JS_DIR / "paper.js")
    block_start = paper_source.index("solutionBlankHtml = `")
    block_end = paper_source.index("const itemHtml = `", block_start)
    block_source = paper_source[block_start:block_end]

    zone_position = block_source.index("solution-space-zone")
    controls_position = block_source.index("solution-space-controls", zone_position)
    assert zone_position < controls_position
    assert 'data-solution-space-controls-qid=' in block_source
    assert 'data-solution-space-zone-qid=' in block_source
    assert 'data-solution-space-delta="-1"' in block_source
    assert 'data-solution-space-delta="-0.5"' in block_source
    assert 'data-solution-space-delta="0.5"' in block_source
    assert 'data-solution-space-delta="1"' in block_source
    assert 'inline-flex w-14 justify-center' in block_source

    controls_source = block_source[controls_position:]
    assert "absolute right-2 top-2" in controls_source
    assert "bottom-2" not in controls_source
    assert "min-h-[32px]" not in block_source

    handler_start = paper_source.index(
        "function restoreSolutionSpaceControlViewport(qid, anchorTop, activeDelta)"
    )
    handler_end = paper_source.index(
        "window.updateGlobalSolutionSpace", handler_start
    )
    handler_source = paper_source[handler_start:handler_end]
    assert "getBoundingClientRect().top" in handler_source
    # 画布缩放后滚动容器像素与布局像素不同尺，补偿必须除以有效缩放率
    assert "scrollParent.scrollTop += shift / paperStageZoomFactor(scrollParent)" in handler_source
    assert "getBoundingClientRect().width / el.offsetWidth" in paper_source
    assert "requestAnimationFrame(restoreAnchor)" in handler_source
    assert "nextButton.focus({ preventScroll: true })" in handler_source
    assert handler_source.index("window.renderPaperCanvas()") < handler_source.rindex(
        "restoreSolutionSpaceControlViewport(qid, anchorTop, activeDelta)"
    )


def test_a4_paper_canvas_is_a_real_page_box_scaled_by_zoom():
    """「查看整卷」必须按真 A4 盒子排版，视觉缩放只作用于外层 stage。

    待办 #18 之前画布是一个假的 A4：宽度吃列宽（w-full）、上下用 40/48px 的
    Tailwind 内边距冒充 1in 页边距，结果「像 A4 但不是 A4」，导出 PDF 又是另一套
    口径，用户在画布上排好的分页与真卷完全无关。现在改为 794×1123 的盒子 +
    zoom 等比缩放，页内几何与导出侧 exam-zh 同参数。
    """

    css_source = _read(CSS_PATH)
    paper_source = _read(STATIC_JS_DIR / "paper.js")
    index_source = _read(INDEX_PATH)

    sheet_start = css_source.index("\n.a4-paper-sheet {")   # 前面带换行，才不会撞上 .dark .a4-paper-sheet
    sheet_block = css_source[sheet_start:css_source.index("}", sheet_start)]
    for declaration in (
        "width: 794px;",
        "min-height: 1123px;",
        "padding: 96px;",
        "font-size: 14px;",
        "line-height: 1.2;",
        '"Times New Roman"',
        "overflow: hidden;",
    ):
        assert declaration in sheet_block, sheet_block

    stage_start = css_source.index(".paper-zoom-stage {")
    stage_block = css_source[stage_start:css_source.index("}", stage_start)]
    assert "zoom: var(--paper-zoom, 1);" in stage_block
    assert "width: 794px;" in stage_block

    # 缩放只挂在 stage，页面盒子自身不参与缩放，否则页内实测口径会跟着变
    assert "zoom: var(--paper-zoom" not in sheet_block

    # 画布列宽 40% -> 52%，右侧不再留大片空白
    assert 'id="paperCanvasSection"' in index_source
    canvas_column = index_source[index_source.index('id="paperCanvasSection"') - 400:
                                 index_source.index('id="paperCanvasSection"')]
    assert "w-[52%]" in canvas_column
    assert "w-[48%]" in _read(INDEX_PATH)[:index_source.index('id="paperCanvasSection"')]

    # 三态缩放控件：适应宽度 / 100% / ±
    for hook in ("window.setPaperZoomFit", "window.setPaperZoomActual",
                 "window.stepPaperZoom", 'id="paperZoomReadout"'):
        assert hook in paper_source
    assert 'data-paper-zoom-mode="fit"' in paper_source
    assert 'data-paper-zoom-mode="manual"' in paper_source
    assert 'class="paper-zoom-stage" id="paperZoomStage"' in paper_source


def test_a4_paper_canvas_keeps_paper_texture_and_selectable_text():
    """纸面回归真实试卷观感：去掉卡片化装饰、水印文字，并允许选中复制。"""

    css_source = _read(CSS_PATH)
    paper_source = _read(STATIC_JS_DIR / "paper.js")

    sheet_start = css_source.index("\n.a4-paper-sheet {")   # 前面带换行，才不会撞上 .dark .a4-paper-sheet
    sheet_block = css_source[sheet_start:css_source.index("}", sheet_start)]
    declarations = [
        line.strip() for line in sheet_block.splitlines()
        if line.strip().endswith(";") and not line.strip().startswith(("*", "/*"))
    ]
    joined = "\n".join(declarations)
    assert "user-select: text;" in joined
    assert "select-none" not in joined

    # 题目不再是圆角卡片：边框/圆角/底色全部归零，也不再有 hover 变紫
    item_start = css_source.index(".a4-paper-sheet .paper-q-item {")
    item_block = css_source[item_start:css_source.index("}", item_start)]
    assert "margin: 0;" in item_block and "padding: 0;" in item_block
    assert "border: 0;" in item_block
    assert "border-radius: 0;" in item_block
    assert "background: transparent;" in item_block
    assert "box-shadow" not in item_block, item_block
    # hover 变紫/抬升的整条规则都必须消失（纸面不是卡片，不该有可点起的错觉）
    assert ".a4-paper-sheet .paper-q-item:hover" not in css_source

    # 解答题留白：极浅灰虚线，没有天蓝虚线框与水印文字
    zone_start = css_source.index(".a4-paper-sheet .solution-space-zone {")
    zone_block = css_source[zone_start:css_source.index("}", zone_start)]
    assert "#e2e8f0" in zone_block
    assert "dashed" in zone_block
    assert "#bae6fd" not in zone_block and "#7dd3fc" not in zone_block

    blank_start = paper_source.index("solutionBlankHtml = `")
    blank_block = paper_source[blank_start:paper_source.index("const itemHtml = `", blank_start)]
    assert "solution-space-label" not in blank_block, "纸面上不要再画留白水印文字"

    # 拖拽改由把手发起：整题 draggable 会让 Chrome 把正文当成不可复制的拖拽源
    assert 'draggable="true"' in paper_source
    toolbar_start = paper_source.index("paper-canvas-toolbar")
    toolbar_block = paper_source[toolbar_start:paper_source.index("</div>\n\n                        <div class=\"flex items-baseline", toolbar_start)]
    assert 'draggable="true"' in toolbar_block
    assert "onPaperCanvasDragStart" in toolbar_block

    item_start = paper_source.index("<div class=\"paper-q-item group relative\"")
    item_open_tag = paper_source[item_start:paper_source.index(">", item_start)]
    assert "draggable" not in item_open_tag, "整题 draggable 会让 Chrome 吞掉正文选中的鼠标事件"


def test_a4_canvas_hover_toolbar_fades_instead_of_masking_the_page():
    """纸面 hover 工具条不能糊住卷面：默认不可见，题目 hover 半透明，落到工具条才实体。"""

    css_source = _read(CSS_PATH)
    paper_source = _read(STATIC_JS_DIR / "paper.js")

    tag_start = paper_source.index('class="paper-canvas-toolbar')
    toolbar_tag = paper_source[tag_start:paper_source.index(">", tag_start)]
    assert "group-hover:opacity-100" not in toolbar_tag, "实心白牌会吞掉一行卷面"
    assert "opacity-0" not in toolbar_tag, "显隐改由 app.css 三段式统一管"

    hidden = css_source.index(".paper-canvas-toolbar {\n    opacity: 0;\n}")
    faded = css_source.index(".paper-q-item:hover .paper-canvas-toolbar {")
    solid = css_source.index(".paper-q-item:hover .paper-canvas-toolbar:hover,")
    assert hidden < faded < solid, "三段式靠书写顺序取胜，顺序不能调"
    assert "opacity: .55;" in css_source[faded:css_source.index("}", faded)]
    solid_block = css_source[solid:css_source.index("}", solid)]
    assert "opacity: 1;" in solid_block and ":focus-within" in solid_block, solid_block

    # 不能借这条规则把「题目 hover 变色」复活
    assert ".a4-paper-sheet .paper-q-item:hover" not in css_source


def test_a4_page_budget_uses_the_real_content_height():
    """分页预算必须走 A4_PAGE 常量：931px 正文高，首页再扣页眉。"""

    paper_source = _read(STATIC_JS_DIR / "paper.js")

    assert "A4_PAGE.contentPx = A4_PAGE.heightPx - A4_PAGE.marginPx * 2;" in paper_source
    assert "const PAGE_N_MAX = A4_PAGE.contentPx;" in paper_source
    assert "const PAGE_1_MAX = A4_PAGE.contentPx - estimateA4HeaderHeight(meta);" in paper_source
    # 旧的魔法数（一律 75px / 40px / 560px 视口高）不能回来
    assert "estHeight: 40," not in paper_source
    assert "const estH = estimateQuestionBlockHeight(" in paper_source
    assert "detachedImgCount" in paper_source


def test_a4_height_estimate_counts_figures_tables_and_choice_wrapping_in_node():
    """真跑 node：估高模型必须把插图/表格/选项折行/段间空行都算进去。

    浏览器实测（15 道真题语料）：改造前首页估 738px / 实际 1465px，页面被撑破
    220px；模型补齐口径后同一份卷面零溢出、首页填充率 0.91。
    """

    paper_source = _read(STATIC_JS_DIR / "paper.js")
    block_start = paper_source.index("    const A4_PAGE = {")
    block_end = paper_source.index("    function paperStageZoomFactor(", block_start)
    geometry_source = paper_source[block_start:block_end]

    script = "const api = (function (window) {\n" + geometry_source + r"""
return {
    A4_PAGE: A4_PAGE,
    estimateQuestionBlockHeight: estimateQuestionBlockHeight,
    paperChoicesHeightPx: paperChoicesHeightPx,
    paperBlockLinePx: paperBlockLinePx,
    paperFigureHeightPx: paperFigureHeightPx,
    paperInlineWidthPx: paperInlineWidthPx,
    paperTableRowCount: paperTableRowCount,
    paperTextHeightPx: paperTextHeightPx,
    estimateA4HeaderHeight: estimateA4HeaderHeight,
};
})(typeof window === 'undefined' ? {} : window);

const A4 = api.A4_PAGE;
function fail(msg) { throw new Error(msg); }

// 几何常数与导出侧 exam-zh 对齐
if (A4.widthPx !== 794 || A4.heightPx !== 1123 || A4.marginPx !== 96) fail('A4 页面几何漂移');
if (A4.contentPx !== 931 || A4.textPx !== 602) fail('版心/正文高漂移');
if (A4.bodyPx !== 14 || A4.linePx !== 17 || A4.cmPx !== 37.8) fail('字号/行高/cm 口径漂移');
if (A4.mathLinePx !== 21 || A4.fracLinePx !== 24 || A4.bigMathLinePx !== 34) fail('公式行盒口径漂移');
if (A4.tableRowPx !== 30 || A4.choiceColGapPx !== 14) fail('表格行/选项间距口径漂移');

// 1) 插图必须整块计高，且尊重 \includegraphics 自带的 max height
const figNoCap = '\\includegraphics[max width=0.85\\linewidth,keepaspectratio]{a.png}';
const figCapped = '\\includegraphics[max height=3.2cm,keepaspectratio]{a.png}';
if (api.paperFigureHeightPx(figNoCap) !== A4.figurePx) fail('无显式上限的插图未计高');
if (api.paperFigureHeightPx(figCapped) !== 141) fail('max height=3.2cm 应按 3.2*37.8+20 折算');
const stem = '已知函数 $f(x)$ 的图象如图所示，则 $f(x)$ 的解析式为 \\paren';
const withFig = stem + '\n\n' + figNoCap + '\n\\begin{choices}\n\\item $1$\n\\item $2$\n\\item $3$\n\\item $4$\n\\end{choices}';
const noFig = stem + '\n\\begin{choices}\n\\item $1$\n\\item $2$\n\\item $3$\n\\item $4$\n\\end{choices}';
const figGap = api.estimateQuestionBlockHeight('single_choice', withFig, 0, 'right', 0)
    - api.estimateQuestionBlockHeight('single_choice', noFig, 0, 'right', 0);
if (figGap < A4.figurePx - 10) fail('题干插图没有把整块高度计入：' + figGap);

// 2) 选项过长必须降列（4 -> 2 -> 1），降列后高度翻倍
const shortItems = '\\begin{choices}\n\\item $1$\n\\item $2$\n\\item $3$\n\\item $4$\n\\end{choices}';
const longItems = '\\begin{choices}\n\\item 该地平均每年新增至少 45 个水质好湖泊且治理达标率逐年提升\n\\item 该地平均每年新增至少 45 个水质好湖泊且治理达标率逐年提升\n\\item 该地平均每年新增至少 45 个水质好湖泊且治理达标率逐年提升\n\\item 该地平均每年新增至少 45 个水质好湖泊且治理达标率逐年提升\n\\end{choices}';
if (!(api.paperChoicesHeightPx(['该地平均每年新增至少 45 个水质好湖泊且治理达标率逐年提升 A', 'B', 'C', 'D'])
    > api.paperChoicesHeightPx(['短一', '短二', '短三', '短四']))) fail('长选项没有触发降列/折行');
const shortH = api.estimateQuestionBlockHeight('single_choice', stem + '\n' + shortItems, 0, 'right', 0);
const longH = api.estimateQuestionBlockHeight('single_choice', stem + '\n' + longItems, 0, 'right', 0);
if (longH <= shortH) fail('选项折列没有抬高整题估高：' + shortH + ' vs ' + longH);

// 3) 行盒口径：光有 $[0,2)$ 这种不带命令的公式也要按公式行高（回归：曾漏判成 17px）
if (api.paperBlockLinePx('$[0,2)$') !== A4.mathLinePx) fail('无命令内联式行高漏判');
if (api.paperBlockLinePx('\\dfrac{1}{a}>\\dfrac{1}{b}') !== A4.bigMathLinePx) fail('dfrac 行高口径漂移');
if (api.paperBlockLinePx('\\frac{1}{a}>\\frac{1}{b}') !== A4.bigMathLinePx) fail('行内 frac 已是 displaystyle，行高口径漂移');
if (api.paperBlockLinePx('\\sqrt{x}+1') !== A4.fracLinePx) fail('sqrt 行高口径漂移');

// 3b) 宽度补偿口径：全部来自无头浏览器里**已加载字体**的 KaTeX 实测（14px 行内）
//     \sqrt{x} 23.81 / x 9.70 → 牌面恒为 +14.11px = 2.0 半角单位（旧 0.9 少算约 8px）
//     \frac{1}{2} 行内 displaystyle 后 12.55，与 \dfrac{1}{2} 完全同宽（旧模型给 \dfrac 另加 +0.5，估到 20px）
//     \left(\frac{1}{2}\right)^2 撑高括号实测 44.27（逐件相加只有 36px）
if (api.paperInlineWidthPx('$\\sqrt{x}$') < 23.0) fail('根号牌面补偿不足：' + api.paperInlineWidthPx('$\\sqrt{x}$'));
if (api.paperInlineWidthPx('$\\frac{1}{2}$') < 12.5) fail('行内分式 displaystyle 宽度补偿不足');
if (api.paperInlineWidthPx('$\\left(\\frac{1}{2}\\right)^2$') < 44.0) fail('撑高定界符补偿不足');
const fracWide = api.paperInlineWidthPx('$\\frac{1}{2}$');
['\\dfrac', '\\cfrac', '\\tfrac'].forEach(function (macro) {
    if (Math.abs(api.paperInlineWidthPx('$' + macro + '{1}{2}$') - fracWide) > 0.001) {
        fail(macro + ' 与 \\frac 已同档渲染，宽度补偿却分叉');
    }
});
if (api.paperBlockLinePx('纯文字没有公式') !== A4.linePx) fail('纯文字行高不该被抬高');

// 4) 段间空行在预览里渲染成 <br><br>，实测多占一个行高
const onePara = api.paperTextHeightPx('第一段文字', 1);
const twoPara = api.paperTextHeightPx('第一段文字\n\n第二段文字', 1);
if (twoPara - onePara < A4.linePx) fail('段间空行没有计入高度');

// 5) 表格按行计高
const tabular = '\\begin{tabular}{|c|c|c|}\n\\hline\nx & 1 & 2 \\\\ \\hline\ny & 3 & 4 \\\\ \\hline\n\\end{tabular}';
if (api.paperTableRowCount(tabular) !== 2) fail('tabular 行数判定错误');
if (api.estimateQuestionBlockHeight('detailed_answer', stem + '\n' + tabular, 0, 'right', 0)
    <= api.estimateQuestionBlockHeight('detailed_answer', stem, 0, 'right', 0)) fail('表格没有计入估高');

// 6) 解答题即使留白 0cm，纸面仍有一条作答线 + 上下边距
// （用两行题干，避开估高下限 max(2 行) 对差值的截断）
const stemTwo = stem + '，且该结论对任意实数恒成立，需要给出完整证明过程并说明取等条件';
const withZone = api.estimateQuestionBlockHeight('detailed_answer', stemTwo, 0, 'right', 0);
const asChoice = api.estimateQuestionBlockHeight('single_choice', stemTwo, 0, 'right', 0);
if (withZone - asChoice !== 33) fail('0cm 留白的兜底高度漂移：' + (withZone - asChoice));
const sevenCm = api.estimateQuestionBlockHeight('detailed_answer', stemTwo, 7, 'right', 0);
if (sevenCm - withZone !== Math.round(7 * A4.cmPx) + 13 - 33) fail('留白厘米数换算漂移');

// 7) 首页页眉估高不能低于实测（实测 345px，估低会把首页撑破）
const header = api.estimateA4HeaderHeight({
    paper_type: 'exam',
    title: '2026 届普通高中学业质量监测数学试题',
    show_notice: true,
});
if (header < 345) fail('页眉估高低于实测：' + header);
"""
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr


def test_static_dialogs_expose_modal_semantics_and_accessible_names():
    elements = _index_elements()
    labelled_dialogs = {
        "aiClassifyModal": "aiClassifyModalTitle",
        "settingsModal": "settingsModalTitle",
        "updateModal": "updateModalTitle",
        "statsModal": "statsModalTitle",
        "lessonEditorModal": "lessonEditorTitle",
        "parsedDuplicateReviewModal": "parsedDuplicateReviewTitle",
        "pdfCropModal": "pdfCropModalTitle",
        "answerTikzWorkbenchModal": "answerTikzWorkbenchTitle",
    }

    for dialog_id, title_id in labelled_dialogs.items():
        attributes = elements[dialog_id]
        assert attributes["role"] == "dialog"
        assert attributes["aria-modal"] == "true"
        assert attributes["aria-hidden"] == "true"
        assert attributes["aria-labelledby"] == title_id
        assert title_id in elements

    # 已改造为内嵌工作区面板的旧弹窗：不能再声明模态语义，否则会把页面
    # 其余内容对辅助技术置为 inert，但仍必须保留可读名称。
    embedded_panels = {"latexImportModal"}
    for panel_id in embedded_panels:
        attributes = elements[panel_id]
        assert attributes["role"] == "region"
        assert attributes["aria-label"]
        assert "aria-modal" not in attributes

    lightbox = elements["imageLightbox"]
    assert lightbox["role"] == "dialog"
    assert lightbox["aria-modal"] == "true"
    assert lightbox["aria-hidden"] == "true"
    assert lightbox["aria-label"]

    workspace_button = elements["workspaceDropdownBtn"]
    assert workspace_button["role"] == "button"
    assert workspace_button["tabindex"] == "0"
    assert workspace_button["aria-haspopup"] == "menu"
    assert workspace_button["aria-expanded"] == "false"
    assert workspace_button["aria-label"]
    for button_id in ("toggleSidebarBtn", "themeDropdownBtn", "darkModeBtn", "statsOpenBtn"):
        assert elements[button_id]["aria-label"]


def test_duplicate_review_has_accessible_decision_controls_and_live_summary():
    elements = _index_elements()
    summary = elements["parsedDuplicateSummary"]
    assert summary["role"] == "status"
    assert summary["aria-live"] == "polite"
    assert summary["aria-atomic"] == "true"

    modal = elements["parsedDuplicateReviewModal"]
    assert modal["aria-describedby"] == "parsedDuplicateReviewDescription"
    for button_id in (
        "duplicateReviewReturnBtn",
        "duplicateReviewSkipBtn",
        "duplicateReviewIndependentBtn",
    ):
        assert button_id in elements

    import_source = _read(STATIC_JS_DIR / "import.js")
    assert "window.MathBankModal.open(modal" in import_source
    assert "onEscape: () => closeParsedDuplicateReviewModal('review')" in import_source
    assert "badge.focus({ preventScroll: true })" in import_source


def test_ai_classification_requires_manual_single_or_multi_choice_confirmation():
    index_source = _read(INDEX_PATH)
    import_source = _read(STATIC_JS_DIR / "import.js")
    css_source = _read(CSS_PATH)

    assert "temporaryClassifyData.question_type" not in import_source
    assert "qtypeLabels[data.question_type]" not in import_source
    assert "temporaryClassifyData.question_form === 'choice'" in import_source
    assert "!temporaryClassifyQuestionType" in import_source
    assert "请先确认此题是单选题还是多选题" in import_source
    assert "qtypeSelect.value = temporaryClassifyQuestionType" in import_source
    assert "window.selectClassifiedChoiceType = selectClassifiedChoiceType" in import_source
    assert 'id="recQType"' not in index_source
    assert 'id="choiceTypeConfirm"' in index_source
    assert 'id="classifySingleChoiceBtn"' in index_source
    assert 'id="classifyMultiChoiceBtn"' in index_source
    assert 'role="radiogroup"' in index_source
    assert index_source.count("question-type-choice-check") == 2
    assert "已识别为选择题，请手动确认" in index_source
    assert "确认分类并保存题目" in index_source
    assert '.question-type-choice-button[aria-checked="true"]:hover' in css_source
    assert 'color: #ffffff;' in css_source
    assert '.question-type-choice-button[aria-checked="true"] .question-type-choice-check' in css_source
    assert "button.classList.toggle('bg-brand-50'" not in import_source


def test_modal_manager_traps_focus_handles_escape_and_restores_focus():
    api_source = _read(STATIC_JS_DIR / "api.js")
    editor_source = _read(STATIC_JS_DIR / "editor.js")
    import_source = _read(STATIC_JS_DIR / "import.js")
    ocr_source = _read(STATIC_JS_DIR / "ocr.js")
    paper_source = _read(STATIC_JS_DIR / "paper.js")

    for marker in (
        "window.MathBankModal = MathBankModal",
        "const focusableSelector",
        "function resolveInitialFocusTarget(dialog, options = {})",
        "dialog.querySelector('[autofocus]')",
        "const labelledBy = dialog.getAttribute('aria-labelledby')",
        "document.getElementById(labelledBy)",
        "data-modal-initial-focus",
        "focusDialogContext(dialog, options)",
        "previousFocus: document.activeElement",
        "event.key === 'Escape'",
        "event.key !== 'Tab'",
        "if (!entry.dialog.contains(document.activeElement))",
        "!focusable.includes(document.activeElement)",
        "element.inert = isIsolated",
        "restoreTarget.focus({ preventScroll: true })",
    ):
        assert marker in api_source

    assert "dialog.querySelector('[autofocus], [data-modal-close]')" not in api_source
    assert "|| getFocusable(dialog)[0]" not in api_source

    # 「+添加厂商」事故后入口已统一：api.js 里 MathBankModal.open 只允许出现在
    # 公共助手内部一次，各文件必须经 openModalWithTransition/reveal 进栈。
    # import.js 残留的直接进栈来自无 opacity-0 形态的运行时动态弹窗，
    # 该计数只许降不许升——升了就说明有人又开始手写全屏遮罩开关。
    assert api_source.count("window.MathBankModal.open") == 1
    assert api_source.count("openModalWithTransition(") >= 4
    assert editor_source.count("openModalWithTransition(") >= 3
    assert import_source.count("openModalWithTransition(") >= 5
    assert import_source.count("window.MathBankModal.open") <= 4
    assert "window.openModalWithTransition(lightbox" in ocr_source
    assert "window.closeModalWithTransition(lightbox)" in ocr_source
    assert "window.revealModalWithTransition(modal)" in paper_source


def test_loading_saved_paper_restores_modal_background_interactivity():
    paper_source = _read(STATIC_JS_DIR / "paper.js")

    close_start = paper_source.index("window.closeSavedPapersModal = function ()")
    close_end = paper_source.index("window.openSavedPapersModal = async function ()", close_start)
    close_source = paper_source[close_start:close_end]
    load_start = paper_source.index("window.loadSavedPaper = async function (paperId)")
    # 删卷入口已改成「先预检再弹统一确认框」，签名不再是 async；锚点跟着实际形态走。
    load_end = paper_source.index("window.deleteSavedPaper = function (paperId)", load_start)
    load_source = paper_source[load_start:load_end]

    assert close_source.index("window.MathBankModal.close(modal)") < close_source.index("modal.remove()")
    assert "window.closeSavedPapersModal();" in load_source
    assert "modal.remove()" not in load_source


def test_mobile_layout_touch_targets_and_dialog_panes_have_regression_guards():
    css_source = _read(CSS_PATH)

    for marker in (
        "@media (max-width: 768px)",
        "@media (max-width: 768px), (pointer: coarse)",
        "@media (hover: none), (pointer: coarse)",
        "min-width: 44px",
        "min-height: 44px",
        "height: calc(100dvh - 56px)",
        "#bankWorkspaceSection",
        "#paperWorkspaceSection",
        "#previewSection",
        "#latexImportModalContent",
        "#pdfCropModalContent",
        "#pdfPagesThumbnailsContainer",
        '.question-card button[aria-label="删除题目"]',
        "max-width: calc(100vw - 1rem)",
        ".sidebar-pagination-controls",
        "overflow-x: auto",
        "overscroll-behavior-x: contain",
        ".tikz-workbench-modal",
        "#answerTikzWorkbenchModal",
        ".answer-tikz-entry",
    ):
        assert marker in css_source

    assert "html.init-ws-paper #paperWorkspaceSection { display: flex !important; }" in css_source
    assert "sidebar-pagination-controls" in _read(STATIC_JS_DIR / "editor.js")


def test_shared_tikz_workbench_is_multimodal_contextual_and_persistent():
    index_source = _read(INDEX_PATH)
    import_source = _read(STATIC_JS_DIR / "import.js")
    api_source = _read(STATIC_JS_DIR / "api.js")

    for marker in (
        'id="openContentTikzWorkbenchBtn"',
        'id="contentTikzAssetsPanel"',
        'id="contentTikzAssetsList"',
        'id="openAnswerTikzWorkbenchBtn"',
        'id="answerTikzInstruction"',
        'id="answerTikzReferenceInput"',
        'id="answerTikzReferenceDropZone"',
        'id="answerTikzUseContext"',
        'id="answerTikzWorkbenchCode"',
        'id="insertAnswerTikzWorkbenchBtn"',
        'id="tikzWorkbenchTargetBadge"',
        'role="button" tabindex="0"',
    ):
        assert marker in index_source

    assert "const TikzState = {" in api_source
    assert "contentAssets: []" in api_source
    assert "answerAssets: []" in api_source
    for marker in (
        "window.openTikzWorkbench",
        "window.openContentTikzWorkbench",
        "window.openAnswerTikzWorkbench",
        "window.renderContentTikzAssets",
        "window.registerAutoContentTikzAsset",
        "window.renderAnswerTikzAssets",
        "window.collectAnswerImagePaths",
        "formData.append('reference_image'",
        "formData.append('reference_image_path'",
        "fetch('/api/ai/draw_tikz'",
        "insertMarkdownAtSavedCursor",
        "answer_tikz_assets",
        "content_tikz_assets",
        "tikz_reference_image_path",
        "window.MathBankModal.open(modal",
        "EditorState.isCurrent(tikzWorkbenchState.editorSession)",
    ):
        assert marker in import_source

    for removed_legacy_marker in (
        'id="contentTikzContainer"',
        'id="answerTikzContainer"',
        'id="editContentTikzCode"',
        'id="editAnswerTikzCode"',
        "window.renderContentTikzToImage",
        "window.renderAnswerTikzToImage",
        "fetch('/api/correct_tikz'",
        "fetch('/api/ai/draw_tikz_from_image'",
    ):
        assert removed_legacy_marker not in index_source
        assert removed_legacy_marker not in import_source


def test_ocr_auto_tikz_keeps_original_question_image_as_edit_reference():
    ocr_source = _read(STATIC_JS_DIR / "ocr.js")
    import_source = _read(STATIC_JS_DIR / "import.js")

    for marker in (
        "formData.append('skip_tikz'",
        "window.registerAutoContentTikzAsset",
        "referenceImagePath: data.image_path || ''",
        "registerAutoTikzAsset('content', payload)",
        "setTikzReferencePath(",
        "data.reference_image_path",
        "formData.append('reference_image_path'",
    ):
        assert marker in ocr_source or marker in import_source

    assert "uploadedImages.push(safeReferencePath)" not in import_source
    assert "hiddenTikzReferencePaths" in import_source


def test_add_drawing_and_edit_drawing_are_separate_actions():
    index_source = _read(INDEX_PATH)
    import_source = _read(STATIC_JS_DIR / "import.js")

    assert index_source.count("<span>新增绘图</span>") == 2
    assert "<span>插入绘图</span>" not in index_source
    assert "新增一幅题干 TikZ 绘图，不会覆盖已有绘图" in index_source
    assert "新增一幅解答 TikZ 绘图，不会覆盖已有绘图" in index_source
    assert "window.openContentTikzWorkbench = function(assetId = null)" in import_source
    assert "window.openAnswerTikzWorkbench = function(assetId = null)" in import_source
    assert "? `修改${targetLabel} TikZ 绘图`" in import_source
    assert ": `新增${targetLabel} TikZ 绘图`" in import_source
    assert "TikzState.contentAssets.push(nextAsset)" in import_source
    assert "TikzState.contentAssets.splice(editingIndex, 1, nextAsset)" in import_source


def test_paper_question_answers_are_collapsible_and_loaded_on_demand():
    paper_source = _read(STATIC_JS_DIR / "paper.js")

    for marker in (
        "answerCache: Object.create(null)",
        "expandedAnswerIds: new Set()",
        "window.togglePaperQuestionAnswer",
        "window.collapseAllPaperAnswers",
        "fetch(`/api/questions/${qid}`)",
        "window.parseMarkdownWithMath(answerText)",
        'aria-expanded="${answerExpanded ? \'true\' : \'false\'}"',
        "参考答案与解析",
        "收起全部答案",
    ):
        assert marker in paper_source


def test_reduced_motion_dark_contrast_and_busy_feedback_are_explicit():
    css_source = _read(CSS_PATH)
    index_source = _read(INDEX_PATH)
    api_source = _read(STATIC_JS_DIR / "api.js")
    editor_source = _read(STATIC_JS_DIR / "editor.js")

    assert "@media (prefers-reduced-motion: reduce)" in css_source
    assert "animation-duration: 0.01ms !important" in css_source
    assert "transition-duration: 0.01ms !important" in css_source
    assert ".animate-spin" in css_source

    assert ".dark input::placeholder" in css_source
    assert "color: #94A3B8 !important" in css_source
    assert "outline: 2px solid rgb(var(--brand-500-rgb))" in css_source
    assert 'button[aria-busy="true"]' in css_source
    assert 'button[aria-disabled="true"]' in css_source
    assert '#questionsList[aria-busy="true"]' in css_source
    assert '[data-modal-initial-focus="true"]:focus-visible' in css_source

    assert "new MutationObserver" in api_source
    assert "button.setAttribute('aria-busy', 'true')" in api_source
    assert "triggerButton.disabled = true" in editor_source
    assert "triggerButton.setAttribute('aria-busy', 'true')" in editor_source
    assert ".finally(() =>" in editor_source
    assert 'id="toast" role="status" aria-live="polite"' in index_source
    for loading_id in (
        "classifyLoading",
        "importLoadingState",
        "contentOcrLoadingIndicator",
        "ocrLoadingIndicator",
        "aiLoadingIndicator",
    ):
        element = _index_elements()[loading_id]
        assert element["role"] == "status"
        assert element["aria-live"] == "polite"


def test_sidebar_uses_server_pagination_and_latest_request_wins():
    editor_source = _read(STATIC_JS_DIR / "editor.js")
    load_start = editor_source.index("function loadQuestions(retryCount = 0)")
    load_end = editor_source.index("//       SIDEBAR PAGINATION SYSTEM HELPERS", load_start)
    load_source = editor_source[load_start:load_end]

    for marker in (
        "new AbortController()",
        "bankQuestionsLoadController.abort()",
        "const loadSequence = ++bankQuestionsLoadSequence",
        "loadSequence !== bankQuestionsLoadSequence",
        "requestController.signal",
        "err.name === 'AbortError'",
        "params.append('page', String(requestedPage))",
        "params.append('page_size', String(PAGE_LIMIT))",
        "params.append('sort', sortOrder === 'asc' ? 'asc' : 'desc')",
        "Array.isArray(payload.items)",
        "const questions = payload.items",
        "Number(payload.total)",
        "Number(payload.total_pages)",
    ):
        assert marker in load_source

    assert "questions.sort(" not in load_source
    assert "questions.slice(" not in load_source


def test_word_export_prepares_pandoc_once_and_can_continue_in_compatibility_mode():
    index_source = _read(INDEX_PATH)
    paper_source = _read(STATIC_JS_DIR / "paper.js")

    for marker in (
        'id="pandocInstallModal"',
        '安装 Word 可编辑公式组件',
        '安装并继续导出',
        '本次兼容导出',
        'id="pandocInstallProgressBar"',
    ):
        assert marker in index_source

    for marker in (
        "fetch('/api/runtime/pandoc/status')",
        "fetch('/api/runtime/pandoc/install', { method: 'POST' })",
        "pollPandocInstall(state.task_id)",
        "await ensurePandocForWordExport()",
        "await generateAndDownloadWord(payload)",
        "decision === 'compatibility'",
    ):
        assert marker in paper_source


def test_generated_tailwind_classes_use_configured_scales():
    combined_source = "\n".join((_read(INDEX_PATH), *(_read(path) for path in JS_FILES)))
    assert "text-2xs" not in combined_source
    for invalid_shadow in ("shadow-xs", "shadow-2xs", "shadow-3xs"):
        assert invalid_shadow not in combined_source

    index_source = _read(INDEX_PATH)
    brand_start = index_source.index("brand: {")
    brand_block = index_source[brand_start:index_source.index("}", brand_start)]
    configured_brand_steps = {
        int(step)
        for step in re.findall(r"(\d{2,3}):\s*'rgba\(var\(--brand-", brand_block)
    }
    assert {50, 200, 500, 600, 700, 900} <= configured_brand_steps, "brand 色阶配置缺少基础档位"

    used_brand_steps = {
        int(step)
        for step in re.findall(
            r"(?:text|bg|border|ring)-brand-(\d{2,3})(?=[/\s\"'`])",
            combined_source,
        )
    }
    assert used_brand_steps <= configured_brand_steps

    standard_steps = {50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950}
    used_standard_steps = {
        int(step)
        for step in re.findall(
            r"(?:text|bg|border|ring)-(?:slate|gray|red|rose|orange|amber|yellow|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink)-(\d{2,3})(?=[/\s\"'`])",
            combined_source,
        )
    }
    assert used_standard_steps <= standard_steps


def _tree_editor_source() -> str:
    """api.js 中双栏大纲树编辑器（treeEditors ... window.treeAddComp）的源码片段。"""

    api = _read(STATIC_JS_DIR / "api.js")
    return api.split("var treeEditors", 1)[1].split("window.treeAddComp", 1)[0]


def test_metadata_tree_defaults_to_collapsed():
    """知识点树默认闭合：展开判据必须是 `=== true`（未记录即视为收起）。"""

    tree = _tree_editor_source()
    assert "!== false" not in tree
    assert tree.count("=== true") >= 8


def test_metadata_tree_supports_drag_sorting_on_all_levels():
    """册次 / 章节 / 小节 / 考点 四级均支持拖动排序。"""

    api = _read(STATIC_JS_DIR / "api.js")
    for helper in (
        "function treeGrip(",
        "function treeBindDrop(",
        "function treeMoveComp(",
        "function treeMoveChap(",
        "function treeMoveSec(",
        "function treeMoveTopic(",
    ):
        assert helper in api, helper
    tree = _tree_editor_source()
    for kind in ("'comp'", "'chap'", "'sec'", "'topic'"):
        assert "kind: " + kind in tree, kind


def test_legacy_single_pane_tree_does_not_overwrite_dual_pane_container():
    """旧版单栏树不得再渲染到 metadataTree（会覆盖右侧高三专题复习树）。"""

    api = _read(STATIC_JS_DIR / "api.js")
    start = api.index("window.renderMetadataVisualEditors = function()")
    body = api[start:start + 800]
    assert "metadataTreeRender()" not in body


def test_difficulty_gradient_tab_is_wired_into_settings_nav():
    """难度梯度是独立设置页签（导航 + 内容 + 切换逻辑齐全），并以表格呈现。"""

    index_source = _read(INDEX_PATH)
    elements = _index_elements()
    assert "btn-settings-difficulty" in elements
    assert "settings-tab-difficulty" in elements
    assert elements["btn-settings-difficulty"].get("onclick") == "switchSettingsTab('difficulty')"

    api = _read(STATIC_JS_DIR / "api.js")
    assert "btn-settings-difficulty" in api
    assert "settings-tab-difficulty" in api
    assert "tabName === 'difficulty'" in api

    start = index_source.index('id="settings-tab-difficulty"')
    end = index_source.index("<!-- JSON Config Tab Content -->")
    body = index_source[start:end]
    assert "<table" in body
    assert "难度梯度定义（L1–L5）" in body
    for level in ("概念理解", "基础运算", "性质应用", "综合运用", "创新拓展"):
        assert level in body, level
    for behavior in ("能说出", "会代公式", "会由性质求参数", "能拆解综合题", "能探究"):
        assert behavior in body, behavior
    for sub in ("定义、符号、表示", "直接套公式", "含参简单讨论", "标准压轴题", "竞赛 / 强基衔接"):
        assert sub in body, sub
    # 每个细目都带冒号说明
    for note in (
        "知道概念的内涵与外延",
        "进行常规的代数运算、化简与求值",
        "借助单调性、奇偶性等性质",
        "考查综合灵活运用能力的常规难题",
        "面向竞赛或强基计划的拔高题",
    ):
        assert note in body, note


def test_lesson_plan_workspace_is_kaoxiang_tree_driven():
    """教案以考向树为骨架：骨架 / 知识块 / 题目 / 思维导图四件套必须就位。"""

    index_source = _read(INDEX_PATH)

    # 工作区骨架（左侧检索列表 + 右侧文档预览）
    for node_id in (
        "lessonWorkspace",
        "lessonPreviewArea",
        "lessonSearchInput",
        "lessonFilterChips",
        "lessonListContainer",
        "lessonListFooter",
    ):
        assert 'id="' + node_id + '"' in index_source, node_id

    # 编辑器区块：考向骨架级联、知识块、思维导图、六环节
    for node_id in (
        "lessonOutlineCompulsory",
        "lessonOutlineChapter",
        "lessonOutlineSection",
        "lessonBlocks",
        "mindmapEditor",
        "processStagesContainer",
    ):
        assert 'id="' + node_id + '"' in index_source, node_id

    # 教学过程不再让用户手写 JSON
    assert "lessonProcess" not in index_source
    assert "JSON格式，可自定义环节" not in index_source

    lesson = _read(STATIC_JS_DIR / "lesson.js")
    for token in (
        "/api/lesson-plans/from-section",
        "/api/lesson-plans/section-topics",
        "knowledge_content",
        "mindmap",
        "question_refs",
        "buildOutline",
        "rebuildMindmap",
        "addBlock",
        "rowsToTree",
        "treeToRows",
        "exportMarkdown",
    ):
        assert token in lesson, token

    # 题目可增可删：题库检索 + 手动录入 + 移除
    assert "/api/questions?q=" in lesson
    assert "lesson-q-remove" in lesson
    assert "lesson-q-manual-add" in lesson


def test_parsed_card_image_source_badges_distinguish_bitmap_from_tikz():
    """拆题预览的配图角标必须能区分「PDF 位图裁剪」与「AI 重绘 TikZ」。

    背景：用户曾需要反复确认预览里的插图到底是不是 TikZ。角标由文件名判定，
    因此这里把该纯函数抽出来在 node 里跑真实分支。
    """

    import_source = _read(STATIC_JS_DIR / "import.js")
    start = import_source.index("function describeQuestionImageSource(path) {")
    end = import_source.index("\n        }\n", start) + len("\n        }\n")
    helper = import_source[start:end]

    script = (
        helper
        + """
const cases = [
  ['/static/uploads/tmp/pdf_crop_75824d00-eb64-4970-9e1e-d0e33aed0527_2_0.png', 'crop', '位图 · 第3页'],
  ['/static/uploads/pdf_crop_taskA_0_1.png', 'crop', '位图 · 第1页'],
  ['/static/uploads/tikz_abc123.png', 'tikz', 'AI · TikZ'],
  ['/static/uploads/dc5687b50f70a0eae76.png', 'bitmap', '位图']
];
const previewPage = describeQuestionImageSource(
  '/static/uploads/tmp/pdf_crop_taskA_4_0.png?cache=1#frag'
);
if (previewPage.kind !== 'crop' || previewPage.label !== '位图 · 第5页') {
  throw new Error('query/hash 未剥离: ' + JSON.stringify(previewPage));
}
for (const [path, kind, label] of cases) {
  const got = describeQuestionImageSource(path);
  if (got.kind !== kind || got.label !== label) {
    throw new Error(path + ' -> ' + JSON.stringify(got));
  }
  if (!got.title) throw new Error('缺少 title 提示: ' + path);
}
const empty = describeQuestionImageSource('');
if (empty.kind !== 'bitmap') throw new Error('空路径应回落为位图');
console.log('OK');
"""
    )
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_tikz_badge_helper_reports_all_four_states():
    """转 TikZ 的角标必须能表达四态：位图 / 已转 TikZ / 转换中 / 转换失败。

    背景：TikZ 转换存在失败率，失败若不显式标出，用户会以为「已经转好了」。
    这里把共享判定器抽到 node 里跑真实分支，锁住状态机与文案。
    """

    import_source = _read(STATIC_JS_DIR / "import.js")
    start = import_source.index("function describeQuestionImageSource(path) {")
    end = import_source.index("function parsedCardLocalToken() {", start)
    helper = import_source[start:end]
    # 判定器必须集中在同一段里，不能散落到各渲染点
    assert "function describeQuestionImageBadge(" in helper
    assert "function tikzBadgeToneClass(" in helper

    script = (
        "const parsedCardTikzStates = new Map();\n"
        + helper
        + """
const CROP = '/static/uploads/tmp/pdf_crop_taskA_2_0.png';
const base = describeQuestionImageBadge(3, CROP, false);
if (base.tone !== 'bitmap' || base.label !== '位图 · 第3页' || base.canRetry !== true) {
  throw new Error('位图态错误: ' + JSON.stringify(base));
}
setParsedCardTikzState(3, CROP, { status: 'converting' });
const converting = describeQuestionImageBadge(3, CROP, false);
if (converting.tone !== 'converting' || converting.label !== '转换中…' || converting.canRetry !== false) {
  throw new Error('转换中态错误: ' + JSON.stringify(converting));
}
setParsedCardTikzState(3, CROP, { status: 'failed', message: 'xelatex 不可用' });
const failed = describeQuestionImageBadge(3, CROP, false);
if (failed.tone !== 'failed' || failed.label !== '转换失败' || failed.canRetry !== true) {
  throw new Error('失败态错误: ' + JSON.stringify(failed));
}
if (failed.title.indexOf('xelatex 不可用') < 0) {
  throw new Error('失败原因未透出到 tooltip: ' + failed.title);
}
if (describeQuestionImageBadge(3, CROP, true).tone !== 'tikz') {
  throw new Error('已转 TikZ 是持久事实，不得被陈旧的「失败」记录盖住');
}
setParsedCardTikzState(3, CROP, { status: 'converting' });
if (describeQuestionImageBadge(3, CROP, true).tone !== 'converting') {
  throw new Error('转换中的进度必须可见，优先级高于已转');
}
clearParsedCardTikzState(3, CROP);
const restored = describeQuestionImageBadge(3, CROP, false);
if (restored.tone !== 'bitmap') {
  throw new Error('清除瞬时状态后未回到位图态: ' + JSON.stringify(restored));
}
const done = describeQuestionImageBadge(3, CROP, true);
if (done.tone !== 'tikz' || done.label !== 'AI · TikZ' || done.canRetry !== false) {
  throw new Error('已转态错误: ' + JSON.stringify(done));
}
const tones = ['tikz', 'converting', 'failed', 'bitmap'].map(tikzBadgeToneClass);
if (new Set(tones).size !== 4) {
  throw new Error('四态底色未区分: ' + tones.join('|'));
}
console.log('OK');
"""
    )
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_soft_delete_image_cleans_all_three_places():
    """删一张配图必须三处同步：image_paths、正文引用、指向它的 TikZ 资产。

    少任何一处都会留破绽：只删 image_paths 会在解析里留破图（后端只清题干、
    解答里的引用是特意保留的）；不删 TikZ 资产则 reference_image_path 悬空。
    """

    import_source = _read(STATIC_JS_DIR / "import.js")
    start = import_source.index("function stripParsedImageReference(")
    end = import_source.index("function parsedCardAssetFor(", start)
    helper = import_source[start:end]
    for token in (
        "function softDeleteParsedCardImage(",
        "function dropParsedTikzAssetsFor(",
        "function parsedCardImageHasTikz(",
    ):
        assert token in helper, token

    script = (
        "globalThis.window = { MathBankSafe: { safeImageUrl: p => (p ? String(p) : '') } };\n"
        "globalThis.document = { getElementById: () => null };\n"
        "const clearedStates = [];\n"
        "function clearParsedCardTikzState(index, safePath) "
        "{ clearedStates.push(index + ':' + safePath); }\n"
        "let parsedQuestionsData = [];\n"
        + helper
        + """
const IMG0 = '/static/uploads/tmp/pdf_crop_taskX_1_0.png';
const IMG1 = '/static/uploads/tmp/pdf_crop_taskX_1_1.png';
parsedQuestionsData = [{
  content: '如图 ![图](' + IMG0 + ') 所示，求角。',
  answer_markdown: '由 ![解答图](' + IMG1 + ') 可得。',
  image_paths: [IMG0, IMG1],
  referenced_images: [IMG0],
  content_tikz_assets: [{ reference_image_path: IMG0, image_path: '/static/uploads/tikz_a.png' }],
  answer_tikz_assets: [{ reference_image_path: IMG1, image_path: '/static/uploads/tikz_b.png' }]
}];

if (softDeleteParsedCardImage(0, IMG0) !== true) throw new Error('删除应返回 true');
const q = parsedQuestionsData[0];
if (q.image_paths.length !== 1 || q.image_paths[0] !== IMG1) {
  throw new Error('image_paths 未摘干净: ' + JSON.stringify(q.image_paths));
}
if (q.content.indexOf('pdf_crop_taskX_1_0.png') >= 0) throw new Error('题干引用未清除');
if (q.answer_markdown.indexOf('pdf_crop_taskX_1_1.png') < 0) throw new Error('解答引用被误删');
if (q.referenced_images.length !== 0) throw new Error('referenced_images 未清理');
if (q.content_tikz_assets.length !== 0) throw new Error('已转 TikZ 资产未连带移除');
if (q.answer_tikz_assets.length !== 1) throw new Error('其它图的 TikZ 资产被误删');
if (clearedStates.length !== 1) throw new Error('未清理转 TikZ 瞬时状态');

if (softDeleteParsedCardImage(0, '/static/uploads/tmp/not-owned.png') !== false) {
  throw new Error('不属于本题的图不应返回 true');
}

// 正文里写成相对路径 / 带 query 的形态，也要按文件名匹配删干净
parsedQuestionsData.push({
  content: '相对路径 ![图](tmp/pdf_crop_taskY_2_3.png?x=1)',
  answer_markdown: '',
  image_paths: ['/static/uploads/tmp/pdf_crop_taskY_2_3.png']
});
softDeleteParsedCardImage(1, '/static/uploads/tmp/pdf_crop_taskY_2_3.png');
if (parsedQuestionsData[1].content.indexOf('pdf_crop_taskY_2_3.png') >= 0) {
  throw new Error('相对路径/带 query 的引用未清除: ' + parsedQuestionsData[1].content);
}
console.log('OK');
"""
    )
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_replace_parsed_card_image_swaps_paths_text_and_edit_boxes():
    r"""「图形替换」必须与软删除同口径：image_paths、正文引用、**编辑框**、TikZ 参考图一起改。

    场景：原图被学科网水印压住，用户去小猿搜题重新截图后回来替换。
    只改 image_paths 不改正文 → 「图换了但解析里还是旧图」；
    不改编辑框更糟 → 保存时读的是编辑框 value，会把旧图原样写回去（软删除踩过同一个坑）。
    """

    import_source = _read(STATIC_JS_DIR / "import.js")
    start = import_source.index("function stripParsedImageReference(")
    end = import_source.index("function parsedCardAssetFor(", start)
    helper = import_source[start:end]
    assert "function replaceParsedCardImage(" in helper
    assert "function replaceParsedImageReference(" in helper

    script = (
        "const OLD = '/static/uploads/tmp/pdf_crop_taskX_1_0.png';\n"
        "const NEW = '/static/uploads/replaced_abc123.png';\n"
        "globalThis.window = { MathBankSafe: { safeImageUrl: p => (p ? String(p) : '') } };\n"
        "const textareaWrites = [];\n"
        "globalThis.document = { getElementById: () => ({\n"
        "  querySelector: sel => ({\n"
        "    get value() { return 'see ' + OLD; },\n"
        "    set value(v) { textareaWrites.push(sel + '|' + v); }\n"
        "  })\n"
        "}) };\n"
        "let parsedQuestionsData = [];\n"
        + helper
        + r"""
parsedQuestionsData = [{
  content: '如图 ![图](' + OLD + ') 所示，求角。',
  answer_markdown: '见 \includegraphics{pdf_crop_taskX_1_0.png}。',
  image_paths: [OLD],
  referenced_images: [OLD],
  content_tikz_assets: [{ reference_image_path: OLD, image_path: '/static/uploads/tikz_a.png' }],
  answer_tikz_assets: [{ reference_image_path: '/static/uploads/other.png', image_path: '/static/uploads/tikz_b.png' }]
}];

if (replaceParsedCardImage(0, OLD, NEW) !== true) throw new Error('替换应返回 true');
const q = parsedQuestionsData[0];
if (q.image_paths.length !== 1 || q.image_paths[0] !== NEW) {
  throw new Error('image_paths 未替换: ' + JSON.stringify(q.image_paths));
}
if (q.content.indexOf('replaced_abc123.png') < 0 || q.content.indexOf('pdf_crop_taskX_1_0.png') >= 0) {
  throw new Error('题干引用未替换: ' + q.content);
}
if (q.answer_markdown.indexOf('includegraphics{replaced_abc123.png}') < 0) {
  throw new Error('解答里 basename 形态的引用未替换: ' + q.answer_markdown);
}
if (q.referenced_images[0] !== NEW) throw new Error('referenced_images 未替换');
if (q.content_tikz_assets[0].reference_image_path !== NEW) {
  throw new Error('TikZ 参考图未改指新图: ' + JSON.stringify(q.content_tikz_assets));
}
if (q.answer_tikz_assets[0].reference_image_path !== '/static/uploads/other.png') {
  throw new Error('无关图的 TikZ 参考图被误改');
}
if (textareaWrites.length !== 2) {
  throw new Error('两个编辑框都要同步改，实际写了 ' + textareaWrites.length + ' 个');
}
if (textareaWrites.some(entry => entry.indexOf('replaced_abc123.png') < 0)) {
  throw new Error('编辑框没换成新图（保存时会把旧图写回去）: ' + JSON.stringify(textareaWrites));
}
if (replaceParsedCardImage(0, '/static/uploads/tmp/not-owned.png', NEW) !== false) {
  throw new Error('不属于本题的图不应返回 true');
}
console.log('OK');
"""
    )
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_replace_parsed_image_reference_does_not_eat_longer_filenames():
    r"""按 basename 替换必须加「后面不接文件名字符」的前瞻，否则会误伤前缀相同的长名。"""

    import_source = _read(STATIC_JS_DIR / "import.js")
    start = import_source.index("function parsedImageBasename(")
    end = import_source.index("function parsedImageDocumentOrder(", start)
    helper = import_source[start:end]

    script = (
        "globalThis.window = { MathBankSafe: { safeImageUrl: p => (p ? String(p) : '') } };\n"
        + helper
        + r"""
const OLD = '/static/uploads/tmp/pic_1_0.png';
const NEW = '/static/uploads/new_9_9.png';
const text = 'a \includegraphics{pic_1_0.png} b pic_1_0.png.bak c pic_1_0.png?x=1 d';
const out = replaceParsedImageReference(text, OLD, NEW);
if (out.indexOf('includegraphics{new_9_9.png}') < 0) throw new Error('正常引用未替换: ' + out);
if (out.indexOf('pic_1_0.png.bak') < 0) throw new Error('同名前缀的长文件被误伤: ' + out);
if (out.indexOf('new_9_9.png?x=1') < 0) throw new Error('带 query 的引用未替换: ' + out);
console.log('OK');
"""
    )
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_parsed_image_overview_is_wired_and_reuses_the_badge_helper():
    """整卷配图总览必须有入口按钮，且角标与批量转 TikZ 复用既有单一实现。"""

    import_source = _read(STATIC_JS_DIR / "import.js")
    html_source = _read(PROJECT_ROOT / "static" / "index.html")

    assert "openParsedImageOverview()" in html_source
    assert "window.openParsedImageOverview = openParsedImageOverview" in import_source

    start = import_source.index("function openParsedImageOverview() {")
    end = import_source.index("function buildParsedCardImageTile(", start)
    overview = import_source[start:end]
    # 角标复用共享判定器（不再各写一份四态规则）
    assert "describeQuestionImageBadge" in overview
    assert "tikzBadgeToneClass" in overview
    # 批量转 TikZ 复用既有的单张转换 + 限流并发
    assert "convertParsedCardImageToTikz" in overview
    assert "await Promise.all([worker(), worker()])" in overview
    # 删除走软删除，并带「已转 TikZ 需二次确认」
    assert "softDeleteParsedCardImage" in overview
    assert "parsedOverviewDeleteArmed" in overview
    # 弹窗控件用 DOM 构建后再挂监听（不经 DOMPurify，但遵循项目既有弹窗写法）
    assert "MathBankModal.open" in overview
    assert "addEventListener" in overview


def test_image_overview_exposes_gallery_and_pages_tabs():
    """审图面板要能「一屏看全所有图」，也能翻「本卷原页」。

    用户明确要求过：不要按题分组的列表（得逐个点开才能看），要把所有图铺成一面
    图墙、每张图直接标出属于第几题或未归属。所以这里是**自适应网格**而非列表。
    """

    import_source = _read(STATIC_JS_DIR / "import.js")
    start = import_source.index("function openParsedImageOverview() {")
    end = import_source.index("function handleParsedImageOverviewConvert(", start)
    overview = import_source[start:end]

    for tab in ('data-overview-tab="gallery"', 'data-overview-tab="pages"'):
        assert tab in overview, tab
    # 两个页签各自的渲染入口
    assert "renderParsedOverviewGalleryTab(" in overview
    assert "renderParsedOverviewPagesTab(" in overview
    # 图墙用自适应列宽铺开（窗口越宽铺得越多），不是按题分组的竖排列表
    assert "repeat(auto-fill, minmax(" in overview
    # 图墙上每张图直接标出题号 / 未归属
    assert "第 ' + (item.index + 1) + ' 题" in overview or "item.index + 1" in overview
    assert "opts.unassigned" in overview
    # 未归属图混排在图墙里，可指派给某题、也可直接删除
    assert "parsedOverviewAssignTarget" in overview
    assert "handleParsedUnassignedAssign" in overview
    assert "discardParsedUnassignedImages" in overview
    # 没有配图的题要能标出来源页，并能一键跳到原页
    assert "buildParsedOverviewEmptyRow(" in overview
    assert "focusParsedOverviewPage(" in overview
    # 未归属图没有归属题，转 TikZ 无处安放 → 不给转换入口
    assert "allowConvert: false" in overview
    # 整页图拿不到时必须说明原因（任务过期/需重新导入），
    # 而不是静默显示成「没有未归属图」。
    assert "重新导入一次即可" in import_source
    assert "parsedOverviewShowEmpty" in overview


def test_question_card_exposes_source_page_jump():
    """题目卡片上要能直接看到「源自第 N 页」并一键跳到原页。

    「我不知道这道题到底有没有图」是核心痛点，而卡片是用户最先看到的地方，
    不该强迫他先去总览面板里翻。只推断出来源页时才显示这个入口。
    """

    import_source = _read(STATIC_JS_DIR / "import.js")
    assert "window.openParsedQuestionSourcePage = openParsedQuestionSourcePage" in import_source

    start = import_source.index("function renderParsedQuestionsList(")
    end = import_source.index("function renderParsedCardPreview(", start)
    card_source = import_source[start:end]
    assert "openParsedQuestionSourcePage(" in card_source
    assert "card-source-page" in card_source
    assert "q._source_page" in card_source
    # 触摸目标遵守移动端最小高度约定
    assert "min-h-11" in card_source

    # 未归属图除了「指派给该题」，还必须能直接删除（垃圾图不必强行指派）
    assert "discardParsedUnassignedImages" in import_source
    delete_start = import_source.index("function handleParsedImageOverviewDelete(")
    delete_end = import_source.index("function handleParsedImageOverviewConvert(", delete_start)
    assert "discardParsedUnassignedImages" in import_source[delete_start:delete_end]


def test_parsed_card_does_not_inline_tikz_source_into_content():
    """题干编辑框**不得**拼接 TikZ 源码。

    源码已存在 `content_tikz_assets` 里，导出时 `paper_helper` 会直接取它内联编译成
    矢量图。曾经在渲染卡片时把源码追加到编辑框的 value，带来两条后果：
      ① 预览是从编辑框 value 渲染的 → 整段代码被当正文显示；
      ② 保存时 `content` 直接取编辑框 value → 源码被**永久写进题干 markdown**
         （后续 AI 调用要重读这堆代码，导出时一个括号不匹配就编译失败）。
    需要核对源码时走卡片上的「TikZ 源码」入口。
    """

    import_source = _read(STATIC_JS_DIR / "import.js")

    card_start = import_source.index("function renderParsedQuestionsList(")
    card_end = import_source.index("function renderParsedCardPreview(", card_start)
    card_source = import_source[card_start:card_end]
    # 拼接逻辑（当时的临时变量名）必须已经消失
    assert "initialContent" not in card_source
    assert "card-content-textarea').value = String(q.content || '')" in card_source

    # 源码入口：卡片模板 + 绑定 + 全局可达
    assert "card-content-tikz-source" in card_source
    assert "card-answer-tikz-source" in card_source
    assert "openParsedCardTikzSource(index, pair[0])" in card_source
    assert "window.openParsedCardTikzSource = openParsedCardTikzSource" in import_source

    # 方案 B：入口必须是绿色成功态，并带上**字符数**
    # —— 字符数是「确实存了源码」最直接的凭据（空的就是 0 字），
    #    用户曾反复怀疑转换是假的，靠这个数字消除疑虑。
    assert "✓ TikZ 源码已存" in card_source
    assert "border-emerald-300" in card_source
    assert "chars" in card_source

    # 只读查看器用 textContent 渲染源码（用 innerHTML 会把源码里的尖括号当 HTML 解析）
    viewer_start = import_source.index("function openParsedCardTikzSource(")
    viewer_end = import_source.index(
        "window.openParsedCardTikzSource = openParsedCardTikzSource", viewer_start
    )
    viewer = import_source[viewer_start:viewer_end]
    assert "pre.textContent = code;" in viewer
    assert "innerHTML" not in viewer

    # 方案 D：弹窗必须摊开「存储事实」——存在哪个字段、多少字、编译产物与原参考图分别是什么。
    # 因为点图只能看到 PNG（给人看的预览），源码是文本、没有图片 URL，
    # 不在这里说清楚，用户永远无法确认「源码到底存没存」。
    assert "源码已存于本题资产" in viewer
    assert "编译产物" in viewer
    assert "原参考图" in viewer
    assert "不是「没存源码」" in viewer


def test_parsed_card_textareas_use_auto_resize():
    """编辑框走 autoResizeTextarea **真自适应**；参数全用默认（按行高自算）。

    卡片曾固定 min-h-[80px]（裁切），后改 min-h-[300px] 死下限（内容少也 300px，
    不是自适应）。现改为：调用只传 bindWindowResize:false（22×2 个卡片不挂全局
    resize 监听），minHeight/maxHeight/bottomPadding 全交给 autoResizeTextarea
    按实际行高自算（内容高度 + 2 行留白，封顶视口 70% 后滚动）。
    """
    import_source = _read(STATIC_JS_DIR / "import.js")

    card_start = import_source.index("function renderParsedQuestionsList(")
    card_end = import_source.index("function renderParsedCardPreview(", card_start)
    card_source = import_source[card_start:card_end]

    assert "window.autoResizeTextarea" in card_source
    assert "bindWindowResize: false" in card_source
    # 不再硬编码留白/上下限（否则就不是「按行高算 2 行」了）
    assert "bottomPadding: 44" not in card_source
    assert "minHeight: 300" not in card_source
    assert "maxHeight: 3000" not in card_source
    # 必须放在 .value 赋值之后（否则首次 resize 读不到初始内容高度）
    value_idx = card_source.index("card-content-textarea').value = String(q.content")
    resize_idx = card_source.index("window.autoResizeTextarea")
    assert resize_idx > value_idx, "autoResizeTextarea 必须放在 .value 赋值之后"

    # 单题编辑器：也只传 {}，让函数自算
    editor_source = _read(STATIC_JS_DIR / "editor.js")
    sp_start = editor_source.index("function setupRealtimePreviews(")
    sp_end = editor_source.index("function ", sp_start + 1)
    sp_source = editor_source[sp_start:sp_end]
    assert "autoResizeTextarea(editContent, {})" in sp_source
    assert "autoResizeTextarea(editAnswer, {})" in sp_source
    assert "bottomPadding: 52" not in sp_source
    # autoResizeTextarea 支持按 lineHeight 自算 + bindWindowResize 开关
    ar_start = editor_source.index("function autoResizeTextarea(")
    ar_end = editor_source.index("window.autoResizeTextarea = autoResizeTextarea", ar_start)
    ar_source = editor_source[ar_start:ar_end]
    assert "getComputedStyle" in ar_source
    assert "lineHeight" in ar_source
    assert "bindWindowResize" in ar_source


def test_choice_images_have_their_own_slot():
    """选项图必须与题干图区分开（图墙标注「选项图」）。

    选项图跟在 `A．/B．/C．/D．` 后面，属于**选项本身**而不是题干配图；
    前端判据必须与后端 `_CHOICE_LABEL_RE` 同口径，否则会出现
    「后端保留了选项图、前端却把它当普通题干图」这种两边打架的情况。
    """

    import_source = _read(STATIC_JS_DIR / "import.js")
    assert "PARSED_CHOICE_LABEL_RE" in import_source
    assert "PARSED_IMAGE_ONLY_RE" in import_source
    assert "parsedCardChoiceImagePaths" in import_source
    assert "item.slot === 'choice' ? '选项图'" in import_source
    # choices 环境内 `\item` 行也是选项图（OCR 提示词要求剥离 A-D 标号，
    # 产出 `\item ![插图](...)`，只认标号的判据会漏掉它）。
    assert "PARSED_CHOICES_ITEM_RE" in import_source

    # 判定顺序：解答 > 选项 > 题干
    slot_start = import_source.index("function parsedCardImageSlot(")
    slot_end = import_source.index("function convertParsedCardImageToTikz(", slot_start)
    slot_source = import_source[slot_start:slot_end]
    assert slot_source.index("'answer'") < slot_source.index("'choice'")

    # 与后端同口径（选项行判据 + 保留选项图的剥离器 + 裸标号行 + choices 内 \item）
    pipeline = _read(PROJECT_ROOT / "mathbank" / "parsing_pipeline.py")
    assert "_CHOICE_LABEL_RE" in pipeline
    assert "_BARE_CHOICE_LABEL_RE" in pipeline
    assert "_strip_image_reference" in pipeline
    assert "_CHOICES_ITEM_RE" in pipeline
    assert "_choices_env_state" in pipeline


def test_choices_env_item_images_are_classified_as_choice_slot():
    r"""choices 环境内 `\item` 行上的图必须归到「选项图」槽。

    整页 OCR 的提示词要求剥离 A/B/C/D 标号，产出 `\item ![插图](...)`；
    只认标号的判据认不出来，图会被错标成「题干图」。这里把该函数抽到 node
    里跑真实代码，锁住与后端同口径的第三种形态。
    """

    import_source = _read(STATIC_JS_DIR / "import.js")
    # 从第一个选项判据常量起切，把 PARSED_* 正则与函数一起带进沙箱。
    start = import_source.index("const PARSED_CHOICE_LABEL_RE")
    end = import_source.index("// 选项横排（仅预览层", start)
    source = import_source[start:end]

    script = (
        "const window = { MathBankSafe: { safeImageUrl: (u) => (u ? String(u) : '') } };\n"
        + source
        + "\n"
        # ① OCR 形态：choices 内 \item 行上的图 -> 选项图
        "const ocr = '题干（ ）\\n\\n\\\\begin{choices}\\n"
        "\\\\item ![插图](tmp/a.png)\\n"
        "\\\\item ![插图](tmp/b.png)\\n"
        "\\\\end{choices}';\n"
        "const got = parsedCardChoiceImagePaths(ocr);\n"
        "if (got.length !== 2) throw new Error('choices 内 \\\\item 图应全部归为选项图: ' + JSON.stringify(got));\n"
        # ② choices 环境之外的普通题干配图不能算选项图
        "const outside = '题干见图 ![插图](tmp/c.png)\\n\\n"
        "\\\\begin{choices}\\n\\\\item 甲\\n\\\\item 乙\\n\\\\end{choices}';\n"
        "if (parsedCardChoiceImagePaths(outside).length !== 0) throw new Error('环境外配图被误判为选项图');\n"
        # ③ 环境之后再出现的游离 \\item 行也不算（只认环境内）
        "const after = '\\\\begin{choices}\\n\\\\item 甲\\n\\\\item 乙\\n\\\\end{choices}\\n\\n"
        "\\\\item ![插图](tmp/d.png)';\n"
        "if (parsedCardChoiceImagePaths(after).length !== 0) throw new Error('环境外的 \\\\item 行被误判');\n"
        # ④ 旧形态仍然有效：A．![](url) 同行
        "const inline = '题干\\nA．![插图](tmp/e.png) B．2 C．3 D．4';\n"
        "if (parsedCardChoiceImagePaths(inline).length !== 1) throw new Error('A．同行形态失效');\n"
        # ⑤ 入库后真实形态：choices 内 \\item 行上的 LaTeX \\includegraphics 必须归选项图
        "const latexItem = '\\\\begin{choices}\\n"
        "\\\\item \\\\includegraphics[max width=0.42\\\\linewidth]{x/a.jpg}\\n"
        "\\\\item \\\\includegraphics{x/b.jpg}\\n"
        "\\\\end{choices}';\n"
        "if (parsedCardChoiceImagePaths(latexItem).length !== 2) throw new Error('choices 内 LaTeX \\\\includegraphics 应归为选项图: ' + JSON.stringify(parsedCardChoiceImagePaths(latexItem)));\n"
        # ⑥ LaTeX \\includegraphics 在题干（choices 外）-> 不算选项图（归 content 槽）
        "const latexStem = '题干 \\\\includegraphics{stem.jpg}\\n"
        "\\\\begin{choices}\\n\\\\item 甲\\n\\\\item 乙\\n\\\\end{choices}';\n"
        "if (parsedCardChoiceImagePaths(latexStem).length !== 0) throw new Error('choices 外 LaTeX 图被误判为选项图');\n"
        # ⑦ Markdown + LaTeX 混合 -> 两种都认
        "const mixed = '\\\\begin{choices}\\n"
        "\\\\item ![插图](tmp/md.png)\\n"
        "\\\\item \\\\includegraphics{x/lx.png}\\n"
        "\\\\end{choices}';\n"
        "const mixedGot = parsedCardChoiceImagePaths(mixed);\n"
        "if (mixedGot.length !== 2) throw new Error('Markdown+LaTeX 混合应认出 2 张: ' + JSON.stringify(mixedGot));\n"
        # ⑧ 裸标号一行 + 下一行只有 LaTeX 图 -> 归选项图
        "const bareLatex = 'A\\n\\\\includegraphics{x/a.jpg}\\nB\\n\\\\includegraphics{x/b.jpg}';\n"
        "if (parsedCardChoiceImagePaths(bareLatex).length !== 2) throw new Error('裸标号+LaTeX 图应归为选项图: ' + JSON.stringify(parsedCardChoiceImagePaths(bareLatex)));\n"
    )

    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr


def test_plain_choice_paragraphs_are_grouped_into_choices_grid():
    """竖排的选项段落要在预览 DOM 上重组进 .choices-grid 四列自适应网格。

    MinerU/拆题产出的选项是「A 一段 / 图形一段 / B 一段 / …」，渲染成
    <p> 后一行一个选项，A4 与预览都浪费纵向空间；项目里已有 .choices-grid
    网格与选项图角标装饰，但只认 \\begin{choices} 源，普通文本选项触发不了。
    重组必须足够保守：连续 ≥3 个选项段（缺 A/B/C 之一不重组），
    「A、B 两点」这类普通正文绝不能被误判成选项。
    """

    import_source = _read(STATIC_JS_DIR / "import.js")
    start = import_source.index("const PARSED_CHOICE_LABEL_RE")
    end = import_source.index("function parsedCardImageSlot(", start)
    source = import_source[start:end]
    for token in (
        "function groupParsedCardOptionParagraphs(",
        "PARSED_BARE_CHOICE_LABEL_RE",
        "choices-grid",
        "data-preferred-columns",
    ):
        assert token in source, token

    script = (
        # —— 最小 DOM 桩：只实现分组函数用到的行为 ——
        "class FakeEl {\n"
        "  constructor(tag, text, hasImg) {\n"
        "    this.tagName = tag; this.nodes = []; this._text = text || '';\n"
        "    this._hasImg = !!hasImg; this.parentNode = null;\n"
        "  }\n"
        "  get children() { return this.nodes; }\n"
        "  get firstChild() { return this.nodes[0] || null; }\n"
        "  appendChild(n) { this.nodes.push(n); n.parentNode = this; return n; }\n"
        "  insertBefore(n, ref) {\n"
        "    const i = this.nodes.indexOf(ref);\n"
        "    if (i < 0) this.nodes.push(n); else this.nodes.splice(i, 0, n);\n"
        "    n.parentNode = this; return n;\n"
        "  }\n"
        "  removeChild(n) { const i = this.nodes.indexOf(n); if (i >= 0) this.nodes.splice(i, 1); return n; }\n"
        "  querySelector(sel) { return (sel === 'img' && this._hasImg) ? {} : null; }\n"
        "  querySelectorAll() { return []; }\n"
        "  setAttribute() {}\n"
        "  cloneNode() { return new FakeEl(this.tagName, this._text, false); }\n"
        "  get textContent() { return this._text; }\n"
        "  set textContent(v) { this._text = String(v); this.nodes = []; }\n"
        "}\n"
        + source.replace(
            "function groupParsedCardOptionParagraphs(container) {",
            "function groupParsedCardOptionParagraphs(container) {\n"
            "  const _gridMarker = (el) => { el._grid = true; return el; };\n"
            "  if (typeof document === 'undefined') {\n"
            "    globalThis.document = { createElement: (tag) => _gridMarker(new FakeEl(tag.toUpperCase())) };\n"
            "  }\n"
            "  return _groupParsedCardOptionParagraphsInner(container);\n"
            "}\n"
            "function _groupParsedCardOptionParagraphsInner(container) {",
        )
    ) + (
        "const container = new FakeEl('DIV');\n"
        "const p = (t, img) => { const el = new FakeEl('P', t, img); container.appendChild(el); return el; };\n"
        "// 题干 + 「A/图形/B/图形/C/图形/D/图形」竖排选项（本卷第 1 题真实形态）\n"
        "p('下列各曲线中，不能表示 $y$ 是 $x$ 的函数的是（ ）');\n"
        "const a1 = p('A'); const a2 = p('', true);\n"
        "const b1 = p('B'); const b2 = p('', true);\n"
        "const c1 = p('C'); const c2 = p('', true);\n"
        "const d1 = p('D'); const d2 = p('', true);\n"
        "groupParsedCardOptionParagraphs(container);\n"
        "const grids = container.nodes.filter(n => n._grid);\n"
        "if (grids.length !== 1) throw new Error('应恰好重组出 1 个网格: ' + grids.length);\n"
        "const grid = grids[0];\n"
        "if (grid.nodes.length !== 4) throw new Error('网格应有 4 个选项单元: ' + grid.nodes.length);\n"
        "if (grid.nodes[0].textContent !== 'A．') throw new Error('裸标号应补全为 A．: ' + grid.nodes[0].textContent);\n"
        "if (grid.nodes[3].textContent !== 'D．') throw new Error('第 4 项标号不对');\n"
        "// 网格要插在第一个选项段原来的位置（题干之后）\n"
        "if (container.nodes[0] !== container.nodes.find(n => n._text && n._text.indexOf('下列') === 0)) {\n"
        "  throw new Error('题干应仍在网格之前');\n"
        "}\n"
        "if (container.nodes[container.nodes.length - 1]._grid !== true) {\n"
        "  throw new Error('网格应在容器末尾（原选项段位置）');\n"
        "}\n"
        "// 「A、B 两点」这类正文不得误判：两个连续标号段 < 3 不重组\n"
        "const caseText = new FakeEl('DIV');\n"
        "caseText.appendChild(new FakeEl('P', 'A、B 两点共线'));\n"
        "caseText.appendChild(new FakeEl('P', 'C、D 也在直线上'));\n"
        "groupParsedCardOptionParagraphs(caseText);\n"
        "if (caseText.nodes.some(n => n._grid)) throw new Error('非选项正文被误重组');\n"
        "// 整行选项（A．106 B．105 C．104 D．103 同一段）保持原样，不拆不重组\n"
        "const caseInline = new FakeEl('DIV');\n"
        "caseInline.appendChild(new FakeEl('P', 'A．106 B．105 C．104 D．103'));\n"
        "groupParsedCardOptionParagraphs(caseInline);\n"
        "if (caseInline.nodes.some(n => n._grid)) throw new Error('单段多选项不应被拆');\n"
        "console.log('OK');\n"
    )
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_unassigned_images_are_derived_from_task_assets():
    """「未归属图」= 任务临时资产里的裁剪图 − 已被题目引用的那些（按 basename 比对）。

    整页原图（pdf_page_*）必须排除，它归「本卷原页」页签；顺序要按
    「页序_序号」数值排，否则多页任务下列表顺序会漂。
    """

    import_source = _read(STATIC_JS_DIR / "import.js")
    start = import_source.index("function parsedImageBasename(")
    end = import_source.index("function parsedCardAssetFor(", start)
    helper = import_source[start:end]
    for token in (
        "function collectParsedUnassignedImages(",
        "function collectParsedPageImages(",
        "function assignParsedUnassignedImages(",
        "function collectParsedImageOverview(",
    ):
        assert token in helper, token

    script = (
        "globalThis.window = { MathBankSafe: { safeImageUrl: p => (p ? String(p) : '') } };\n"
        "function parsedCardImageSlot() { return 'content'; }\n"
        "function parsedCardImageHasTikz() { return false; }\n"
        "let parsedQuestionsData = [];\n"
        + helper
        + """
const A = '/static/uploads/tmp/pdf_crop_taskZ_0_1.png';
const B = '/static/uploads/tmp/pdf_crop_taskZ_0_2.png';
const C = '/static/uploads/tmp/pdf_crop_taskZ_1_0.png';
const PAGE = '/static/uploads/tmp/pdf_page_taskZ_0.png';

window.pdfTempAssets = [PAGE, A, B, C];
window.pdfPageImages = [PAGE];
parsedQuestionsData = [{ content: '题干', answer_markdown: '', image_paths: [A] }];

const unassigned = collectParsedUnassignedImages();
if (unassigned.join(',') !== [B, C].join(',')) {
  throw new Error('未归属图算错（应排除已引用与整页图并按文档序）: ' + unassigned.join(','));
}
if (collectParsedPageImages().length !== 1) throw new Error('整页图未收集到');

// 指派：加进该题 image_paths；已在题上的图不重复添加
if (assignParsedUnassignedImages(0, [B, A]) !== 1) throw new Error('重复图不应被再次加入');
if (parsedQuestionsData[0].image_paths.length !== 2) throw new Error('指派结果不对');
const after = collectParsedUnassignedImages();
if (after.join(',') !== C) {
  throw new Error('指派后未归属图应只剩 C: ' + after.join(','));
}

// 题干里写成相对路径的引用，也要按 basename 认出来（否则会重复列为未归属）
parsedQuestionsData = [{ content: '题干', answer_markdown: '', image_paths: ['tmp/pdf_crop_taskZ_0_2.png'] }];
const relativeOwned = collectParsedUnassignedImages();
if (relativeOwned.join(',') !== [A, C].join(',')) {
  throw new Error('相对路径引用未被识别: ' + relativeOwned.join(','));
}

// 放弃未归属图：从本次会话的资产清单里摘掉，重开面板也不该再出现
window.pdfTempAssets = [PAGE, A, B, C];
parsedQuestionsData = [{ content: '题干', answer_markdown: '', image_paths: [A] }];
if (discardParsedUnassignedImages([C]) !== 1) throw new Error('放弃计数不对');
if (collectParsedUnassignedImages().join(',') !== B) {
  throw new Error('放弃后未归属图应只剩 B: ' + collectParsedUnassignedImages().join(','));
}
if (window.pdfTempAssets.indexOf(C) >= 0) throw new Error('资产清单里仍留着已放弃的图');
if (discardParsedUnassignedImages(['/static/uploads/tmp/pdf_crop_nope_0_0.png']) !== 0) {
  throw new Error('放弃不存在的图应返回 0');
}
window.pdfTempAssets = [PAGE, A, B, C];

// 题库为空时，全部裁剪图都是未归属（整页图仍需排除）
parsedQuestionsData = [];
if (collectParsedUnassignedImages().length !== 3) throw new Error('空题库时未归属图数量不对');

// 任务没有登记临时资产时不得抛错，返回空数组
window.pdfTempAssets = [];
if (collectParsedUnassignedImages().length !== 0) throw new Error('无资产清单时应返回空');
console.log('OK');
"""
    )
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_parsed_card_image_strip_offers_tikz_conversion_and_marks_converted():
    """配图条必须给位图「转 TikZ」入口，且已重绘的位图改显 TikZ 渲染结果。"""

    import_source = _read(STATIC_JS_DIR / "import.js")

    assert "/api/ai/draw_tikz" in import_source
    assert "/api/render_tikz" in import_source
    assert "window.convertParsedCardImageToTikz = convertParsedCardImageToTikz" in import_source
    assert "appendParsedCardImageStrip(contentPrev, q, index, answerText)" in import_source

    tile_start = import_source.index("function buildParsedCardImageTile(")
    tile_end = import_source.index("function appendParsedCardImageStrip(", tile_start)
    tile_source = import_source[tile_start:tile_end]
    # 未重绘 -> 显示转 TikZ 按钮；已重绘 -> 显示编译结果并标 AI · TikZ
    assert "转 TikZ" in tile_source
    assert "已转为 TikZ" in tile_source
    assert "reference_image_path: safePath" in import_source
    # 四态角标（位图 / AI·TikZ / 转换中 / 转换失败）统一由共享判定器产出，
    # 渲染点只负责取 tone/label/title——规则集中在一处才不会分叉。
    assert "describeQuestionImageBadge" in tile_source
    assert "tikzBadgeToneClass" in tile_source
    assert "badgeInfo.label" in tile_source
    # 缩略图不得再走 innerHTML 拼接（<button>/data-* 会被 DOMPurify 剥掉）
    assert "card-img-to-tikz" in tile_source
    assert "createElement('button')" in tile_source

    # 解答预览里的插图同样要标注来源并可直接转 TikZ
    assert "decorateParsedCardAnswerImages(answerPrev, q, index)" in import_source
    deco_start = import_source.index("function decorateParsedCardAnswerImages(")
    deco_end = import_source.index("function renderParsedCardPreview(", deco_start)
    deco_source = import_source[deco_start:deco_end]
    assert "card-img-to-tikz" in deco_source
    assert "describeQuestionImageBadge" in deco_source
    assert "tikzBadgeToneClass" in deco_source


def test_stem_and_answer_figures_are_routed_to_their_own_slots():
    """题干有图、解答也有图时：归属判定必须复用既有规则，并分槽展示与登记。

    DB 只有一个扁平 image_paths（题干图与解答图混存），因此归属只能在渲染/登记时
    按「是否出现在解答 markdown 里」派生——这与 editor.js / import.js 的保存路径
    使用的 collectAnswerImagePaths 是同一条规则。
    """

    import_source = _read(STATIC_JS_DIR / "import.js")

    # 1) 复用既有规则，不另造一套
    assert "window.collectAnswerImagePaths" in import_source
    helper_start = import_source.index("function parsedCardAnswerImagePaths(answerText)")
    helper_end = import_source.index("function parsedCardImageSlot(", helper_start)
    helper_source = import_source[helper_start:helper_end]
    assert "collectAnswerImagePaths" in helper_source
    # 切片沙箱里 window 未必带该函数，必须做存在性守卫
    assert "typeof window.collectAnswerImagePaths !== 'function'" in helper_source

    # 2) 题干配图条剔除解答图（解答区已内联呈现，不再重复）
    strip_start = import_source.index("function appendParsedCardImageStrip(")
    strip_end = import_source.index("function decorateParsedCardAnswerImages(", strip_start)
    strip_source = import_source[strip_start:strip_end]
    assert "parsedCardAnswerImagePaths(answerText)" in strip_source
    assert "!answerOwned.has(safePath)" in strip_source

    # 3) 卡片上的配图徽章同样剔除解答图
    assert "parsedCardAnswerImagePaths(q.answer_markdown)" in import_source
    assert ".filter(path => !answerOwned.has(path))" in import_source

    # 4) 转 TikZ 按归属写入对应槽位，且沿用同一 assetsKey 做幂等更新
    convert_start = import_source.index("async function convertParsedCardImageToTikz(")
    convert_end = import_source.index("window.convertParsedCardImageToTikz", convert_start)
    convert_source = import_source[convert_start:convert_end]
    assert "slot === 'answer' ? 'answer' : 'content'" in convert_source
    assert "'answer_tikz_assets'" in convert_source
    assert "'content_tikz_assets'" in convert_source
    assert "q[assetsKey] = assets" in convert_source
    # 题干条传 content、解答区传 answer
    assert "buildParsedCardImageTile(safePath, assetByRef[safePath], index, 'content')" in import_source
    assert "convertParsedCardImageToTikz(index, src, 'answer')" in import_source

    # 5) 手动裁图也能投放到解答：默认题干，可在弹窗里切换
    assert "window.pdfCropTargetSlot = 'content'" in import_source
    assert "window.setPdfCropTarget = function(slot)" in import_source
    assert "window.setPdfCropTarget('content');" in import_source  # 每次打开弹窗复位
    crop_start = import_source.index("window.setPdfCropTarget = function(slot)")
    crop_end = import_source.index("// Crop Selection variables", crop_start)
    crop_source = import_source[crop_start:crop_end]
    assert "aria-pressed" in crop_source
    assert "'pdfCropTargetContent'" in crop_source
    assert "'pdfCropTargetAnswer'" in crop_source

    index_source = _read(STATIC_DIR / "index.html")
    for element_id in ("pdfCropTargetToggle", "pdfCropTargetContent", "pdfCropTargetAnswer"):
        assert f'id="{element_id}"' in index_source, element_id
    assert 'onclick="setPdfCropTarget(\'answer\')"' in index_source



def test_import_logs_console_lives_in_left_nav_bottom():
    """解析日志应位于左侧导航栏底部（用户箭头指定的红色区域）。

    先在右侧预览区待过，后按用户截图要求迁回左侧导航栏底部、主题切换按钮上方。
    """

    index_source = _read(STATIC_DIR / "index.html")
    import_source = _read(STATIC_JS_DIR / "import.js")

    nav = index_source.index('id="globalNav"')
    nav_bottom = index_source.index('class="nav-bottom')
    log_panel = index_source.index('id="importLogPanel"')
    console = index_source.index('id="importLogsConsole"')
    review = index_source.index('id="importReviewPane"')
    wrapper = index_source.index('id="parsedQuestionsWrapper"')

    # 位置：必须在左侧导航栏底部的 nav-bottom 内，不能在右侧预览区
    assert nav < nav_bottom < log_panel < console
    assert log_panel < review
    assert log_panel < wrapper

    # 旧右侧高面板（min-h-[18rem]）已移除；旧左侧 h-24 / max-w-md 控制台也不存在
    assert "min-h-[18rem]" not in index_source
    assert 'max-w-md bg-slate-50 rounded-xl p-3 text-left font-mono text-[10px] text-slate-600 overflow-y-auto h-24' not in index_source

    panel_markup = index_source[log_panel:console]
    console_markup = index_source[console:console + 500]

    # 保持日志语义与可访问性
    assert 'role="log"' in console_markup
    assert 'aria-live="polite"' in console_markup

    # 字体调大、不加粗（用户明确要求）；左侧深色背景适配
    assert "font-mono text-xs" in panel_markup or "font-mono text-xs" in console_markup
    assert "font-bold" not in panel_markup

    # 有折叠按钮与终端图标，便于在窄栏里展开/收起
    assert "fa-terminal" in panel_markup
    assert 'onclick="toggleImportLogPanel()"' in panel_markup

    # 显隐同步：拆解期间与结果审查期间都显示，仅初始空态隐藏
    assert "function setupImportLogPanelSync()" in import_source
    assert "setupImportLogPanelSync();" in import_source
    assert "function toggleImportLogPanel()" in import_source
    sync_start = import_source.index("function setupImportLogPanelSync()")
    sync_end = import_source.index("// App Initialization", sync_start)
    sync_source = import_source[sync_start:sync_end]
    assert "importLoadingState" in sync_source
    assert "parsedQuestionsWrapper" in sync_source
    assert "isLoading || hasResults" in sync_source
    assert "MutationObserver" in sync_source
    assert "attributeFilter: ['class']" in sync_source

    # 日志行一律不加粗，只用颜色区分状态
    log_start = import_source.index("function appendImportLog(")
    log_end = import_source.index("function runAIPaperParse()", log_start)
    log_source = import_source[log_start:log_end]
    assert "font-semibold" not in log_source
    assert "font-bold" not in log_source
    assert "text-emerald-600" in log_source
    assert "text-red-500" in log_source


def test_markdown_pipe_table_keeps_structure_without_inline_style():
    """Markdown 管线表格必须靠 CSS 类出框线，不能靠内联 style。

    背景：用户反馈"表格不对"——渲染出来是一行挤在一起的文字。根因是表格 HTML 的
    边框/内边距全写在 style 属性里，而 MathBankSafe 的 DOMPurify 白名单
    (RICH_HTML_CONFIG.ALLOWED_ATTR) 不含 style，内联样式被静默剥掉。
    附带约束：AI/OCR 常把分隔行的列数写少（表头 6 格 / 分隔行 5 格），
    必须补齐而不是错位。
    """

    editor_source = _read(STATIC_JS_DIR / "editor.js")
    css_source = _read(CSS_PATH)

    # 1) 单一构造器：两条管线都必须走它
    assert editor_source.count("function buildPipeTableHtml(") == 1
    assert editor_source.count("buildPipeTableHtml(") == 3  # 1 处定义 + 2 处调用
    builder_start = editor_source.index("function buildPipeTableHtml(")
    builder_end = editor_source.index("function convertTabularEnvs(", builder_start)
    builder_source = editor_source[builder_start:builder_end]
    assert 'class="md-table-wrap"' in builder_source
    assert 'class="md-table"' in builder_source
    # 构造器里不允许出现内联样式
    assert "style=" not in builder_source

    # 2) 全文件不得再有"表格 + 内联 style"的产出
    assert not re.search(r"<table[^>]*style=", editor_source)
    assert not re.search(r"<t[hd] style=", editor_source)

    # 3) 样式由 app.css 提供
    assert ".md-table th," in css_source
    assert ".md-table td {" in css_source
    assert "border: 1px solid #cbd5e1;" in css_source

    # 4) 跑真实预处理：截图里那张 6 列 / 5 格分隔行的表
    helper_start = editor_source.index("function transformFillinMacro(clean)")
    helper_marker = "window.preprocessFormulaForKaTeX = preprocessFormulaForKaTeX;"
    helper_end = editor_source.index(helper_marker, helper_start) + len(helper_marker)
    helper_source = editor_source[helper_start:helper_end]

    script = r"""
global.window = {
  MathBankSafe: {
    safeImageUrl(value) { return value; },
    escapeAttribute(value) { return value; },
    sanitizeRichHtml(value) { return value; },
""" + _ESCAPE_TEXT_STUB + r"""  },
};
""" + _escape_html_helper_source() + helper_source + r"""
const screenshotTable = [
  '| $x$ | 10 | 15 | 20 | 25 | 30 |',
  '| :---: | :---: | :---: | :---: | :---: |',
  '| $Q(x)$ | 50 | 55 | 60 | 55 | 50 |',
].join(String.fromCharCode(10));
const rendered = window.preprocessFormulaForKaTeX(screenshotTable);
if (!rendered.includes('<table class="md-table">')) {
  throw new Error('表格未使用 md-table 类: ' + rendered);
}
if (rendered.includes('style=')) {
  throw new Error('表格仍依赖内联 style（会被 DOMPurify 剥掉）: ' + rendered);
}
const headerCount = (rendered.match(/<th /g) || []).length;
const bodyCount = (rendered.match(/<td /g) || []).length;
if (headerCount !== 6) throw new Error('表头列数应为 6，实际 ' + headerCount);
if (bodyCount !== 6) throw new Error('表体列数应为 6，实际 ' + bodyCount);
for (const token of ['$x$', '$Q(x)$', '10', '30', '55']) {
  if (!rendered.includes(token)) throw new Error('丢失单元格内容: ' + token);
}

// 必须保持"首尾都有竖线"作为表格判据：放宽会误吞 |x| 这类绝对值
const absoluteValue = ['设 |x| < 1 且 |y| > 2，求范围', '因为 |a| + |b| >= 0'].join(String.fromCharCode(10));
const absoluteRendered = window.preprocessFormulaForKaTeX(absoluteValue);
if (absoluteRendered.includes('<table')) {
  throw new Error('绝对值竖线被误判成表格: ' + absoluteRendered);
}
console.log('OK');
"""
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_preview_illustration_tikz_entry_is_dom_appended_not_sanitized_away():
    """预览配图的「转 TikZ」入口必须净化之后用 DOM 追加。

    背景：用户看到图下方只有一行 "TikZ" 文本，分不清是"已转成 TikZ"还是"没转"。
    根因是入口被拼成 <button onclick=...> 塞进 HTML，而 sanitizeRichHtml 的
    DOMPurify 白名单不含 <button>、也不放行 onclick，净化后按钮外壳被拆掉、
    只剩文本。现在改为 renderQuestionPreviewContent 里 DOM 追加。
    """

    editor_source = _read(STATIC_JS_DIR / "editor.js")

    # 1) 预处理阶段不得再产出按钮/内联事件
    assert 'onclick="autoTikzFromIllustration(' not in editor_source
    preprocess_start = editor_source.index("function preprocessFormulaForKaTeX(text)")
    preprocess_end = editor_source.index(
        "window.preprocessFormulaForKaTeX = preprocessFormulaForKaTeX;", preprocess_start
    )
    preprocess_source = editor_source[preprocess_start:preprocess_end]
    # 只断言"实际产出的标记"，注释里提到 <button> 不算
    assert "<button type=" not in preprocess_source
    assert "onclick=" not in preprocess_source
    # 图片本体与安全大图标记必须保留（既有断言依赖该字面量）
    assert 'data-safe-image-open="true"' in preprocess_source

    # 2) 净化之后用 DOM 追加真正可点的按钮
    decorator_start = editor_source.index("function decoratePreviewIllustrationButtons(")
    decorator_end = editor_source.index(
        "window.decoratePreviewIllustrationButtons = decoratePreviewIllustrationButtons;",
        decorator_start,
    )
    decorator = editor_source[decorator_start:decorator_end]
    assert "createElement('button')" in decorator
    assert "addEventListener('click'" in decorator
    assert "window.autoTikzFromIllustration(src)" in decorator
    # 沙箱（无 document / 容器是纯对象）里必须安全空转
    assert "typeof document === 'undefined'" in decorator
    assert "typeof container.querySelectorAll !== 'function'" in decorator
    # 已经是 TikZ 渲染结果 / AI 专用参考图不给入口
    assert "skipPaths" in decorator
    assert "reference_image_path" in decorator
    assert "asset && asset.image_path" in decorator

    # 3) 调用位置：必须在 KaTeX/选择题布局之后（净化已发生）
    call_index = editor_source.index("decoratePreviewIllustrationButtons(container);")
    adapt_index = editor_source.index("adaptChoicesGridLayout(container);", decorator_end)
    assert adapt_index < call_index


def test_editor_source_chips_render_and_delete_one_source():
    """「已存来源」chips 必须真在 DOM 上可点，且删除后主来源跟着换。

    内联七字段只能改写主来源，第 2..N 条此前既看不见也删不掉（多来源来自查重
    「并入已有题」），删掉弹窗旧实现后更没有入口。本用例用最小 DOM 桩真跑一遍
    渲染 + 删除，不用文本断言糊过去。
    """

    editor_source = _read(STATIC_JS_DIR / "editor.js")
    start = editor_source.index("var EDITOR_GRADE_LABELS")
    end = editor_source.index("window.applyEditorSourceFields = applyEditorSourceFields;", start)
    source = editor_source[start:end]
    for token in (
        "function renderEditorSourceChips(",
        "function buildEditorSourceChip(",
        "function removeEditorSourceAt(",
        "function setSelectWithFallback(",
    ):
        assert token in source, token
    # 来源文本是用户可控内容：只能 createElement + textContent，不得走 innerHTML
    chips_start = source.index("function editorSourceChipsHost(")
    assert "innerHTML" not in source[chips_start:]

    script = r"""
class El {
  constructor(tag, id) {
    this.tagName = String(tag || 'DIV').toUpperCase();
    this._id = ''; this.nodes = []; this._text = ''; this.attrs = {};
    this.listeners = {}; this.hidden = false; this.parentNode = null;
    this.className = ''; this.type = ''; this.title = ''; this._value = '';
    if (id) this.id = id;
  }
  get id() { return this._id; }
  set id(v) { this._id = v; if (v) registry[v] = this; }
  get options() { return this.tagName === 'SELECT' ? this.nodes : undefined; }
  get children() { return this.nodes; }
  get firstChild() { return this.nodes[0] || null; }
  get value() { return this._value; }
  set value(v) {
    const want = v === null || v === undefined ? '' : String(v);
    if (this.tagName === 'SELECT') {
      // 浏览器语义：值不在 option 列表里就回落成空 —— 静默丢数据正是从这里进来的
      this._value = this.nodes.some(function (o) { return o.value === want; }) ? want : '';
    } else { this._value = want; }
  }
  get textContent() {
    return this.nodes.length ? this.nodes.map(function (n) { return n.textContent || ''; }).join('') : this._text;
  }
  set textContent(v) { this._text = String(v); this.nodes = []; }
  set innerHTML(v) { throw new Error('来源 chips 不得使用 innerHTML'); }
  appendChild(n) { this.nodes.push(n); n.parentNode = this; return n; }
  insertBefore(n, ref) {
    if (!n) return n;
    const i = ref ? this.nodes.indexOf(ref) : -1;
    if (i < 0) this.nodes.push(n); else this.nodes.splice(i, 0, n);
    n.parentNode = this; return n;
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); }
  dispatch(t) { (this.listeners[t] || []).forEach(function (fn) { fn(); }); }
  click() { this.dispatch('click'); }
  closest(sel) {
    let n = this;
    const cls = /^\.([\w-]+)$/.exec(sel);
    while (n) {
      if (!cls || (n.className || '').split(/\s+/).indexOf(cls[1]) >= 0) return n;
      n = n.parentNode;
    }
    return null;
  }
}
class Opt {
  constructor(v, t) { this.value = v; this.textContent = t === undefined ? v : t; this.parentNode = null; }
}
const registry = {};
function el(tag, id) { const e = new El(tag, id); if (id) registry[id] = e; return e; }
function select(id, label, items, custom) {
  const s = el('SELECT', id);
  s.appendChild(new Opt('', label));
  (items || []).forEach(function (v) { s.appendChild(new Opt(v, v)); });
  if (custom) s.appendChild(new Opt('__custom__', '＋ 自定义…'));
  return s;
}
const metaRow = el('DIV', 'metaFullRow');
const group = el('DIV'); group.className = 'editor-meta-group';
const col = el('DIV'); col.className = 'editor-meta-col';
const fields = el('DIV'); fields.className = 'editor-meta-fields';
const row = el('DIV'); row.className = 'inline-flex items-center gap-1.5 shrink-0';
metaRow.appendChild(group); group.appendChild(col); col.appendChild(fields); fields.appendChild(row);
[
  select('editSrcYear', '年份', ['2023', '2024', '2025', '2026']),
  select('editSrcSemester', '学段', ['上', '下']),
  select('editSrcRegion', '学校', ['哈三中'], true),
  select('editSrcExamType', '类型', ['高考', '期末']),
  el('INPUT', 'editSrcPaperTitle'),
  el('INPUT', 'editSource'),
  select('editApplicableGrade', '年级', ['g1']),
].forEach(function (n) { row.appendChild(n); });
globalThis.document = {
  getElementById: function (id) { return registry[id] || null; },
  createElement: function (tag) { return tag === 'OPTION' ? new Opt('') : new El(tag); },
};
globalThis.window = globalThis;
window.EditorSources = [];
window.confirm = function () { return true; };
function fillEditorSrcProvince() {}
function fillEditorSrcCity() {}
function fillEditorSrcSchoolSelect() {}
function renderEditorPaperMeta() {}
""" + source + """
const out = {};
const year = document.getElementById('editSrcYear');
const region = document.getElementById('editSrcRegion');

// —— 1) 主来源的学校不在词库里：同步后不得被回落成空
window.EditorSources = [
  { year: '2023', region: '哈三中', exam_type: '期末', paper_title: '2023哈三中期末' },
  { year: '2016', region: '南溪一中', exam_type: '高考', paper_title: '2016宜宾期末' },
];
renderSourceChips();
out.mainFieldsKept = year.value === '2016' && region.value === '南溪一中';
out.tailOptions = region.options.map(function (o) { return o.value; }).slice(-2);

const host = document.getElementById('editorSourceChips');
out.chipsInSourceCol = host.parentNode === col;
out.chipLabels = host.children.map(function (c) { return c.firstChild.textContent; });
out.delAria = host.children.map(function (c) { return c.nodes[1].getAttribute('aria-label'); });
out.mainBadge = host.children[0].className.indexOf('is-main') >= 0;

// —— 2) 点 × 删掉主来源：剩下那条升为主来源，字段与隐藏域一起跟随
host.children[0].nodes[1].click();
out.yearsAfterDelete = window.EditorSources.map(function (s) { return s.year; });
out.fieldsAfterDelete = year.value + '|' + region.value;
out.hiddenAfterDelete = document.getElementById('editSource').value;
out.labelsAfterDelete = document.getElementById('editorSourceChips').children.map(function (c) { return c.firstChild.textContent; });

// —— 3) 取消确认时一条都不能少
window.EditorSources = [
  { year: '2023', region: '哈三中', exam_type: '三模' },
  { year: '2024', region: '师大附中', exam_type: '期末' },
];
renderSourceChips();
window.confirm = function () { return false; };
document.getElementById('editorSourceChips').children[0].nodes[1].click();
out.cancelledCount = window.EditorSources.length;

// —— 4) 内容没变就不得重建 DOM（否则内联字段每敲一个字都会抢走焦点）
window.confirm = function () { return true; };
renderSourceChips();
const before = document.getElementById('editorSourceChips').children[0];
renderEditorSourceChips();
out.rebuildSkipped = document.getElementById('editorSourceChips').children[0] === before;

console.log(JSON.stringify(out));
"""
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr

    import json

    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["mainFieldsKept"] is True, out
    assert out["tailOptions"] == ["南溪一中", "__custom__"], out   # 补的选项插在「自定义」之前
    assert out["chipsInSourceCol"] is True, out   # chips 落在来源组的字段列里，不挤进控件行
    assert out["mainBadge"] is True, out
    # 排序口径与后端一致：高考 > 模考 > 联考 > 校考/期末，同级别年份新在前
    assert out["chipLabels"] == [
        "主来源 · 2016年南溪一中高考",
        "2023年哈三中期末",
    ], out["chipLabels"]
    assert out["delAria"][0].startswith("删除来源："), out["delAria"]

    assert out["yearsAfterDelete"] == ["2023"], out
    assert out["fieldsAfterDelete"] == "2023|哈三中", out
    assert out["hiddenAfterDelete"] == "2023年哈三中期末", out
    assert out["labelsAfterDelete"] == ["主来源 · 2023年哈三中期末"], out

    assert out["cancelledCount"] == 2, out
    assert out["rebuildSkipped"] is True, out
def test_editor_source_sync_survives_the_province_city_cascade():
    """回写来源必须先重建级联选项再赋值，否则城市会被静默清掉。

    真实浏览器实测：旧 `syncEditorSourceFields` 先 `pEl.value=` / `cEl.value=`，
    再调 `fillEditorSrcCity()` 重建 option —— 而重建前城市列表是空的（要按省份过滤），
    于是「哈尔滨市」根本挂不上，下一次字段变更 `applyEditorSourceFields` 读回空值，
    就把这道题的 city 覆写成空。
    """

    editor_source = _read(STATIC_JS_DIR / "editor.js")
    start = editor_source.index("var EDITOR_GRADE_LABELS")
    end = editor_source.index("window.applyEditorSourceFields = applyEditorSourceFields;", start)
    main_block = editor_source[start:end]
    cascade_start = editor_source.index("function fillEditorSrcProvince(")
    cascade_end = editor_source.index("function matchEditorSrcSchoolToGeo(", cascade_start)
    cascade_block = editor_source[cascade_start:cascade_end]
    assert "fillEditorSrcCity();" in cascade_block
    assert "fillEditorSrcSchoolSelect();" in cascade_block

    script = r"""
class El {
  constructor(tag, id) {
    this.tagName = String(tag || 'DIV').toUpperCase();
    this._id = ''; this.nodes = []; this._text = ''; this.attrs = {};
    this.listeners = {}; this.hidden = false; this.parentNode = null;
    this.className = ''; this.type = ''; this.title = ''; this._value = '';
    if (id) this.id = id;
  }
  get id() { return this._id; }
  set id(v) { this._id = v; if (v) registry[v] = this; }
  get options() { return this.tagName === 'SELECT' ? this.nodes : undefined; }
  get children() { return this.nodes; }
  get value() { return this._value; }
  set value(v) {
    const want = v === null || v === undefined ? '' : String(v);
    if (this.tagName === 'SELECT') {
      // 浏览器语义：option 列表里没有这个值就回落成空
      this._value = this.nodes.some(function (o) { return o.value === want; }) ? want : '';
    } else { this._value = want; }
  }
  get textContent() { return this._text; }
  set textContent(v) { this._text = String(v); }
  set innerHTML(v) {
    const m = /^<option value="">([^<]*)<\/option>$/.exec(String(v));
    if (!m) throw new Error('这里只允许 select 的 option 复位写法：' + String(v).slice(0, 40));
    this.nodes = []; this.appendChild(new Opt('', m[1])); this._value = '';
  }
  appendChild(n) { this.nodes.push(n); n.parentNode = this; return n; }
  insertBefore(n, ref) {
    if (!n) return n;
    const i = ref ? this.nodes.indexOf(ref) : -1;
    if (i < 0) this.nodes.push(n); else this.nodes.splice(i, 0, n);
    n.parentNode = this; return n;
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  addEventListener() {}
  closest(sel) {
    let n = this;
    const cls = /^\.([\w-]+)$/.exec(sel);
    while (n) {
      if (!cls || (n.className || '').split(/\s+/).indexOf(cls[1]) >= 0) return n;
      n = n.parentNode;
    }
    return null;
  }
}
class Opt {
  constructor(v, t) { this.value = v; this.textContent = t === undefined ? v : t; this.parentNode = null; }
}
const registry = {};
function el(tag, id) { const e = new El(tag, id); if (id) registry[id] = e; return e; }
function select(id, label) { const s = el('SELECT', id); s.appendChild(new Opt('', label)); return s; }
const row = el('DIV');
[
  (function () { const y = select('editSrcYear', '年份'); ['2023', '2024', '2025'].forEach(function (v) { y.appendChild(new Opt(v, v)); }); return y; })(),
  select('editSrcSemester', '学期'),
  select('editSrcProvince', '省份'), select('editSrcCity', '城市'),
  select('editSrcRegion', '学校'), select('editSrcExamType', '考试类型'),
  el('INPUT', 'editSrcPaperTitle'), el('INPUT', 'editSource'),
  select('editApplicableGrade', '年级'),
].forEach(function (n) { row.appendChild(n); });
globalThis.document = {
  getElementById: function (id) { return registry[id] || null; },
  createElement: function (tag) { return tag === 'OPTION' ? new Opt('') : new El(tag); },
};
globalThis.window = globalThis;
window.EditorSources = [];
window.confirm = function () { return true; };;
window._regionsTree = [{ province: '黑龙江省', cities: ['哈尔滨市', '大庆市'] }];
window._schoolsDict = [{ name: '哈三中', province: '黑龙江省', city: '哈尔滨市' }];
function renderEditorPaperMeta() {}
""" + cascade_block + main_block + """
const out = {};
document.getElementById('editSrcCity').appendChild(new Opt('__custom__', '＋ 自定义…'));
window.EditorSources = [{
  year: '2024', province: '黑龙江省', city: '哈尔滨市', region: '哈三中',
  semester: '上', exam_type: '三模', paper_title: '2024哈三中三模',
}];
renderSourceChips();
out.provinceAfterSync = document.getElementById('editSrcProvince').value;
out.cityAfterSync = document.getElementById('editSrcCity').value;

// 模拟用户只动了一下年份：字段回读后城市不得消失
document.getElementById('editSrcYear').value = '2023';
applyEditorSourceFields();
const saved = window.EditorSources[0];
out.cityAfterApply = saved.city;
out.yearAfterApply = saved.year;
out.hiddenAfterApply = document.getElementById('editSource').value;
console.log(JSON.stringify(out));
"""
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr

    import json

    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["provinceAfterSync"] == "黑龙江省", out
    assert out["cityAfterSync"] == "哈尔滨市", out
    assert out["cityAfterApply"] == "哈尔滨市", out
    assert out["yearAfterApply"] == "2023", out
    assert "哈尔滨市" in out["hiddenAfterApply"], out

def test_editor_meta_group_styles_live_in_app_css():
    """元数据分组与来源 chips 的样式必须在 app.css 里，且带窄屏断点。

    预览净化白名单不含 `style`（AGENTS.md §6），录入区同样禁止内联样式：
    类名一旦只出现在 JS/HTML 里而 CSS 没有对应规则，就会静默变成「挤成一行的文字」。
    375px 必须可操作，所以还要有 768px 断点把组标签翻到字段上方、并取消左缩进。
    """

    css = _read(CSS_PATH)
    html = _read(INDEX_PATH)
    for cls in (
        ".editor-meta-group",
        ".editor-meta-group-label",
        ".editor-meta-fields",
        ".editor-meta-col",
        ".editor-meta-indent",
        ".editor-source-chip",
        ".editor-source-chip-del",
    ):
        assert cls in css, cls
    assert ".editor-source-chips[hidden]" in css and ".editor-meta-indent" in css
    media_at = css.index("@media (max-width: 768px)", css.index(".editor-meta-group"))
    block_end = css.index(".extra-topic-row {", media_at)
    block = css[media_at:css.index(chr(10) + "}", block_end)]
    assert ".editor-meta-indent" in block and "margin-left: 0" in block
    assert ".editor-meta-group" in block and "flex-direction: column" in block
    # HTML 侧只挂类名，不得出现内联 style
    region = html[html.index('<div id="metaFullRow"'):html.index('id="questionContentPanel"')]
    assert 'style="' not in region, "元数据区不得写内联 style"
    assert region.count('class="editor-meta-group"') == 3, region.count('class="editor-meta-group"')




def test_mineru_cache_force_fresh_toggle_is_cloud_only():
    """「忽略缓存」入口必须只在云端路线可见，且非云端时不得把 force_fresh 发出去。

    后端默认复用缓存省钱；但用户看不出这次到底有没有花钱就会以为解析坏了。
    同时这个开关若被带到原生/OCR 路线上没有任何语义，纯属噪声。
    """

    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index.html").read_text(encoding="utf-8")
    import_js = (root / "static" / "js" / "import.js").read_text(encoding="utf-8")

    assert 'id="mineruForceFresh"' in html
    assert 'id="mineruForceFreshRow"' in html
    # 初始收起：只有选中云端路线才展开
    assert 'class="hidden mt-1.5 flex items-center' in html
    assert "setupMineruCacheControls()" in import_js

    # 提交侧：仅当策略为 mineru_cloud 且勾选时才带 force_fresh
    gate = import_js[import_js.index("const mineruForceFresh"):import_js.index(
        "const endpoint = '/api/upload/pdf-task'"
    )]
    assert "pdfStrategy === 'mineru_cloud'" in gate
    assert "getElementById('mineruForceFresh')" in gate
    assert "pdfFormData.append('force_fresh', 'true');" in import_js
    assert import_js.count("pdfFormData.append('force_fresh'") == 1

    # 切换离开云端路线时必须复位，避免残留勾选悄悄烧钱
    body = import_js[import_js.index("function setupMineruCacheControls"):]
    body = body[:body.index("\n        }")]
    assert "checkbox.checked = false" in body
    assert "addEventListener('change', sync)" in body


def test_parsed_card_shows_content_fidelity_badge():
    """拆题卡片必须显示「内容与原卷不符」角标，否则后端的对账结论没人看得见。

    这条链路两端都会静默失效：后端不打 `_fidelity_review`（见
    `tests/test_output_fidelity.py`），或前端不读它——两种情况下用户都只会以为
    「系统检查过了、没问题」。所以这里同时钉住后端标记与前端渲染。
    """

    root = PROJECT_ROOT
    import_js = (root / "static" / "js" / "import.js").read_text(encoding="utf-8")
    pipeline = (root / "mathbank" / "parsing_pipeline.py").read_text(encoding="utf-8")

    assert "_fidelity_review" in import_js
    assert "内容与原卷不符" in import_js
    # 理由逐条进 title，不能只给一个笼统的「请核对」
    assert "fidelityReasons.join" in import_js
    # 与配图角标并列而非互斥：两种异常要能同时呈现
    strip = import_js[import_js.index("const reviewReasons"):import_js.index(
        "const fidelityReasons"
    )]
    assert "_image_review" in strip or "reviewReasons" in strip

    # 后端必须在归一化之前跑对账，并打印一行摘要
    assert "check_split_output_fidelity(" in pipeline
    assert "format_fidelity_log(" in pipeline
    call_at = pipeline.index("check_split_output_fidelity(")
    normalize_at = pipeline.index("return apply_storage_normalization(parsed_questions)")
    assert call_at < normalize_at, "对账必须在入库归一化之前，否则口径已与源文不同"



def _formula_review_helper_source() -> str:
    """从 api.js 里原样取出 MathBankFormulaReview，测试跑的就是线上那一份实现。"""

    api_source = _read(STATIC_JS_DIR / "api.js")
    start = api_source.index("window.MathBankFormulaReview")
    end = api_source.index("return { inspect: inspect, mathSpans: mathSpans };", start)
    end = api_source.index("})();", end) + len("})();")
    return api_source[start:end]


_FORMULA_REVIEW_JS_PRELUDE = r"""
const path = require('path');
global.katex = require(path.join(process.cwd(), 'static/lib/katex/katex.min.js'));
global.window = global;
"""

_FORMULA_REVIEW_FIXTURES = r"""
// 「必须放过」的一侧刻意用真实存储形态：这些写法在库里成百上千，误报一条就等于
// 整个功能作废（用户被无关角标轰炸后不会再信任何角标）。
const LEGIT = [
    '已知 $f(x)=\\frac{1}{x}$，则 $f(2)=\\frac{1}{2}$',
    '$\\left\\{\\begin{array}{l} x+1>0 \\\\ 2x-4\\leq 0 \\end{array}\\right.$',
    '集合 $A=\\complement_R B$，则 $\\complement_R A=\\varnothing$',
    '$\\lim\\limits_{n\\to\\infty} a_n = A$',
    '$[-1,1)$ 上的单调递增函数',
    '$\\sqrt[3]{8}=2$',
    '$\\overrightarrow{AB} \\cdot \\overrightarrow{CD} = 0$',
    '$\\{x \\mid x^2 - 3x + 2 = 0\\} = \\{1, 2\\}$',
    '$\\begin{cases} 1, & x>0 \\\\ -1, & x<0 \\end{cases}$',
    '$\\sum_{i=1}^{n} i = \\frac{n(n+1)}{2}$',
    '$C_{10}^3 = 120$',
    '下列各式正确的是（　）',
    '\\begin{choices}\n\\item $\\frac{1}{2}$\n\\item $\\frac{\\sqrt{3}}{2}$\n\\end{choices}',
    // 自有宏：KaTeX 严格模式必然报 Undefined control sequence，白名单必须放行，
    // 否则**每一道填空题**都会亮红角标。
    '则 $\\fillin$ 的最小值为____。',
    '【插图待补】比较 $a$、$b$ 大小并填 $\\fillin$',
    // 合法的空参数：`\frac{1}{}` / `\frac{}{}` 在 KaTeX 里完全合法，只有「没写完的
    // 花括号」才报错。这条边界一旦判错，正常题会被整批报成坏公式。
    '$\\frac{1}{}$',
    '$\\frac{}{} = 0$',
];
const BROKEN = [
    '$\\frac{1}{$}',
    '$\\dfrac{1}{x} + \\frac{2',
    '$\\begin{pmatrix} 1 & 2 \\\\ 3',
    '若 $a>b 则一定成立',       // 定界符落单：奇数个未转义 $
];
"""


def test_formula_review_passes_legit_stored_forms():
    """真跑 node + 内置 KaTeX：合法存储形态必须零误报。

    这条是整套判据的生死线。判据本身是启发式的，只要它开始乱报，用户就会连同
    真正有用的「内容与原卷不符」「配图待核对」一起无视。
    """

    script = (
        _FORMULA_REVIEW_JS_PRELUDE
        + _formula_review_helper_source()
        + _FORMULA_REVIEW_FIXTURES
        + r"""
const falsePositives = [];
for (const text of LEGIT) {
    const reasons = window.MathBankFormulaReview.inspect(text, '');
    if (reasons.length) falsePositives.push(text + ' => ' + reasons.join(' | '));
}
console.log(JSON.stringify({ count: falsePositives.length, items: falsePositives }));
"""
    )
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr
    import json

    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["count"] == 0, "公式自检误报了合法存储形态：\n" + "\n".join(
        payload["items"]
    )


def test_formula_review_helper_reads_katex_off_window_not_a_bare_identifier():
    """钉住真实失效：助手内部若引用裸 `katex`，页面里会静默空转。

    浏览器实测踩到的——调用点的 `typeof window.MathBankFormulaReview` 守卫形同虚设，
    函数体里那句裸 `katex.renderToString` 抛 ReferenceError，被 try/catch 吞成
    「没问题」，于是角标永远不出现且没有任何报错。

    ⚠️ 这个失效**在 node 里天然测不出来**：`global.window = global` 会让裸标识符和
    `window.katex` 指向同一个对象。必须用 vm 造一个「全局上没有 katex、只有
    window.katex」的独立上下文，才复现浏览器的加载语义。
    """

    import json

    script = (
        "const fs = require('fs');\n"
        "const vm = require('vm');\n"
        "const path = require('path');\n"
        "const katex = require(path.join(process.cwd(), 'static/lib/katex/katex.min.js'));\n"
        "const src = fs.readFileSync('static/js/api.js', 'utf8');\n"
        "const start = src.indexOf('window.MathBankFormulaReview');\n"
        "const marker = 'return { inspect: inspect, mathSpans: mathSpans };';\n"
        "const end = src.indexOf('})();', src.indexOf(marker, start)) + 5;\n"
        "const snippet = src.slice(start, end);\n"
        # `$f(x)=\frac{1}{$`：花括号没写完，严格模式必错
        "const probe = '\\u5df2\\u77e5 $f(x)=' + String.fromCharCode(92)\n"
        "    + 'frac{1}{$\\uff0c\\u7ed3\\u675f\\u3002';\n"
        "function run(how) {\n"
        "    const win = {};\n"
        "    const ctx = vm.createContext({ window: win });\n"
        "    if (how === 'windowOnly') win.katex = katex; else ctx.katex = katex;\n"
        "    vm.runInContext(snippet, ctx);\n"
        "    return win.MathBankFormulaReview.inspect(probe, '').length;\n"
        "}\n"
        "console.log(JSON.stringify({\n"
        "    windowOnly: run('windowOnly'),\n"
        "    bareOnly: run('bareOnly'),\n"
        "}));\n"
    )
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["windowOnly"] > 0, "KaTeX 只挂在 window 上时自检静默空转（裸标识符回归）"
    assert payload["bareOnly"] == 0, "实现不得依赖裸 katex 之外的解析路径"


def test_formula_review_flags_structurally_broken_math():
    """结构性损坏必须报出来：花括号没闭合、环境缺 \\end、定界符落单。

    这一类不会触发锁异常（锁只保证原文不丢，模型自己写坏的公式照样带回来），
    预览又是 `throwOnError:false`，静默降级成普通文字——组卷前没有任何地方会提示。
    """

    script = (
        _FORMULA_REVIEW_JS_PRELUDE
        + _formula_review_helper_source()
        + _FORMULA_REVIEW_FIXTURES
        + r"""
const missed = [];
for (const text of BROKEN) {
    const reasons = window.MathBankFormulaReview.inspect(text, '');
    if (!reasons.length) missed.push(text);
}
console.log(JSON.stringify({ count: missed.length, items: missed }));
"""
    )
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr
    import json

    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["count"] == 0, "公式自检漏掉了结构性损坏：" + "；".join(payload["items"])


def test_formula_review_survives_missing_katex_and_answers_are_scanned_too():
    """检测器自身坏掉时不得变成新的报错源；解答区的坏公式同样要抓到。

    ``katex`` 未加载（离线包缺失、脚本被拦）时必须安静返回空数组而不是抛异常——
    拆题卡片渲染路径整个 try/catch 兜着，但助手自己就该放行。
    """

    script = (
        r"""
global.window = global;
"""
        + _formula_review_helper_source()
        + r"""
let threw = '';
try {
    const reasons = window.MathBankFormulaReview.inspect('$\\frac{1}{$}', '');
    threw = Array.isArray(reasons) ? '' : 'not-array';
} catch (e) {
    threw = String(e && e.message);
}
"""
        + _FORMULA_REVIEW_JS_PRELUDE.replace("\n", "\n")
        + r"""
global.katex = require(path.join(process.cwd(), 'static/lib/katex/katex.min.js'));
const answerOnly = window.MathBankFormulaReview.inspect(
    '题干完全正常 $a+b=c$',
    '故选 B。由 $\\frac{1}{$ 解得 x=2'
);
console.log(JSON.stringify({ threw, answerOnly }));
"""
    )
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr
    import json

    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["threw"] == "", payload["threw"]
    assert payload["answerOnly"], "解答区里的坏公式没有被检出"


def test_parsed_card_renders_formula_badge_without_blocking_import():
    """接线检查：角标必须挂在卡片上，且导入闸门不得因它而变严。

    只测文本存在是不够的——曾经出现过角标代码写了但没人调用、以及角标和
    阻断逻辑混在一起两种失效，所以这里同时钉住「调用了」与「没有阻断」。
    """

    import_source = _read(STATIC_JS_DIR / "import.js")

    # 渲染入口调用一次严格模式自检，结果按题缓存
    assert "MathBankFormulaReview.inspect(" in import_source
    assert "_formula_review" in import_source

    # 角标经 createElement 追加（sanitizeRichHtml 白名单外的一切都不能拼 HTML）
    badge_builder = import_source[
        import_source.index("function buildParsedCardReviewBadges") : import_source.index(
            "function appendParsedCardImageStrip"
        )
    ]
    assert "createElement('span')" in badge_builder
    assert "公式结构待核对" in badge_builder
    # 三种角标共用一套外观，避免样式各写一份后走形
    assert badge_builder.count("REVIEW_BADGE_CLASS") >= 3

    # 无配图题也要能看到角标（配图条只在有图时生成）
    assert "appendParsedCardReviewBadges(" in import_source

    # 闸门口径不变：公式坏了不得阻止导入
    validate_body = import_source[
        import_source.index("function validateParsedQuestionBeforeImport") : import_source.index(
            "function ", import_source.index("function validateParsedQuestionBeforeImport") + 10
        )
    ]
    assert "_formula_review" not in validate_body, "公式结构自检不得参与导入阻断判定"


def test_api_js_helpers_do_not_use_regex_lookbehind():
    """AGENTS.md 明令禁止后行断言：旧 Safari / WebView 会 SyntaxError 中断级联加载。

    新写的定界符计数很容易顺手用 `(?<!\\)`，所以直接对全文件扫一遍而不是靠人记。
    先剥掉注释再扫——paper.js 顶部就写着这条禁令本身，逐字扫全文会永远误报。
    """

    def strip_comments(source: str) -> str:
        source = re.sub(r"/\*[\s\S]*?\*/", "", source)
        return "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("//")
        )

    for name in (
        "tailwind_gate.js",
        "api.js",
        "editor.js",
        "ocr.js",
        "import.js",
        "paper.js",
        "virtual_list.js",
        "lesson.js",
    ):
        code = strip_comments(_read(STATIC_JS_DIR / name))
        assert "(?<!" not in code, f"{name} 使用了后行断言"
        assert "(?<=" not in code, f"{name} 使用了先行断言"


def _extract_import_js_function(src: str, name: str) -> str:
    """按「函数名 + 顶格两空格闭合」取出 import.js 里的一个函数体。

    import.js 的函数一律缩进 8 空格、以 `        }` 闭合，这个约定被既有代码保持；
    抽取失败时宁可让测试报错，也不要静默拿到半截函数——那会让断言永远为真。
    """

    start = src.index("function " + name + "(")
    tail = src[start:]
    end = tail.index("\n        }")
    return tail[: end + len("\n        }")]


def test_formula_badge_is_actually_built_and_carries_the_reasons():
    """真跑 node + 最小 DOM 桩：角标必须被 createElement 造出来并带上逐条理由。

    纯文本断言挡不住这类失效：函数写了但没人调用、class 拼错导致角标不可见、
    title 忘了赋值让用户看不到到底是哪条公式坏了。这里直接检查产物节点。
    """

    import json

    source = _read(STATIC_JS_DIR / "import.js")
    script = "".join(
        (
            _FORMULA_REVIEW_JS_PRELUDE,
            _formula_review_helper_source(),
            "global.document = {\n",
            "    createElement: function (tag) {\n",
            "        return { tag: tag, className: '', textContent: '', title: '', children: [] };\n",
            "    },\n",
            "};\n",
            _extract_import_js_function(source, "buildParsedCardReviewBadges"),
            "\nvar REVIEW_BADGE_CLASS = 'card-review-badge';\n",
            "var q = {\n",
            "    _image_review: ['failsafe'],\n",
            "    _fidelity_review: ['选项变少（源 4 项 → 输出 3 项）'],\n",
            "};\n",
            "q._formula_review = window.MathBankFormulaReview.inspect("
            "'已知 $f(x)=\\\\frac{1}{$ 求定义域', '');\n",
            "var badges = buildParsedCardReviewBadges(q);\n",
            "console.log(JSON.stringify(badges.map(function (b) {\n",
            "    return { tag: b.tag, text: b.textContent, title: b.title, cls: b.className };\n",
            "})));\n",
        )
    )
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr
    badges = json.loads(result.stdout.strip().splitlines()[-1])

    # 三种理由各出一枚角标，且都是真实 span（不是 innerHTML 拼串）
    assert [b["tag"] for b in badges] == ["span", "span", "span"], badges
    texts = [b["text"] for b in badges]
    assert "配图由兜底推断，请核对" in texts
    assert "内容与原卷不符，请核对" in texts
    assert "公式结构待核对，请检查" in texts, texts

    formula = [b for b in badges if b["text"].startswith("公式结构")][0]
    assert formula["title"].startswith("KaTeX 严格模式报错：")
    # 用户要能看出是哪一段公式坏了，而不只是「有问题」
    assert "\\frac" in formula["title"], formula["title"]

    fidelity = [b for b in badges if b["text"].startswith("内容与原卷")][0]
    assert "选项变少" in fidelity["title"]

    # 外观走共享 class，样式不会三份各走形
    assert all("card-review-badge" in b["cls"] for b in badges), badges


def test_formula_badge_absent_for_clean_question():
    """干净题目一枚角标都不出——包括自有宏 \\\\fillin 那道生死线。"""

    import json

    source = _read(STATIC_JS_DIR / "import.js")
    script = "".join(
        (
            _FORMULA_REVIEW_JS_PRELUDE,
            _formula_review_helper_source(),
            "global.document = { createElement: function (t) {\n",
            "    return { tag: t, className: '', textContent: '', title: '' };\n",
            "} };\n",
            _extract_import_js_function(source, "buildParsedCardReviewBadges"),
            "\nvar REVIEW_BADGE_CLASS = 'card-review-badge';\n",
            "var q = { _image_review: [], _fidelity_review: [] };\n",
            "q._formula_review = window.MathBankFormulaReview.inspect(\n",
            "    '已知 $a\\\\in\\mathbb{{Z}}$，则 $\\\\complement_R A$ 为（　）并填 $\\\\fillin$',\n",
            "    '由 $\\\\begin{cases} x>0 \\\\\\\\ y<0 \\\\end{cases}$ 得 $\\\\frac{1}{2}$'\n",
            ");\n",
            "console.log(JSON.stringify({\n",
            "    reasons: q._formula_review,\n",
            "    badges: buildParsedCardReviewBadges(q).length,\n",
            "}));\n",
        )
    )
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["reasons"] == [], payload["reasons"]
    assert payload["badges"] == 0


def test_parsed_card_option_columns_control_is_wired():
    """拆卷卡片的「选项列数」下拉：控件 / 回填 / 脏检查 / 提交 四件事必须齐全。

    背景：拆完 22 题后要改列数，只能逐个「导入 → 打开 → 改」。后端
    ``create_question`` 早已接收 ``option_columns``，缺的只是卡片上的入口。
    取值必须与单题编辑器 ``editOptionColumns`` 完全一致（0=自适应，1/2/4=强制列数），
    否则同一道题在两条链上会得到不同排版。
    """
    import_source = _read(STATIC_JS_DIR / "import.js")
    index_source = _read(INDEX_PATH)

    # ① 控件存在，且四个取值齐全
    marker = 'class="card-option-columns'
    start = import_source.index(marker)
    block = import_source[start:import_source.index("</select>", start)]
    for value in ("0", "4", "2", "1"):
        assert f'value="{value}"' in block, f"选项列数缺少取值 {value}"
    assert "自适应" in block

    # ② 回填：AI 可能给 undefined / null / "" / 3（旧语义），一律兜底 0
    assert "const cardOptionColumns = card.querySelector('.card-option-columns');" in import_source
    assert "validCols" in import_source
    # ③ 脏检查 + ④ 保存时提交
    assert "optionColsInput.addEventListener('change'" in import_source
    assert "formData.append('option_columns'" in import_source
    # 单题编辑器侧同名控件必须存在，两边语义不能漂移
    assert 'id="editOptionColumns"' in index_source

    # 真跑 node：回填逻辑从源码抽出执行，挡住「静态断言全绿但取值写反」
    fill_start = import_source.index(
        "const cardOptionColumns = card.querySelector('.card-option-columns');"
    )
    fill_end = import_source.index(
        "}", import_source.index("cardOptionColumns.value =", fill_start)
    ) + 1
    fill_source = import_source[fill_start:fill_end]
    script = "".join(
        (
            "const el = { value: 'SENTINEL' };\n",
            "const card = { querySelector: function () { return el; } };\n",
            "function resolve(q) {\n",
            fill_source,
            "\n  return el.value;\n}\n",
            "var cases = [{}, {option_columns: null}, {option_columns: ''},",
            " {option_columns: 0}, {option_columns: 4}, {option_columns: 2},",
            " {option_columns: 1}, {option_columns: 3}, {option_columns: '4'}];\n",
            "var got = cases.map(resolve);\n",
            "var want = ['0','0','0','0','4','2','1','0','4'];\n",
            "if (JSON.stringify(got) !== JSON.stringify(want)) {\n",
            "  throw new Error('option_columns 回填漂移: ' + JSON.stringify(got));\n",
            "}\n",
        )
    )
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr


def test_panel_source_fields_propagate_to_parsed_card_source():
    """左侧「来源」字段变化后，拆卷卡片的「来源」要自动变成标准格式。

    回归：哈尔滨三中卷导入时面板填了 2023 / 哈三中 / 10月月考 / 黑龙江省 / 哈尔滨市 /
    高一，卡片来源却仍是 AI 抄的原卷标题。根因是 syncInheritedCardSource 拿
    「卡片值 === 旧面板标题」当继承判据——而卡片来源的初值是后端 q.source（AI 原始
    标题，见 parsing_pipeline 的 `source or paper_title`），与面板标题不相等；面板
    标题留空（placeholder 明说可留空）时还会因 !prevTitle 直接短路。这里把三个真实
    函数抽到 node 里跑，锁住修复后的行为。
    """

    import_source = _read(STATIC_JS_DIR / "import.js")
    start = import_source.index("function buildStandardPaperTitle(")
    end = import_source.index("function initSourceSmartFields(", start)
    fns = import_source[start:end]

    ai_raw = "哈三中2023-2024学年度上学期高一学年第一次验收考试数学试卷"
    std = "2023年哈三中高一10月月考"

    script = "".join((
        "const els = {\n",
        "  importSrcYear: { value: '2023' },\n",
        "  importSrcRegion: { value: '哈三中' },\n",
        "  importSrcExamType: { value: '10月月考' },\n",
        "  importSrcSemester: { value: '' },\n",
        "  importApplicableGrade: { value: 'g1' },\n",
        "  importPaperTitle: { value: '' },\n",
        "};\n",
        "const cardEl = { value: '" + ai_raw + "', dataset: {} };\n",
        "const document = {\n",
        "  getElementById: (id) => els[id] || null,\n",
        "  querySelectorAll: (sel) => (sel.indexOf('card-source') >= 0 ? [cardEl] : []),\n",
        "};\n",
        "const parsedQuestionsData = [{ source: cardEl.value }];\n",
        fns,
        "\n",
        # A：面板字段齐全、试卷标题留空 -> 卡片必须落到标准标题
        "applyStandardPaperTitle();\n",
        "if (cardEl.value !== '" + std + "') throw new Error('标题留空时卡片未更新: ' + cardEl.value);\n",
        "if (parsedQuestionsData[0].source !== '" + std + "') throw new Error('q.source 未同步');\n",
        # B：面板字段再变 -> 未手改的卡片继续跟随
        "els.importSrcExamType.value = '期末';\n",
        "applyStandardPaperTitle();\n",
        "if (cardEl.value !== '2023年哈三中高一期末') throw new Error('二次变更未同步: ' + cardEl.value);\n",
        # C：用户手改过这张卡 -> 不再被覆盖
        "cardEl.value = '我自己改的来源';\n",
        "cardEl.dataset.userEdited = '1';\n",
        "parsedQuestionsData[0].source = '我自己改的来源';\n",
        "parsedQuestionsData[0].__sourceUserEdited = true;\n",
        "els.importSrcExamType.value = '一模';\n",
        "applyStandardPaperTitle();\n",
        "if (cardEl.value !== '我自己改的来源') throw new Error('手改过的卡片被覆盖: ' + cardEl.value);\n",
        "if (parsedQuestionsData[0].source !== '我自己改的来源') throw new Error('手改过的 q.source 被覆盖');\n",
    ))

    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr


def test_parsed_card_source_edit_flag_is_bound():
    """卡片「来源」被用户手改后要留标记，后续面板字段变化不得覆盖。

    标记双向落地：DOM 上的 data-user-edited 给同步即时判读，数据对象的
    __sourceUserEdited 保证卡片重渲染后标记不丢；两条解析完成路径在渲染后
    还要主动调用一次标准标题同步，让新卡片默认落到标准格式。
    """

    import_source = _read(STATIC_JS_DIR / "import.js")

    card_start = import_source.index("function renderParsedQuestionsList(")
    card_end = import_source.index("function renderParsedCardPreview(", card_start)
    card_source = import_source[card_start:card_end]
    assert "dataset.userEdited = '1'" in card_source
    assert (
        "if (q.__sourceUserEdited === true) cardSourceInput.dataset.userEdited = '1';"
        in card_source
    )
    assert "__sourceUserEdited = true" in card_source
    assert "sourceEditBound" in card_source

    sync_start = import_source.index("function syncInheritedCardSource(")
    sync_end = import_source.index("function initSourceSmartFields(", sync_start)
    sync_source = import_source[sync_start:sync_end]
    # 认手改标记，且不再拿「等于旧面板标题」当继承判据
    assert "el.dataset && el.dataset.userEdited === '1'" in sync_source
    assert "q.__sourceUserEdited !== true" in sync_source
    assert "=== prevTitle" not in sync_source

    # 两条解析完成路径渲染后都调用标准标题同步（新卡片默认标准格式）
    assert import_source.count(
        "if (typeof applyStandardPaperTitle === 'function') applyStandardPaperTitle();"
    ) >= 5


def test_textareas_have_no_css_height_lock():
    """四个编辑 textarea 的 CSS **不得**带 min-h/max-h —— 否则会盖掉 JS 算出的高度。

    真自适应要求框高由 JS 的 inline height 主导；CSS 里任何 min-height/max-height
    都会把它按住（上一轮 min-h-[300px] 正是「内容少时框仍是 300px」的元凶）。
    resize-y 保留，允许用户手动拖。
    """
    import_source = _read(STATIC_JS_DIR / "import.js")
    card_start = import_source.index("function renderParsedQuestionsList(")
    card_end = import_source.index("function renderParsedCardPreview(", card_start)
    card_source = import_source[card_start:card_end]

    for cls in ("card-content-textarea", "card-answer-textarea"):
        i = card_source.index(cls)
        snippet = card_source[i : card_source.index("></textarea>", i)]
        assert "min-h-[" not in snippet, snippet
        assert "max-h-[" not in snippet, snippet
        assert "resize-y" in snippet, snippet

    index_source = _read(INDEX_PATH)
    for tid in ("editContent", "editAnswerMarkdown"):
        i = index_source.index('id="' + tid + '"')
        snippet = index_source[i : index_source.index("></textarea>", i)]
        assert "min-h-[" not in snippet, tid
        assert "max-h-[" not in snippet, tid
        assert "resize-y" in snippet, tid


def test_auto_resize_is_truly_adaptive_by_line_height():
    """真自适应：框高 = 内容高度 + 2 行留白；超视口 70% 才封顶并滚动。

    node 里跑真实的 autoResizeTextarea（模拟 lineHeight=21px、视口 800px）：
      3 行   -> 3*21 + 2*21 = 105px          （不再是 300px 死下限）
      20 行  -> 20*21 + 42   = 462px
      100 行 -> 封顶 70%*800 = 560px，overflowY=auto
    """
    editor_source = _read(STATIC_JS_DIR / "editor.js")
    fn_start = editor_source.index("function autoResizeTextarea(")
    fn_end = editor_source.index("window.autoResizeTextarea = autoResizeTextarea", fn_start)
    fn_source = editor_source[fn_start:fn_end]

    script = (
        "const LINE_H = 21;\n"
        "const VIEWPORT = 800;\n"
        "let __rafCb = null;\n"
        "const window = { innerHeight: VIEWPORT, addEventListener: function () {} };\n"
        "const requestAnimationFrame = function (cb) { __rafCb = cb; };\n"
        "const getComputedStyle = function () { return { lineHeight: LINE_H + 'px', fontSize: '14px' }; };\n"
        ""
        "const ResizeObserver = undefined;\n"
        + fn_source
        + "\n"
        "function run(lines) {\n"
        "  __rafCb = null;\n"
        "  const el = { offsetParent: {}, style: {}, scrollHeight: lines * LINE_H,\n"
        "    parentElement: null, addEventListener: function () {} };\n"
        "  autoResizeTextarea(el, {});\n"
        "  if (__rafCb) __rafCb();\n"
        "  return [parseInt(el.style.height, 10), el.style.overflowY];\n"
        "}\n"
        "const a = run(3), b = run(20), c = run(100);\n"
        "if (a[0] !== 105) throw new Error('3 行应为 105px，实际 ' + a[0]);\n"
        "if (b[0] !== 462) throw new Error('20 行应为 462px，实际 ' + b[0]);\n"
        "if (c[0] !== 560) throw new Error('100 行应封顶 560px，实际 ' + c[0]);\n"
        "if (a[1] !== 'hidden') throw new Error('3 行 overflowY 应为 hidden，实际 ' + a[1]);\n"
        "if (c[1] !== 'auto') throw new Error('100 行 overflowY 应为 auto，实际 ' + c[1]);\n"
    )
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr


def test_auto_resize_retries_when_scroll_height_is_zero():
    """首次布局未完成（scrollHeight=0）必须**定时重试**，不能放弃。

    历史 bug：resize() 遇到 scrollHeight===0 或 offsetParent===null 就直接 return，
    之后再没机会重算 → 卡片框停在浏览器默认高度（~130px），内容被截断。
    这里让 node 跑真实函数：先量不到（且 offsetParent=null 模拟容器未显示），
    布局完成后必须被重试补上正确高度。
    """
    editor_source = _read(STATIC_JS_DIR / "editor.js")
    fn_start = editor_source.index("function autoResizeTextarea(")
    fn_end = editor_source.index("window.autoResizeTextarea = autoResizeTextarea", fn_start)
    fn_source = editor_source[fn_start:fn_end]

    script = (
        "const LINE_H = 21;\n"
        "const VIEWPORT = 800;\n"
        "const window = { innerHeight: VIEWPORT, addEventListener: function () {} };\n"
        "let __raf = null;\n"
        "const requestAnimationFrame = function (cb) { __raf = cb; };\n"
        "const getComputedStyle = function () { return { lineHeight: LINE_H + 'px', fontSize: '14px' }; };\n"
        ""
        "const ResizeObserver = undefined;\n"
        + fn_source
        + "\n"
        "const el = { offsetParent: null, style: {}, scrollHeight: 0,\n"
        "  parentElement: null, addEventListener: function () {} };\n"
        "autoResizeTextarea(el, {});\n"
        "if (__raf) __raf();\n"
        "// 布局完成前不应把框定死\n"
        "if (el.style.height && el.style.height !== 'auto') {\n"
        "  throw new Error('scrollHeight=0 时不应定高: ' + el.style.height);\n"
        "}\n"
        "setTimeout(function () { el.scrollHeight = 3 * LINE_H; }, 30);\n"
        "setTimeout(function () {\n"
        "  const got = parseInt(el.style.height, 10);\n"
        "  if (got !== 105) throw new Error('重试后应为 105px，实际 ' + got);\n"
        "}, 600);\n"
    )
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr


def test_auto_resize_uses_js_not_field_sizing():
    """回归守卫：编辑框高度由 **JS 撑高** 主导，不再用 CSS `field-sizing`。

    2026-09-24 回退：field-sizing: content 存在已知渲染 bug——撑高后点击坐标
    映射错乱，编辑框内点击任意位置插入符都停在框顶、不跟随点击。故彻底移除该特性，
    改回纯 JS 的 autoResizeTextarea（始终写 inline height）。此测试锁死"不再回归"。
    """
    css = _read(CSS_PATH)
    # 精确匹配「真正的 CSS 声明」（带分号）；注释里"曾用 field-sizing: content"是说明、
    # 不带分号，不应误伤。
    assert "field-sizing: content;" not in css, "不得再启用 field-sizing（点击坐标 bug）"

    editor_source = _read(STATIC_JS_DIR / "editor.js")
    assert "_FIELD_SIZING_OK" not in editor_source, "field-sizing 逻辑应已彻底移除"
    assert "CSS.supports('field-sizing'" not in editor_source

    # node 里跑真实函数：必须走 JS 撑高（写 inline height），而非让位
    fn_start = editor_source.index("function autoResizeTextarea(")
    fn_end = editor_source.index("window.autoResizeTextarea = autoResizeTextarea", fn_start)
    fn_source = editor_source[fn_start:fn_end]
    script = (
        "const window = { innerHeight: 800, addEventListener: function () {} };\n"
        "const getComputedStyle = function () { return { lineHeight: '21px', fontSize: '14px' }; };\n"
        "const requestAnimationFrame = function () {};\n"
        "const ResizeObserver = undefined;\n"
        + fn_source
        + "\n"
        "const el = { offsetParent: {}, style: {}, scrollHeight: 63,\n"
        "  parentElement: null, addEventListener: function () {} };\n"
        "autoResizeTextarea(el, {});\n"
        "if (parseInt(el.style.height, 10) !== 105) throw new Error('应走 JS 撑高算成 105px，实际 ' + el.style.height);\n"
    )
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr


def test_legacy_browser_still_uses_js_auto_resize():
    """回归：autoResizeTextarea 始终走 **JS 撑高**，并挂 window resize 监听（不能整体失效）。"""
    editor_source = _read(STATIC_JS_DIR / "editor.js")
    fn_start = editor_source.index("function autoResizeTextarea(")
    fn_end = editor_source.index("window.autoResizeTextarea = autoResizeTextarea", fn_start)
    fn_source = editor_source[fn_start:fn_end]

    script = (
        "const CSS = { supports: function () { return false; } };\n"
        "let winEvents = [];\n"
        "const window = { innerHeight: 800,\n"
        "  addEventListener: function (n) { winEvents.push(n); } };\n"
        "const requestAnimationFrame = function () {};\n"
        "const getComputedStyle = function () { return { lineHeight: '21px', fontSize: '14px' }; };\n"
        "const ResizeObserver = undefined;\n"
        + fn_source
        + "\n"
        "const el = { offsetParent: {}, style: {}, scrollHeight: 63,\n"
        "  parentElement: null, addEventListener: function () {} };\n"
        "autoResizeTextarea(el, {});\n"
        "if (parseInt(el.style.height, 10) !== 105) throw new Error('老浏览器应算成 105px，实际 ' + el.style.height);\n"
        "if (winEvents.indexOf('resize') < 0) throw new Error('老浏览器应挂 window resize');\n"
    )
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr


def test_delete_paper_confirm_dialog_is_a_real_modal_with_no_markdown_leaks():
    """删卷确认弹窗必须是 MathBankModal，且文案里不能出现渲染不出来的 Markdown 记号。

    浏览器实测时抓到一个静默缺陷：弹窗用 createElement + textContent 构建（这是项目
    规则，见第 6 节净化白名单），而当初的文案写了 `**不会影响题库中的题目**`——
    Markdown 在 textContent 上完全不生效，用户看到的是字面星号，恰好把「题目不会被删」
    这句最要紧的承诺印成了乱码。静态断言当时全绿，只有真开浏览器才发现。
    """

    paper_source = _read(STATIC_JS_DIR / "paper.js")
    start = paper_source.index("window.openDeletePaperConfirm")
    end = paper_source.index("async function runDeletePaper", start)
    modal_source = paper_source[start:end]

    assert "window.MathBankModal.open(" in modal_source
    assert "delete-preview" in modal_source
    assert "initialFocus" in modal_source

    assert "不会影响题库中的题目" in modal_source
    assert "createElement('strong')" in modal_source
    assert "**" not in modal_source, (
        "textContent 渲染不了 Markdown；强调请用 <strong>，别写 **…**"
    )


def test_delete_paper_confirm_dialog_reads_the_preview_numbers_in_node():
    """真跑 node：喂进预检数据后，弹窗文本必须报出真实题数并带 <strong> 强调。"""

    paper_source = _read(STATIC_JS_DIR / "paper.js")
    start = paper_source.index("let deleteConfirmState")
    end = paper_source.index("async function runDeletePaper", start)
    helper = paper_source[start:end]

    script = """
const store = {};
function El(tag) {
  const n = {
    tagName: tag.toUpperCase(),
    children: [],
    _text: '',
    style: {},
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    appendChild(c) { this.children.push(c); return c; },
    setAttribute(k, v) { this['@' + k] = v; },
    getAttribute(k) { return this['@' + k] === undefined ? null : this['@' + k]; },
    querySelector(sel) {
      const want = sel.replace('#', '');
      const walk = (node) => {
        for (const c of node.children || []) {
          if (c.id === want) return c;
          const hit = walk(c);
          if (hit) return hit;
        }
        return null;
      };
      return walk(this);
    },
    querySelectorAll() { return []; },
    remove() {},
    focus() {},
    addEventListener() {},
  };
  Object.defineProperty(n, 'id', { get() { return this._id || ''; }, set(v) { this._id = v; } });
  Object.defineProperty(n, 'className', { get() { return this._cls || ''; }, set(v) { this._cls = v; } });
  Object.defineProperty(n, 'textContent', {
    get() { return this._text; },
    // 真实 DOM 语义：赋值 textContent 会替换掉全部子节点。桩必须照做，否则元素会同时
    // 留着旧 children 和新 _text，遍历出来的文本和产品代码差着一整段。
    set(v) { this._text = String(v); this.children.length = 0; },
  });
  Object.defineProperty(n, 'innerText', {
    get() { return this.textContent; },
    set(v) { this.textContent = v; },
  });
  return n;
}
global.document = {
  // getElementById 必须遍历真实树：产品代码只把弹窗 appendChild 到 body，从不登记 id 表，
  // 只查 store 的桩会让「弹窗根本没建起来」和「建好了但找不到」长得一模一样。
  getElementById(id) {
    if (store[id]) return store[id];
    const walk = (node) => {
      for (const c of node.children || []) {
        if (c.id === id) return c;
        const hit = walk(c);
        if (hit) return hit;
      }
      return null;
    };
    return walk(global.document.body);
  },
  createElement(t) { return El(t); },
  createTextNode(t) { return { nodeType: 3, textContent: String(t), tagName: '#text' }; },
  body: El('body'),
  activeElement: null,
};
// createElement 出来的节点默认没有 children 数组（只有 El() 造的有），文本遍历必须先补齐，
// 否则 nameLine 那类「appendChild 之后再改 textContent」的结构会整个从 dump 里消失。
global.document.body.appendChild(El('placeholder'));
global.window = global;
global.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
global.fetch = async (url) => ({
  ok: true,
  json: async () => (String(url).includes('delete-preview')
    ? { status: 'success', data: { id: 9, paper_name: '测试卷', question_count: 19 } }
    : { status: 'success', data: [] }),
});
global.escapeHtml = (s) => String(s);
global.showToast = () => {};
global.MathBankModal = { open() {}, close() {} };
global.confirm = () => { throw new Error('BARE_CONFIRM_USED'); };

__HELPER__

// paper.js 整体包在一个 IIFE 里，closeDeletePaperConfirm / runDeletePaper 是私有的，
// 只能由抽出来的这段本体自己导出到 window 上（与本项目其余前端可执行回归同做法）。
window.__deletePaperTestApi = { closeDeletePaperConfirm };

(async () => {
  await window.openDeletePaperConfirm(9);
  await new Promise((r) => setTimeout(r, 30));
  const modal = document.getElementById('deletePaperConfirmModal');
  if (!modal) { console.log('MODAL_MISSING'); process.exit(1); }
  const dump = [];
  // 桩把文本存在 _text 上，而真实 DOM 是「有 children 就读不到 textContent」——两种形态都
  // 要收：先收自己的 _text（叶子/被赋值过的节点），再递归 children（appendChild 组装的节点）。
  const walk = (n) => {
    if (n.nodeType === 3) { dump.push(n.textContent); return; }
    dump.push('<' + (n.tagName || '?') + '>');
    if (n._text) dump.push(n._text);
    (n.children || []).forEach(walk);
  };
  walk(modal);
  const text = dump.join('');
  if (!text.includes('包含 19 道题')) { console.log('COUNT_NOT_SHOWN::' + text); process.exit(1); }
  if (!text.includes('测试卷')) { console.log('NAME_NOT_SHOWN::' + text); process.exit(1); }
  if (!dump.some((t) => t === '<STRONG>')) { console.log('NO_STRONG::' + text); process.exit(1); }
  if (text.includes('**')) { console.log('MARKDOWN_LEAK::' + text); process.exit(1); }
  console.log('DELETE_MODAL_OK');
})();
""".replace("__HELPER__", helper)

    result = _run_node_script(script)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DELETE_MODAL_OK" in result.stdout, result.stdout + result.stderr


IMPORT_JS_FOR_SOURCE = STATIC_JS_DIR / "import.js"

_SOURCE_STUB_PRELUDE = r"""
class El {
  constructor(tag, id) {
    this.tagName = String(tag || 'DIV').toUpperCase();
    this._id = ''; this.nodes = []; this._text = ''; this.attrs = {};
    this.listeners = {}; this.hidden = false; this.parentNode = null;
    this.className = ''; this.style = {}; this._value = '';
    if (id) this.id = id;
  }
  get id() { return this._id; }
  set id(v) { this._id = v; if (v) registry[v] = this; }
  get children() { return this.nodes; }
  get nextSibling() {
    if (!this.parentNode) return null;
    const i = this.parentNode.nodes.indexOf(this);
    return i >= 0 ? (this.parentNode.nodes[i + 1] || null) : null;
  }
  get value() { return this._value; }
  set value(v) { this._value = v === null || v === undefined ? '' : String(v); }
  get textContent() {
    return this.nodes.length ? this.nodes.map(function (n) { return n.textContent || ''; }).join('') : this._text;
  }
  set textContent(v) { this._text = String(v); this.nodes = []; }
  set innerHTML(v) { throw new Error('来源提示节点不得使用 innerHTML'); }
  appendChild(n) { this.nodes.push(n); n.parentNode = this; return n; }
  insertBefore(n, ref) {
    if (!n) return n;
    const i = ref ? this.nodes.indexOf(ref) : -1;
    if (i < 0) this.nodes.push(n); else this.nodes.splice(i, 0, n);
    n.parentNode = this; return n;
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); }
  dispatch(t) { (this.listeners[t] || []).forEach(function (fn) { fn(); }); }
  focus() {}
  querySelector() { return null; }
  querySelectorAll() { return []; }
}
const registry = {};
function el(tag, id) { const e = new El(tag, id); if (id) registry[id] = e; return e; }
// 拆卷来源七字段的宿主：每个输入框单独一个 div，警告必须插在输入框同一个宿主里。
const host = el('DIV');
[
  'importSrcYear', 'importSrcRegion', 'importSrcSemester',
  'importSrcExamType', 'importApplicableGrade', 'importPaperTitle',
].forEach(function (id) { const box = el('DIV'); box.appendChild(el('INPUT', id)); host.appendChild(box); });
el('SELECT', 'importSrcProvince');
el('SELECT', 'importSrcCity');
globalThis.document = {
  getElementById: function (id) { return registry[id] || null; },
  createElement: function (tag) { return new El(tag); },
  body: el('BODY'),
};
globalThis.window = globalThis;
window._schoolsDict = [];
window._regionsTree = [];
window._displayConfig = null;
window.showToast = function () {};
function fillCitySelect() {}
function refreshSchoolDatalist() {}
function tryMatchSchoolToGeo() {}
function applyStandardPaperTitle() {}
function loadSchoolDict() {}
"""


def _extract_import_source(source: str, start_token: str, end_token: str) -> str:
    start = source.index(start_token)
    end = source.index(end_token, start)
    return source[start:end]


def test_filename_prefill_regexes_are_executable_and_widened():
    """#10 文件名解析：三条正则放宽后必须真跑 node 命中样例，且不许瞎猜。

    旧实现要求「2023-2024学年」带「学年」二字，`东北师大附中2023-2024高一上学期…`
    这类写法学年度对恒为 null；考试类型只捞到裸「月考」，序数与月份全丢。
    """

    import_source = _read(IMPORT_JS_FOR_SOURCE)
    helper = _extract_import_source(
        import_source, "function prefillSourceFromFilename", "function buildStandardPaperTitle")

    for token in ("(?:次|轮)?月考", "(?:半?学期|学期)?", "(?:学年)?"):
        assert token in helper, token

    script = _SOURCE_STUB_PRELUDE + "\n" + helper + r"""
const CASES = [
  ['东北师大附中2023-2024高一上学期9月月考试题.pdf',
    { yearPair: '2023|2024', year: '2023', grade: 'g1', sem: '上', exam: '9月月考', school: '东北师大附中' }],
  ['哈尔滨三中2024届高三上学期第一次月考试卷.pdf',
    { yearPair: '', year: '2024', grade: 'g3', sem: '上', exam: '第一次月考' }],
  ['某某中学10月月考.docx',
    { yearPair: '', year: '', grade: '', sem: '', exam: '10月月考' }],
  ['实验中学9月第一次月考物理试卷.pdf',
    { exam: '9月月考' }],
  ['第一中学2024-2025高二下学期期末检测.docx',
    { yearPair: '2024|2025', year: '2025', grade: 'g2', sem: '下', exam: '期末' }],
  ['哈六中高三第一轮复习资料.pdf', { exam: '' }],
  ['普通题目.pdf', { yearPair: '', year: '', grade: '', sem: '', exam: '', school: '' }],
];
window._schoolsDict = [
  { name: '东北师大附中', short_name: '东北师大附中', full_name: '东北师范大学附属中学', province: '吉林省', city: '长春市' },
  { name: '哈六中', short_name: '哈六中', full_name: '哈尔滨市第六中学校', province: '黑龙江省', city: '哈尔滨市' },
];
const fails = [];
for (const [file, want] of CASES) {
  ['importSrcYear', 'importSrcRegion', 'importSrcSemester', 'importSrcExamType', 'importApplicableGrade']
    .forEach((id) => { document.getElementById(id).value = ''; });
  window._srcYearPair = null;
  prefillSourceFromFilename(file);
  const got = {
    yearPair: window._srcYearPair ? window._srcYearPair.join('|') : '',
    year: document.getElementById('importSrcYear').value,
    grade: document.getElementById('importApplicableGrade').value,
    sem: document.getElementById('importSrcSemester').value,
    exam: document.getElementById('importSrcExamType').value,
    school: document.getElementById('importSrcRegion').value,
  };
  for (const key of Object.keys(want)) {
    if (String(got[key]) !== String(want[key])) {
      fails.push(file + ' [' + key + '] 期望 ' + JSON.stringify(want[key]) + ' 实得 ' + JSON.stringify(got[key]));
    }
  }
}
if (fails.length) { console.log('FAILS::' + fails.join(' ;; ')); process.exit(1); }
console.log('PREFILL_OK');
"""

    result = _run_node_script(script)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PREFILL_OK" in result.stdout, result.stdout + result.stderr


def test_filename_prefill_covers_real_world_grade_exam_and_school_forms():
    """真实文件名摸底的四类缺口：年级带「年级」、长尾卷名、联考优先级、学校别名/省域。

    旧实现的四个静默失败：
    1) 「高一年级上学期」因正则要求「高一」后直接跟「上/下」而年级学期双双留空；
    2) 「第二次阶段性考试」「综合素质检测」不在词表，考试类型留空；
    3) 词表里「期中」排在「联考」之前，「11月期中联考」被读成期中；
    4) 学校只比对 name：「哈师大附中」对不上词库名「哈师附」，而「省实验」在
       吉林/黑龙江各有一所，裸最长匹配按词库顺序把黑龙江卷归到吉林。
    """

    import_source = _read(IMPORT_JS_FOR_SOURCE)
    helper = _extract_import_source(
        import_source, "function prefillSourceFromFilename", "function buildStandardPaperTitle")
    # 助手必须落在同一段提取范围内，否则测试替身里根本不存在这些函数。
    for fn in ("function schoolMatchKeys(", "function detectSourceGeoContext(",
               "function matchSchoolFromFilename("):
        assert fn in helper, fn

    script = _SOURCE_STUB_PRELUDE + "\n" + helper + r"""
window._schoolsDict = [
  { name: '哈师附', short_name: '哈师附', full_name: '哈尔滨师范大学附属中学', province: '黑龙江省', city: '哈尔滨市' },
  { name: '东北师大附中', short_name: '东北师大附中', full_name: '东北师范大学附属中学', province: '吉林省', city: '长春市' },
  { name: '省实验', short_name: '省实验', full_name: '吉林省实验中学', province: '吉林省', city: '长春市' },
  { name: '省实验', short_name: '省实验', full_name: '黑龙江省实验中学', province: '黑龙江省', city: '哈尔滨市' },
  { name: '德强', short_name: '德强', full_name: '哈尔滨德强高级中学', province: '黑龙江省', city: '哈尔滨市' },
];

const CASES = [
  ['2024年哈师大附中高一年级上学期10月月考.pdf',
    { grade: 'g1', sem: '上', exam: '10月月考', school: '哈师附', province: '黑龙江省', city: '哈尔滨市' }],
  ['2024年东北师大附中高三上学期第二次阶段性考试.pdf',
    { grade: 'g3', sem: '上', exam: '第二次阶段性考试', school: '东北师大附中', province: '吉林省', city: '长春市' }],
  ['2024年吉林省省实验高二下学期综合素质检测.pdf',
    { grade: 'g2', sem: '下', exam: '综合素质检测', school: '省实验', province: '吉林省', city: '长春市' }],
  // 联考优先于期中，且保留月份。
  ['2024年德强高中高一上学期11月期中联考.pdf',
    { grade: 'g1', sem: '上', exam: '11月联考', school: '德强', province: '黑龙江省', city: '哈尔滨市' }],
  ['2024年高三上学期期中联考.pdf', { exam: '联考' }],
  // 全称直接命中：黑龙江的「省实验」必须压过吉林的同名简称。
  ['2024年黑龙江省实验中学高一上学期期中考试.pdf',
    { school: '省实验', province: '黑龙江省', city: '哈尔滨市' }],
  // 文件名写了省、但从头到尾只出现简称：必须强制同省。
  ['2024年黑龙江省省实验高一上学期期中考试.pdf',
    { school: '省实验', province: '黑龙江省', city: '哈尔滨市' }],
  // 只写「师大附中」没有城市缩写：按省域在词库里召回唯一候选。
  ['2024年黑龙江省师大附中高一上学期期中考试.pdf',
    { school: '哈师附', province: '黑龙江省', city: '哈尔滨市' }],
  // 没有任何省/市线索的两所「省实验」→ 不猜，留给用户手选。
  ['2024年省实验高一上学期期中考试.pdf', { school: '', province: '', city: '' }],
];

const FIELDS = {
  year: 'importSrcYear', grade: 'importApplicableGrade', sem: 'importSrcSemester',
  exam: 'importSrcExamType', school: 'importSrcRegion', province: 'importSrcProvince',
  city: 'importSrcCity',
};
const fails = [];
for (const [file, want] of CASES) {
  Object.keys(FIELDS).forEach((k) => { document.getElementById(FIELDS[k]).value = ''; });
  window._srcYearPair = null;
  prefillSourceFromFilename(file);
  for (const key of Object.keys(want)) {
    const got = String(document.getElementById(FIELDS[key]).value);
    if (got !== String(want[key])) {
      fails.push(file + ' [' + key + '] 期望 ' + JSON.stringify(want[key]) + ' 实得 ' + JSON.stringify(got));
    }
  }
}
if (fails.length) { console.log('FAILS::' + fails.join(' ;; ')); process.exit(1); }
console.log('REAL_FILENAME_OK');
"""

    result = _run_node_script(script)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "REAL_FILENAME_OK" in result.stdout, result.stdout + result.stderr


def test_new_file_drop_clears_previous_source_fields_before_prefill():
    """拖入新文件必须覆盖上一份文件的来源数据，而不是被「已有值」守卫留下。

    实测 Bug：先后拖入两份试卷时，年份/学校/省市区/考试类型仍是第一份文件的；
    prefill 的每一处赋值都带 `!el.value` 守卫，旧值因此永远不被覆盖。
    """

    import_source = _read(IMPORT_JS_FOR_SOURCE)
    helper = _extract_import_source(
        import_source, "let _sourcePrefillGeneration", "function buildStandardPaperTitle")
    handler = _extract_import_source(
        import_source, "function handleTexFileSelect", "// 旧版独立配图区在部分页面布局中不存在")

    # 重置必须发生在分支判断之前，否则 docx/pdf/markdown 各自预填时旧值还在。
    assert "resetImportSourceFields();" in handler
    assert handler.index("resetImportSourceFields();") < handler.index("if (lowerFileName.endsWith('.docx'))")
    assert "let _sourcePrefillGeneration" in helper

    script = _SOURCE_STUB_PRELUDE + "\n" + helper + "\n" + handler + r"""
const latexTextarea = new El('TEXTAREA');
const texFileName = new El('SPAN');
const texFileIcon = new El('I');
const texInput = new El('INPUT');
const texImagesSection = el('DIV', 'texImagesSection');
el('DIV', 'pdfPageRangeContainer');
El.prototype.classList = { add() {}, remove() {}, toggle() {}, contains() { return false; } };
window._schoolsDict = [
  { name: '德强', full_name: '哈尔滨德强高级中学', short_name: '德强', province: '黑龙江省', city: '哈尔滨市' },
  { name: '大庆实验', full_name: '大庆实验中学', short_name: '大庆实验', province: '黑龙江省', city: '大庆市' },
];
window._regionsTree = [{ province: '黑龙江省', cities: ['哈尔滨市', '大庆市'] }];

const FIELDS = {
  year: 'importSrcYear', grade: 'importApplicableGrade', sem: 'importSrcSemester',
  exam: 'importSrcExamType', school: 'importSrcRegion', province: 'importSrcProvince',
  city: 'importSrcCity', title: 'importPaperTitle',
};
// 上一份文件留下的脏数据：新文件解析失败时这些值最容易被原样保留。
document.getElementById('importSrcYear').value = '2021';
document.getElementById('importApplicableGrade').value = 'g2';
document.getElementById('importSrcSemester').value = '下';
document.getElementById('importSrcExamType').value = '期末';
document.getElementById('importSrcRegion').value = '老学校';
document.getElementById('importSrcProvince').value = '吉林省';
document.getElementById('importSrcCity').value = '长春市';
document.getElementById('importPaperTitle').value = '旧试卷标题';
window._srcYearPair = ['2021', '2022'];
window._lastSrcProvince = '吉林省';
window._lastSrcCity = '长春市';

const fails = [];
function check(label, want) {
  const got = String(document.getElementById(FIELDS[label]).value);
  if (got !== String(want)) fails.push(label + ' 期望 ' + JSON.stringify(want) + ' 实得 ' + JSON.stringify(got));
}

handleTexFileSelect({ name: '德强高中2022—2023学年度高一上学期11月月考+答案.pdf' });
check('year', '2022'); check('grade', 'g1'); check('sem', '上');
check('exam', '11月月考'); check('school', '德强');
check('province', '黑龙江省'); check('city', '哈尔滨市');
check('title', '德强高中2022—2023学年度高一上学期11月月考+答案');
if (String(window._srcYearPair) !== '2022,2023') fails.push('yearPair 未随新文件更新: ' + window._srcYearPair);

// 第二次拖入：必须在上一份的解析结果之上整体覆盖，而不是叠加。
handleTexFileSelect({ name: '【数学】大庆实验中学2024-2025学年高一上学期期中考试试卷（含答案解析）.pdf' });
check('year', '2024'); check('grade', 'g1'); check('sem', '上');
check('exam', '期中'); check('school', '大庆实验');
check('province', '黑龙江省'); check('city', '大庆市');
check('title', '【数学】大庆实验中学2024-2025学年高一上学期期中考试试卷（含答案解析）');
if (window._lastSrcCity !== '大庆市') fails.push('_lastSrcCity 未更新: ' + window._lastSrcCity);
if (fails.length) { console.log('FAILS::' + fails.join(' ;; ')); process.exit(1); }
console.log('RESET_OK');
"""

    result = _run_node_script(script)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RESET_OK" in result.stdout, result.stdout + result.stderr


def test_school_soft_warning_is_non_blocking_and_dismisses_on_match():
    """#13 学校软警告：词库外提示、命中或清空即消失，且绝不清空用户输入。"""

    import_source = _read(IMPORT_JS_FOR_SOURCE)
    helper = _extract_import_source(
        import_source, "const SCHOOL_ALIAS_KEYS", "function initSourceSmartFields")
    assert "innerHTML" not in helper, "警告节点必须 createElement + textContent"

    script = _SOURCE_STUB_PRELUDE + "\n" + helper + r"""
window._schoolsDict = [
  { name: '哈三中', short_name: '哈三中', full_name: '哈尔滨市第三中学校' },
  { name: '省实验', short_name: '省实验', full_name: '吉林省实验中学' },
];
const region = document.getElementById('importSrcRegion');
const warn = document.getElementById('importSrcRegionWarn');
const fails = [];
function check(label, expectShown) {
  updateSchoolSoftWarning();
  const w = document.getElementById('importSrcRegionWarn');
  if (!w) { fails.push(label + ': 警告节点没建出来'); return; }
  if (w.hidden === expectShown) { fails.push(label + ': hidden=' + w.hidden + ' 期望相反'); }
  if (expectShown && !w.textContent.includes('未找到该学校')) { fails.push(label + ': 文案缺失'); }
  if (!expectShown && w.textContent !== '') { fails.push(label + ': 应清空却留着 ' + w.textContent); }
}
check('in-library', false);          // 空值不该提示
region.value = '哈三中';            check('in-library', false);
region.value = '哈尔滨市第三中学校'; check('full-name', false);
region.value = '不存在学校X';        check('off-library', true);
if (region.value !== '不存在学校X') { fails.push('软警告把用户输入改掉了: ' + region.value); }
region.value = '';                   check('empty-dismiss', false);
// 词库还没加载完时不得误报，否则首屏每个已填值都会闪一条假警告
window._schoolsDict = [];
region.value = '任意手输';           check('dict-not-ready', false);
if (fails.length) { console.log('FAILS::' + fails.join(' ;; ')); process.exit(1); }
console.log('SOFT_WARN_OK');
"""

    result = _run_node_script(script)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SOFT_WARN_OK" in result.stdout, result.stdout + result.stderr


def test_school_soft_warning_is_wired_to_input_events():
    """接线检查：input/change 都必须调用软警告，漏一处就是「函数写了但没人调」。"""

    import_source = _read(IMPORT_JS_FOR_SOURCE)
    start = import_source.index("regionEl.addEventListener('input'")
    end = import_source.index("const titleElForWarn", start)
    wiring = import_source[start:end]

    input_block = wiring[:wiring.index("});")]
    assert "updateSchoolSoftWarning()" in input_block, "input 事件没触发软警告——首屏打字不提示"
    change_line = [ln for ln in wiring.splitlines() if "addEventListener('change'" in ln]
    assert change_line, "找不到学校输入框的 change 监听"
    assert "updateSchoolSoftWarning()" in change_line[0], "change 事件没触发软警告——选完候选黄字不消失"


def test_api_vendor_grid_top_aligns_and_never_collapses_to_zero_width():
    """API 厂商配置行：三列控件必须顶对齐，且列宽不得有 0px 下限。

    实测踩过的两个坑（都是静态文本断言测不出、只有量 getBoundingClientRect 才发现）：
    1) 旧 class 用 items-end（底对齐）。厂商名列下面那行灰色提示只加高自己那一列，
       于是同排的 API Key / 接口地址被整体推下去 25px；带提示的 4 行和不带提示的
       2 行还不一致。改 items-start 后每行五列 top 完全相等。
    2) 旧模板是 minmax(0, 2fr)，允许轨道压到 0px。窄容器里 label 文字竖排换行、
       把列高撑成 45~60px（349px 视口实测 grid-template-columns = 96px 0px 0px 41px 44px）。
       现在列宽下限必须是非零绝对值。
    """

    html = _read(INDEX_PATH)
    css = _read(CSS_PATH)

    # 渲染结构：12 处内联任意值全部换成语义类，且不得残留旧的塌陷模板
    assert "minmax(6rem,1fr)_minmax(0,2fr)" not in html, (
        "厂商行不得再用 minmax(0, Nfr)：0 下限会让列在窄容器里塌成 0px"
    )
    assert html.count('class="api-vendor-grid"') == 6, "6 个固定厂商控件行必须都用统一类"

    rule_start = css.index(".api-vendor-grid {")
    rule = css[rule_start:css.index("}", rule_start)]
    assert "align-items: start" in rule, "厂商行必须顶对齐，否则灰字提示会把兄弟列推下去"
    assert "items-end" not in rule
    for token in ("minmax(8rem, 1fr)", "minmax(11rem, 2fr)"):
        assert token in rule, f"列宽下限缺失: {token}"
    assert "minmax(0," not in rule, "列宽下限不得为 0"

    # 按钮与输入框等高 + 下移一个 label 占位：二者缺一都会露出「按钮比输入框矮/高一线」
    btn_start = css.index(".api-vendor-grid > a,")
    btn_block = css[btn_start:btn_start + 700]
    assert "height: 2.75rem" in btn_block, "获取 Key(a)/测试连接(button) 必须与 select/input 等高"
    assert "margin-top: 17px" in btn_block, (
        "按钮列没有 label，必须补 label 占位才能与输入框本体齐平（实测值，非理论 16.3px）"
    )
    assert "padding-top" not in btn_block, (
        "padding-top 会被按钮自带的 inline-flex + items-center 重新居中掉，实测无效"
    )


def test_api_vendor_buttons_keep_natural_width():
    """按钮宽度必须由内容自身决定，不得为了对齐而拉伸成砖头。

    上一版为了让控件行与结果行的第 4/5 列等宽，把它们写成定宽 4.5rem/5.5rem，
    结果「获取 Key」「测试连接」被拉满整列（实测 72px/88px），右侧还留大片空白，
    用户直接抗议。现在两行合进同一个网格容器，auto 列天然共享同一组宽度，
    按钮回到内容自适应的紧凑宽度（实测窄屏 41/44px、宽屏 63/62px）。
    """

    css = _read(CSS_PATH)
    rule_start = css.index(".api-vendor-grid {")
    rule = css[rule_start:css.index("}", rule_start)]

    assert "4.5rem" not in rule and "5.5rem" not in rule, (
        "按钮两列不得写死宽度：那会把按钮拉成砖头"
    )
    cols_line = rule[rule.index("grid-template-columns"):]
    tail = cols_line.split(";", 1)[0]
    assert tail.count("auto") == 2, "最后两列必须是 auto（由内容取宽，并与同行其他单元共享轨道）"
    assert "justify-self: end" in css, "按钮靠右贴列缘，不被拉伸填充"


def test_api_vendor_result_shares_the_control_grid():
    """测试结果必须与控件在同一个网格容器里，否则跨容器列宽必然错位。

    分两个容器时（各写自己的 grid-template-columns），CSS Grid 没有任何跨容器轨道
    对齐保证：控件行有按钮所以 auto 列算出 63/62px，结果行没按钮就算出 0px，
    省下的空间被前面的 fr 吸收——实测宽屏下结果文案比「接口地址」右偏 75px。
    中途还试过给结果行补空 <div> 占位格：空 div 撑不起 auto 轨道，同样失败。
    唯一稳的做法是合成一个容器，再显式把结果钉在第 3 列。
    """

    html = _read(INDEX_PATH)
    css = _read(CSS_PATH)

    assert "api-vendor-result-row" not in html, (
        "结果行不得再独立成一个网格容器"
    )
    assert html.count('data-result-slot="3"') == 6, "6 个测试结果都要带落位标记"

    slot_start = css.index('.api-vendor-grid > [data-result-slot="3"]')
    slot_rule = css[slot_start:css.index("}", slot_start)]
    assert "grid-column: 3" in slot_rule, "结果文案必须显式落在第 3 列，自动落位会排到第 1 列"

    # 每个厂商块内：控件网格闭合前必须已经包含该块的 <p data-result-slot>
    blocks = re.findall(
        r'<div class="api-vendor-grid">.*?</div>\s*(?=<!--|\r\n\s*<div class="api-key-row|$)',
        html, re.S)
    with_slot = sum(1 for b in blocks if 'data-result-slot="3"' in b)
    assert with_slot == 6, f"仅 {with_slot}/6 个结果文案在控件网格内部"

def test_custom_vendor_modal_clears_opacity_so_it_cannot_swallow_clicks():
    """「点添加厂商卡死」的根因守卫：弹窗必须真的可见，否则透明全屏层会吃掉所有点击。
 
    这里没有任何死循环。openCustomVendorModal() 原本只做两件事——remove('hidden')
    和 MathBankModal.open()，而 #customVendorModal 的根节点 class 里带着
    `opacity-0 transition-opacity`，谁都没把它清掉。于是弹窗以 opacity:0 的形态
    铺满整屏（fixed inset-0 z-50），同时 MathBankModal 把 header/main 置 inert：
    屏幕上什么都看不见，但每一次点击都落在这层透明遮罩上，背景又全被 inert 封住，
    用户看到的就是「页面瞬间死锁」。现在开关都收敛到 api.js 的公共助手，守卫
    同时钉住「调用点」与「助手本体」两头——历史上漏的正是只钉一头挡不住的形态。
 
    揭示必须带 setTimeout 兜底：后台标签页/无头环境里 rAF 会被冻结（实测本环境
    rafFired=false），只挂在 rAF 上的话弹窗永远显示不出来。
    """
 
    js = _read(STATIC_JS_DIR / "api.js")
 
    start = js.index("function openCustomVendorModal()")
    body = js[start:js.index("function submitCustomVendor()", start)]
    assert "window.openModalWithTransition('customVendorModal'" in body, (
        "openCustomVendorModal 必须走公共开关助手，否则又留下透明全屏拦截层吞整页点击"
    )
 
    close_start = js.index("function closeCustomVendorModal()")
    close_body = js[close_start:js.index("function submitCustomVendor()", close_start)]
    assert "window.closeModalWithTransition('customVendorModal')" in close_body, (
        "closeCustomVendorModal 必须走公共开关助手，复位 opacity-0 与 hidden"
    )
 
    reveal_start = js.index("function revealModalWithTransition(modal)")
    reveal_body = js[reveal_start:js.index("function openModalWithTransition", reveal_start)]
    assert "classList.remove('opacity-0')" in reveal_body, (
        "reveal 助手必须真的移除 opacity-0"
    )
    assert "setTimeout(show" in reveal_body, (
        "揭示动作必须有 setTimeout 兜底：rAF 在后台标签页被冻结时不会执行"
    )
 
    close_helper_start = js.index("function closeModalWithTransition(")
    close_helper_body = js[close_helper_start:js.index("window.revealModalWithTransition", close_helper_start)]
    assert "classList.add('opacity-0')" in close_helper_body, (
        "关闭时必须复位 opacity-0，否则下次打开没有淡入过渡、且状态不一致"
    )
    assert "classList.add('hidden')" in close_helper_body, (
        "关闭时必须立刻补回 hidden，不留「看不见但吞点击」的淡出窗口期"
    )
 
    # 弹窗根节点确实带 opacity-0，这条前提不成立的话上面的守卫就是空转
    html = _read(INDEX_PATH)
    root = html[html.index('<div id="customVendorModal"'):html.index("\n", html.index('<div id="customVendorModal"'))]
    assert "opacity-0" in root, "customVendorModal 根节点应带 opacity-0（守卫前提）"
 
 
def test_all_static_opacity_overlays_open_through_the_transition_helpers():
    """全站守卫：index.html 里每个「fixed inset-0 + opacity-0」弹窗都必须经公共助手开关。
 
    「点添加厂商卡死」事故证明：只要存在"只清 hidden、不清 opacity-0"的弹窗，
    就一定会出现吞掉整页点击、看起来像死机的页面。这个测试是登记表——新增
    此类弹窗必须同步登记；绕过助手或漏登记，直接红。
    """
    html = _read(INDEX_PATH)
    js_by_file = {name: _read(STATIC_JS_DIR / name)
                  for name in ("api.js", "editor.js", "import.js", "ocr.js",
                               "entry_options.js", "paper.js")}
 
    expectations = {
        "settingsModal": ("api.js", "openModalWithTransition(modal"),
        "customVendorModal": ("api.js", "openModalWithTransition('customVendorModal'"),
        "updateModal": ("api.js", "openModalWithTransition(modal"),
        "statsModal": ("editor.js", "openModalWithTransition(modal"),
        "scanDuplicatesModal": ("editor.js", "openModalWithTransition(modal"),
        "aiClassifyModal": ("import.js", "openModalWithTransition('aiClassifyModal'"),
        "pdfCropModal": ("import.js", "openModalWithTransition('pdfCropModal'"),
        "optionCropModal": ("entry_options.js", "openModalWithTransition('optionCropModal'"),
        "parsedDuplicateReviewModal": ("import.js", "openModalWithTransition(modal"),
        "answerTikzWorkbenchModal": ("import.js", "openModalWithTransition(modal"),
        "displayOrderModal": ("import.js", "revealModalWithTransition(modal"),
        "imageLightbox": ("ocr.js", "openModalWithTransition(lightbox"),
        "pandocInstallModal": ("paper.js", "revealModalWithTransition(modal"),
    }
 
    for modal_id, (file_name, needle) in expectations.items():
        marker = '<div id="%s"' % modal_id
        assert marker in html, "弹窗 %s 不存在，登记表已过期" % modal_id
        root = html[html.index(marker):html.index("\n", html.index(marker))]
        assert "fixed inset-0" in root and "opacity-0" in root, (
            "%s 根节点应为 fixed inset-0 + opacity-0（守卫前提）" % modal_id
        )
        assert needle in js_by_file[file_name], (
            "%s 的打开链路没走公共开关助手（%s 应出现在 %s 中）" % (modal_id, needle, file_name)
        )
 
    # 运行时挂载的动态全屏层同样登记（mergeSource 并入层 / missingCompulsory 确认框）
    assert "openModalWithTransition(modal, { onEscape: () => closeMergeSourceModal(modal) })" in js_by_file["import.js"]
    assert "openModalWithTransition(modalDiv" in js_by_file["editor.js"]
 
    # 级联加载的下游模块都依赖 window 导出，缺一个就 ReferenceError 雪崩
    for export_name in ("revealModalWithTransition", "openModalWithTransition", "closeModalWithTransition"):
        assert ("window." + export_name + " =") in js_by_file["api.js"]
 
    # 自动发现守卫：markup 里每一个「fixed inset-0 + opacity-0」浮层都必须在登记表内。
    # 将来新增透明全屏层却漏登记 = 整页假死事故重演，这里直接判失败，不给侥幸机会。
    discovered = set()
    for line in html.splitlines():
        if "fixed inset-0" not in line or "opacity-0" not in line:
            continue
        found = re.search(r'id="([^"]+)"', line)
        if found:
            discovered.add(found.group(1))
    registered = set(expectations)
    assert discovered == registered, (
        "透明全屏浮层与登记表不一致 漏登记 %s / 已下线 %s"
        % (sorted(discovered - registered), sorted(registered - discovered))
    )


# ==================== #32 单题录入重构：P0 素材通道 / P2 选项卡 / P3 切图 ====================

_OPTION_CARD_PRELUDE = """
const fs = require('fs');
const source = fs.readFileSync('static/js/entry_options.js', 'utf8');
const noop = () => {};
const win = {};
const doc = {
  readyState: 'complete',
  getElementById: () => null,
  querySelectorAll: () => [],
  addEventListener: noop,
};
new Function('window', 'document', 'URL', 'fetch', 'Image', 'File', 'FormData', 'Event', source)(
  win, doc, function URL() {}, function fetch() {}, function Image() {},
  function File() {}, function FormData() {}, function Event() {}
);
const api = win.OptionCardInternals;
if (!api) { console.log('FAIL: 选项卡没有导出 OptionCardInternals'); process.exit(1); }
function assertOk(condition, message) {
  if (!condition) { console.log('FAIL: ' + message); process.exit(1); }
}
"""


def _run_option_card_script(body: str) -> str:
    """在假 DOM 里加载 entry_options.js，直接驱动它导出的纯逻辑。"""
    result = _run_node_script(_OPTION_CARD_PRELUDE + body)
    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")
    return result.stdout or ""


def test_single_screenshot_media_channel_replaces_the_four_dropzones():
    """素材通道：四个长得一模一样的截图区合并成一个入口 + 目的地选择。"""
    index_source = _read(INDEX_PATH)
    ocr_source = _read(STATIC_JS_DIR / "ocr.js")
    retired_ids = (
        "contentOcrDropZone",
        "illustrationDropZone",
        "ocrDropZone",
        "imageAnswerDropZone",
        "contentOcrFileInput",
        "imageAnswerFileInput",
    )

    for retired_id in retired_ids:
        assert 'id="%s"' % retired_id not in index_source, "旧截图区还在页面上：%s" % retired_id
        assert retired_id not in ocr_source, "ocr.js 仍引用已删除的 %s" % retired_id

    elements = _index_elements()
    for element_id in (
        "mediaChannelPanel",
        "mediaTargetGroup",
        "mediaTargetHint",
        "mediaEngineHint",
        "mediaDropZone",
        "mediaFileInput",
        "mediaPreviewContainer",
        "mediaStatusBadge",
        "mediaOpenCropBtn",
    ):
        assert element_id in elements, "素材通道缺少 %s" % element_id
    assert elements["mediaDropZone"]["tabindex"] == "0", "唯一截图入口必须可 Tab 聚焦"
    assert elements["mediaFileInput"]["accept"] == "image/*"
    assert "multiple" in elements["mediaFileInput"], "图片作答需要一次多张"

    targets = re.findall(r'data-media-target="([a-z-]+)"[^>]*aria-pressed="(\w+)"', index_source)
    assert [name for name, _ in targets] == ["stem-ocr", "stem-image", "answer-ocr", "answer-image"]
    assert [pressed for _, pressed in targets].count("true") == 1, "目的地默认选中项必须唯一"

    for marker in (
        "function currentMediaTarget()",
        "function applyMediaTarget(target)",
        "function submitMediaFiles(files)",
        "renderMediaPreview(file)",
        "'mediaChannelTarget'",
    ):
        assert marker in ocr_source

    # 双触发守卫：焦点已在编辑框/通道里时，全局粘贴路由必须让位给元素自身监听器
    assert "closest('#editContent, #editAnswerMarkdown, #mediaDropZone')" in ocr_source
    # 编辑框仍是直达入口：拖图进题干=题干 OCR，进解析=解析 OCR
    assert "setupDragDropListeners(contentEditorEl, (file) => runContentOcr(file));" in ocr_source
    assert "setupDragDropListeners(answerEditorEl, (file) => runOcr(file));" in ocr_source


def test_global_skip_tikz_switch_migrates_legacy_preferences():
    """两枚「禁用 TikZ 绘图」合并成一枚，旧偏好任一为真时合并后仍保持禁用。"""
    index_source = _read(INDEX_PATH)
    ocr_source = _read(STATIC_JS_DIR / "ocr.js")

    assert 'id="globalSkipTikz"' in index_source
    for retired in ("contentOcrSkipTikz", "answerOcrSkipTikz"):
        assert 'id="%s"' % retired not in index_source

    assert "const legacyKeys = ['contentOcrSkipTikz', 'answerOcrSkipTikz'];" in ocr_source
    assert "localStorage.removeItem(key)" in ocr_source
    assert "'skipTikzDisabled'" in ocr_source
    assert "function isTikzGenerationDisabled()" in ocr_source
    assert ocr_source.count("const skipTikz = isTikzGenerationDisabled();") == 2
    assert ocr_source.count("formData.append('skip_tikz', skipTikz ?") == 2


def test_batch_pipeline_now_lives_in_the_paper_import_workspace():
    """第四方案·批量从单题解析面板搬回试卷录入工作台，用模式按钮切换。"""
    index_source = _read(INDEX_PATH)

    positions = tuple(
        index_source.index(marker)
        for marker in (
            'id="paperImportWorkspace"',
            'id="paperImportModeBar"',
            'id="batchImportPane"',
            'id="batchImportPanel"',
            'id="latexImportModal"',
            'id="editorSection"',
        )
    )
    assert positions == tuple(sorted(positions)), "批量面板必须留在试卷录入工作台内、单题编辑区之外"
    assert 'id="tabBtn-batch"' not in index_source
    assert 'id="tabContent-batch"' not in index_source

    batch_source = _read(STATIC_JS_DIR / "import_batch.js")
    assert "window.setPaperImportMode = function (mode)" in batch_source
    assert "$('paperImportModeBatch')" in batch_source
    assert "$('tabBtn-batch')" not in batch_source

    api_source = _read(STATIC_JS_DIR / "api.js")
    assert "window.enterBatchImport = function()" in api_source
    assert "window.setPaperImportMode('batch')" in api_source
    assert "window.setPaperImportMode('single')" in api_source

    import_source = _read(STATIC_JS_DIR / "import.js")
    guard_start = import_source.index("function paperImportWorkspaceActive()")
    guard_end = import_source.index("function ensureGlobalDropOverlay()", guard_start)
    assert "getElementById('batchImportPane')" in import_source[guard_start:guard_end], (
        "批量模式下窗口级拖放必须让路，否则一次拖文件喂两套流水线"
    )

    tab_start = import_source.index("function switchWorkflowTab(tabId)")
    tab_end = import_source.index("function clearEditor()", tab_start)
    tab_source = import_source[tab_start:tab_end]
    assert "'batch'" not in tab_source
    assert "if (btn)" in tab_source and "if (content)" in tab_source, "结果面板已常显，切换函数不能再假设 tab 存在"


def test_option_editor_card_is_wired_into_the_stem_panel():
    """选项卡挂在题干面板里，只对选择题显形，样式全部落在 app.css。"""
    index_source = _read(INDEX_PATH)
    entry_source = _read(STATIC_JS_DIR / "entry_options.js")
    css_source = _read(CSS_PATH)

    assert "/static/js/entry_options.js" in index_source
    positions = tuple(
        index_source.index(marker)
        for marker in (
            'id="questionContentPanel"',
            'id="editContent"',
            'id="optionEditorPanel"',
            'id="contentTabContent-ocr"',
            'id="illustrationsList"',
        )
    )
    assert positions == tuple(sorted(positions)), "选项卡应紧跟题干编辑框、位于识别结果之前"

    elements = _index_elements()
    for element_id in (
        "optionEditorPanel",
        "optionRows",
        "optionAddBtn",
        "optionSplitBtn",
        "optionModeNote",
    ):
        assert element_id in elements, "选项卡缺少 %s" % element_id
    assert "hidden" in elements["optionEditorPanel"]["class"], "非选择题默认不该显示选项卡"

    for selector in (
        ".option-panel",
        ".option-rows",
        ".option-row",
        ".option-label",
        ".option-text",
        ".option-image-chip",
        ".option-icon-btn",
    ):
        assert selector in css_source

    assert "CHOICE_TYPES = ['single_choice', 'multi_choice']" in entry_source
    assert "function isChoiceType()" in entry_source
    assert "$('editQType')" in entry_source


def test_option_editor_card_follows_the_shared_stem_preview_path():
    """载入题目/恢复草稿是程序化赋值，不触发 input：选项卡必须挂在共用预览链路上。"""
    editor_source = _read(STATIC_JS_DIR / "editor.js")
    entry_source = _read(STATIC_JS_DIR / "entry_options.js")
    import_source = _read(STATIC_JS_DIR / "import.js")

    start = editor_source.index("const updateContentPreview = () => {")
    early_return = editor_source.index("const text = editContent.value;", start)
    assert "window.refreshOptionEditorCard" in editor_source[start:early_return], (
        "共用预览链路没带动选项卡：载入已有选择题时卡片会残留上一题的选项"
    )

    hook = entry_source.index("window.refreshOptionEditorCard = function")
    hook_body = entry_source[hook:entry_source.index("};", hook) + 2]
    assert "refreshVisibility()" in hook_body, "程序化设置题型下拉时选项卡不会显形"
    assert "force === true" in hook_body, "默认必须走签名比对，否则在选项行里打字会被重渲染抢焦点"

    for source, label in ((editor_source, "草稿恢复"), (import_source, "题库选题载入")):
        assert "window.updateContentPreview();" in source, "%s 路径未收敛到共用预览" % label


_OPTION_CARD_DOM_PRELUDE = """
const fs = require('fs');
const source = fs.readFileSync('static/js/entry_options.js', 'utf8');
function makeEl(id) {
  const listeners = {};
  const el = {
    id, value: '', className: '', src: '', disabled: false, children: [],
    hiddenTokens: new Set(['hidden']),
    classList: {
      toggle(name, force) {
        if (force === undefined) {
          if (el.hiddenTokens.has(name)) { el.hiddenTokens.delete(name); } else { el.hiddenTokens.add(name); }
        } else if (force) { el.hiddenTokens.add(name); } else { el.hiddenTokens.delete(name); }
      },
      add(name) { el.hiddenTokens.add(name); },
      remove(name) { el.hiddenTokens.delete(name); },
      contains(name) { return el.hiddenTokens.has(name); },
    },
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    dispatchEvent(evt) { (listeners[evt.type] || []).forEach((fn) => fn(evt)); return true; },
    appendChild(node) { el.children.push(node); return node; },
    setAttribute() {}, getAttribute() { return null; },
    querySelector() { return null; }, querySelectorAll() { return []; },
  };
  Object.defineProperty(el, 'textContent', {
    get() { return el._text || ''; },
    set(v) { el._text = v; if (v === '') el.children = []; },
  });
  return el;
}
const registry = {};
['editContent', 'editQType', 'optionEditorPanel', 'optionRows', 'optionModeNote',
  'optionAddBtn', 'optionSplitBtn', 'mediaOpenCropBtn', 'editOptionColumns'].forEach((id) => {
    registry[id] = makeEl(id);
  });
const document = {
  readyState: 'complete',
  getElementById: (id) => registry[id] || null,
  createElement: (tag) => makeEl('created-' + tag),
  querySelectorAll: () => [],
  addEventListener: () => {},
};
const win = {};
function Event(type) { this.type = type; }
new Function('window', 'document', 'URL', 'fetch', 'Image', 'File', 'FormData', 'Event', source)(
  win, document, function URL() {}, function fetch() {}, function Image() {},
  function File() {}, function FormData() {}, Event
);
if (!win.refreshOptionEditorCard) { console.log('FAIL: 选项卡没有导出刷新入口'); process.exit(1); }
function check(name, ok) {
  if (!ok) { console.log('FAIL: ' + name); process.exit(1); }
  console.log('PASS ' + name);
}
"""


def test_option_editor_card_tracks_programmatic_question_loads():
    """载入题目/恢复草稿是程序化赋值：卡片要跟进刷新，且内容未变时不得重建 DOM 抢焦点。"""
    result = _run_node_script(_OPTION_CARD_DOM_PRELUDE + r"""
win.refreshOptionEditorCard();
check('默认非选择题时面板隐藏', registry.optionEditorPanel.classList.contains('hidden'));

registry.editQType.value = 'single_choice';
registry.editContent.value = '已知集合 $A=\\{x \\mid x^2-3x=0\\}$ 的子集个数为\nA．2\nB．3\nC．4\nD．8';
win.refreshOptionEditorCard();
check('载入选择题后面板显形', !registry.optionEditorPanel.classList.contains('hidden'));
check('载入选择题后渲染 4 个选项行', registry.optionRows.children.length === 4);

const rowRef = registry.optionRows.children[1];
win.refreshOptionEditorCard();
check('题干未变时重复刷新不重建行节点', registry.optionRows.children[1] === rowRef);

registry.editQType.value = 'fill_in';
registry.editContent.value = '计算 $1+1$ 的值。';
win.refreshOptionEditorCard();
check('切到填空题后面板收起', registry.optionEditorPanel.classList.contains('hidden'));
check('切到填空题后不留上一题的选项行', registry.optionRows.children.length !== 4);

registry.editQType.value = 'single_choice';
registry.editContent.value = '下列函数为奇函数的是\nA．$y=x^2$\nB．$y=x^3$\nC．$y=2x+1$\nD．$y=\\frac{1}{x}$';
win.refreshOptionEditorCard();
check('换一道选择题后重新渲染', registry.optionRows.children.length === 4);

registry.editContent.value = '新题\nA．1\nB．2';
registry.editContent.dispatchEvent(new Event('input'));
win.refreshOptionEditorCard();
check('手打题干路径仍然同步', registry.optionRows.children.length === 2);

console.log('OK');
""")
    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")
    assert "OK" in (result.stdout or "")


def test_option_editor_card_parses_every_choice_shape():
    """四种存量形态都要认出选项：逐行标号 / 单行挤选项 / choices 环境 / 图片选项。"""
    output = _run_option_card_script(r"""
const lines = api.parseStem('已知集合 $A=\\{x \\mid x^2-3x=0\\}$ 的子集个数为\nA．2\nB．3\nC．4\nD．8');
assertOk(lines.found && !lines.envBased, '逐行标号没被认成选项块');
assertOk(lines.options.map((o) => o.raw).join(',') === '2,3,4,8', '逐行内容错位');
assertOk(lines.prefix.indexOf('子集个数') !== -1 && lines.suffix === '', '题干前缀或后缀切错');

const inline = api.parseStem('函数 $f(x)=x^2-2x$ 在 $[0,3]$ 的最小值为 A. 8 B. 9 C. 11 D. 13');
assertOk(inline.found && inline.options.length === 4, '单行四选项没被切开');
assertOk(inline.options.map((o) => o.raw).join(',') === '8,9,11,13', '单行切分内容错位');
assertOk(inline.prefix.indexOf('的最小值为') !== -1, '单行形态的题干前缀丢了');

const env = api.parseStem('设 $a>0$，则\n  \\begin{choices}\n    \\item $1$\n    \\item $2$\n    \\item $3$\n    \\item $4$\n  \\end{choices}\n');
assertOk(env.found && env.envBased, '入库形态的 choices 环境没被认出');
assertOk(env.options.map((o) => o.raw).join(',') === '$1$,$2$,$3$,$4$', '环境切项混进了 \\end{choices}');

const images = api.parseStem('四个函数图象如下\nA．\\includegraphics[max width=0.42\\linewidth]{aa11.png}\nB．![选项B](/static/uploads/bb22.png)\nC．1\nD．2');
assertOk(images.found && images.options.length === 4, '图片型选项没被认出');
assertOk(images.options[0].images.length === 1 && images.options[0].text === '', 'LaTeX 图片未归入图片槽');
assertOk(images.options[1].images[0].url === '/static/uploads/bb22.png', 'Markdown 图片地址丢失');

const pairs = api.parseStem('下列命题正确的是\nA．对\nB．错');
assertOk(pairs.found && pairs.options.length === 2, '两选项判断题未识别');

const none = api.parseStem('设 $x \\in R$，定义 $\\operatorname{sgn} x = \\begin{cases} 1, & x > 0 \\\\ 0, & x = 0 \\end{cases}$，则函数 $f(x)=|x|\\operatorname{sgn} x$ 的图象大致是');
assertOk(!none.found, '不含选项的题干被误判成选择题');

const math = api.parseStem('已知 $A=\\{1,2\\}$，$B=\\{2,3\\}$，则 $A \\cap B$ 等于');
assertOk(!math.found, '数学段里的集合字母被当成选项标号');

console.log('OK');
""")
    assert "OK" in output


def test_option_editor_card_writes_plain_text_and_backend_rebuilds_choices():
    """回写只出「A．正文」纯文本，choices 版式由后端归一化链生成且幂等。"""
    entry_source = _read(STATIC_JS_DIR / "entry_options.js")
    compose_start = entry_source.index("function composeStem()")
    compose_end = entry_source.index("function syncFromStem(", compose_start)
    compose_source = entry_source[compose_start:compose_end]
    assert "begin{choices}" not in compose_source, "前端不得自己拼 choices 环境"
    assert "option.label + '．' + optionBody(option)" in compose_source

    from mathbank.latex_normalize import normalize_for_storage

    plain = "已知集合 $A=\\{1,2\\}$，则\nA．1\nB．2\nC．3\nD．4"
    stored = normalize_for_storage(plain, "single_choice")
    assert "\\begin{choices}" in stored
    assert stored.count("\\item ") == 4
    assert normalize_for_storage(stored, "single_choice") == stored

    images = "四个函数图象如下\n" + "\n".join(
        "%s．![选项%s](/static/uploads/%s.png)" % (label, label, label.lower()) for label in "ABCD"
    )
    image_stored = normalize_for_storage(images, "single_choice")
    assert image_stored.count("max width=0.42\\linewidth") == 4, "选项图没走选项限宽"
    assert normalize_for_storage(image_stored, "single_choice") == image_stored


def test_option_crop_tool_is_front_end_geometry_on_the_existing_upload():
    """切图工具：纯前端网格/框选几何 + 现有 /api/upload，零新增后端。"""
    index_source = _read(INDEX_PATH)
    entry_source = _read(STATIC_JS_DIR / "entry_options.js")

    elements = _index_elements()
    modal = elements["optionCropModal"]
    assert modal["role"] == "dialog"
    assert modal["aria-modal"] == "true"
    assert modal["aria-hidden"] == "true"
    assert modal["aria-labelledby"] == "optionCropModalTitle"
    assert "data-modal-close" in elements["optionCropCloseBtn"]
    assert elements["cropCountBadge"]["aria-live"] == "polite"

    assert 'data-crop-mode="grid"' in index_source and 'data-crop-mode="free"' in index_source
    assert len(re.findall(r'data-crop-preset="\dx\d"', index_source)) == 5, "网格预设少了"
    for element_id in ("cropRows", "cropCols", "cropInset", "cropFrame", "cropSourceImg",
                       "cropResults", "cropApplyBtn", "cropClearBtn"):
        assert element_id in elements

    assert entry_source.count("fetch(") == 1, "切图只允许走一个既有上传接口"
    assert "'/api/upload'" in entry_source
    assert "'optcrop_'" in entry_source
    for banned in ("/api/crop", "html2canvas", "toDataURL('image/jpeg'"):
        assert banned not in entry_source
    for marker in ("function computeGridBoxes(", "function bindCropDrag()",
                   "function applyCropFragments()", "function fillOptionsFromFragments("):
        assert marker in entry_source
    assert "MAX_TOTAL_PIXELS = 30000000" in entry_source and "MAX_UPLOAD_BYTES = 10 * 1024 * 1024" in entry_source

    output = _run_option_card_script(r"""
const boxes = api.computeGridBoxes(800, 600, 2, 2, 2);
assertOk(boxes.length === 4, '2×2 网格应切出 4 块');
assertOk(Math.abs(boxes[0].x - 8) < 1e-9 && Math.abs(boxes[0].y - 6) < 1e-9, '收边 2% 未生效：' + JSON.stringify(boxes[0]));
assertOk(boxes.every((b) => b.w > 0 && b.h > 0), '网格块出现非正尺寸');
assertOk(Math.abs(boxes[0].x + boxes[0].w + 8 - 400) < 1e-9, '相邻块之间没留出对称收边');

const row = api.computeGridBoxes(800, 200, 1, 4, 0);
assertOk(row.length === 4 && row[3].x === 600 && row[3].w === 200, '1×4 横切坐标不对：' + JSON.stringify(row));
assertOk(api.computeGridBoxes(100, 100, 99, 0, 50).length === 9, '行列数必须夹在 1–9');
assertOk(api.computeGridBoxes(400, 400, 2, 2, 30).every((b) => b.w > 0), '收边到 30% 仍应留下正尺寸');

console.log('OK');
""")
    assert "OK" in output


    # 取消守卫：上传途中关掉弹窗，这一批必须作废，且不能把「应用」按钮永久锁死
    crop_source = _read(STATIC_JS_DIR / "entry_options.js")
    close_start = crop_source.index("function closeCropDialog()")
    close_body = crop_source[close_start:crop_source.index("function cropSourceForOption(", close_start)]
    assert "crop.session += 1;" in close_body, "关掉弹窗不会作废在途批次"
    assert "crop.busy = false;" in close_body, "关掉弹窗后 busy 卡死，应用按钮再也点不动"

    apply_start = crop_source.index("function applyCropFragments()")
    apply_body = crop_source[apply_start:close_start]
    assert "const session = crop.session;" in apply_body
    assert apply_body.count("if (crop.session !== session) return;") == 2, (
        "成功与失败两条分支都要校验会话号"
    )


def test_save_payload_registers_tikz_owned_images():
    """保存链路必须登记绘图资产认领的物理文件——TikZ 渲染图按设计不出现在正文里。"""
    api_source = _read(STATIC_JS_DIR / "api.js")
    editor_source = _read(STATIC_JS_DIR / "editor.js")
    import_source = _read(STATIC_JS_DIR / "import.js")
    ocr_source = _read(STATIC_JS_DIR / "ocr.js")

    start = api_source.index("assetImagePaths()")
    body = api_source[start:api_source.index("\n            }", start)]
    assert "asset.image_path" in body and "asset.reference_image_path" in body, (
        "assetImagePaths() 必须同时认领渲染图与原参考图"
    )

    assert editor_source.count("...TikzState.assetImagePaths()") == 2, (
        "脏态快照与保存 payload 都要带上资产认领的文件"
    )
    assert "...TikzState.assetImagePaths()" in import_source, "真正的保存链路漏登记资产文件"

    # 「把原参考图从配图条里藏起来」是显示口径，不能被顺手换成资产口径
    for source, label in ((editor_source, "editor.js"), (import_source, "import.js"), (ocr_source, "ocr.js")):
        assert "new Set(TikzState.referencePaths())" in source, "%s 的隐藏参考图显示口径被改坏" % label


def test_illustration_text_sync_keeps_registration_without_body_reference():
    """正文刷新只剩一个正确判据：刚才在正文里、现在没了，才算用户把图删了。"""
    result = _run_node_script(r"""
const fs = require('fs');
const src = fs.readFileSync('static/js/editor.js', 'utf8');
const start = src.indexOf('function illustrationRegistrationSurvives');
if (start < 0) { console.log('FAIL: editor.js 没有 illustrationRegistrationSurvives'); process.exit(1); }
const end = src.indexOf('window.illustrationRegistrationSurvives = illustrationRegistrationSurvives;', start);
if (end < 0) { console.log('FAIL: 判定助手没有导出到 window'); process.exit(1); }
const survives = new Function(src.slice(start, end) + String.fromCharCode(10) + 'return illustrationRegistrationSurvives;')();
function assertOk(cond, msg) { if (!cond) { console.log('FAIL: ' + msg); process.exit(1); } }

// 载入带 TikZ 的题：正文从没出现过渲染图，第一次刷新绝不能把登记剔掉（否则保存 400）
assertOk(survives('/static/uploads/tikz_a.png', '', '如图所示，求函数图象的对称轴。', []),
    '载入后第一次刷新就剔掉了 TikZ 渲染图登记');
// 从没进过正文的合法登记（拆卷截图 / MinerU 整页图）一律保留
assertOk(survives('/static/uploads/pdf_crop_task1.png', '上一版正文', '换了别的正文', []),
    '正文不引用的配图登记被静默释放');
// 绘图资产认领的文件，无论正文怎么写都必须保留
assertOk(survives('/static/uploads/tikz_b.png', '刚才还写着 /static/uploads/tikz_b.png', '图已从正文删掉', ['/static/uploads/tikz_b.png']),
    '资产认领的文件仍被剔除');
// 用户真的删掉图片语法时，登记要跟着释放，否则文件永远回收不掉
assertOk(!survives('/static/uploads/x.png', '![插图](/static/uploads/x.png)', '图被我删了', []),
    '正文删图后登记没有释放');
// 仍在正文里的图当然保留
assertOk(survives('/static/uploads/y.png', '旧文', '看 ![插图](/static/uploads/y.png)', []),
    '正文仍引用的图被误删');
assertOk(!survives('', 'a', 'b', []), '空路径不该留在登记里');
console.log('OK');
""")
    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")
    assert "OK" in (result.stdout or "")


# --------------------------------------------------------------------------
# #35 组卷页性能架构：变高虚拟滚动 / lean 分页 / 购物车按需水合
# --------------------------------------------------------------------------

def _virtual_list_helper_source() -> str:
    """抠出 virtual_list.js 里不碰 DOM 的两个纯函数（窗口定位全靠它们）。"""

    source = _read(STATIC_JS_DIR / "virtual_list.js")
    start = source.index("function clamp(value, low, high) {")
    end = source.index("function VirtualList(options) {")
    helpers = source[start:end]
    assert "function findRowIndex(" in helpers, "二分定位必须和 clamp 待在同一段可执行区域里"
    return helpers


def test_virtual_list_row_lookup_matches_a_linear_scan():
    """变高虚拟滚动每帧都在问「这个 scrollTop 落在第几行」，错一格就是白屏或跳动。

    前缀和 + 二分的边界（正好压在行边界、目标为负、目标超出总高）是最容易写错的地方，
    所以拿一条线性扫描当参照随机 fuzz，再单独跑一遍 10 万行等高数据。
    """

    script = _virtual_list_helper_source() + r"""
function buildOffsets(heights) {
    const offsets = [0];
    for (let i = 0; i < heights.length; i += 1) offsets.push(offsets[i] + heights[i]);
    return offsets;
}
function reference(offsets, target, count) {
    for (let i = 0; i < count; i += 1) {
        if (target < offsets[i + 1]) return i;
    }
    return count - 1;
}
const problems = [];
let seed = 20261019;
function rand() {
    seed = (seed * 1103515245 + 12345) % 2147483648;
    return seed / 2147483648;
}
for (let trial = 0; trial < 200; trial += 1) {
    const count = 1 + Math.floor(rand() * 300);
    const heights = [];
    for (let i = 0; i < count; i += 1) heights.push(1 + Math.floor(rand() * 900));
    const offsets = buildOffsets(heights);
    const total = offsets[count];
    for (let step = 0; step < 60; step += 1) {
        const target = Math.floor(rand() * (total + 120)) - 40;
        const got = findRowIndex(offsets, target, count);
        if (got !== reference(offsets, target, count)) problems.push({ trial, count, target, got });
        if (got < 0 || got > count - 1) problems.push({ trial, target, got, outOfRange: true });
    }
}
const flat = new Array(100000).fill(160);
const flatOffsets = buildOffsets(flat);
let flatMismatches = 0;
for (let target = 0; target < flatOffsets[100000]; target += 997) {
    if (findRowIndex(flatOffsets, target, 100000) !== Math.floor(target / 160)) flatMismatches += 1;
}
// 行边界必须归属「下一行」：target 正好等于某行下沿时，那一行已经滚出视口顶部了。
let edgeMismatches = 0;
for (let i = 1; i < 5000; i += 1) {
    if (findRowIndex(flatOffsets, i * 160, 100000) !== i) edgeMismatches += 1;
}
const clampBad = [];
if (clamp(5, 0, 3) !== 3) clampBad.push('high');
if (clamp(-1, 0, 3) !== 0) clampBad.push('low');
if (clamp(2, 0, 3) !== 2) clampBad.push('mid');
if (problems.length) console.log('FIRST_PROBLEMS ' + JSON.stringify(problems.slice(0, 5)));
console.log('###COUNTS###' + problems.length + ',' + flatMismatches + ',' + edgeMismatches + ',' + clampBad.length);
"""
    result = _run_node_script(script)
    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")
    match = re.search(r"###COUNTS###(\d+),(\d+),(\d+),(\d+)", result.stdout)
    assert match, "node 侧没有产出计数"
    problems, flat_mismatches, edge_mismatches, clamp_bad = (
        int(value) for value in match.groups()
    )

    assert problems == 0, (result.stdout or "").strip()
    assert flat_mismatches == 0, "10 万行等高数据定位错位：每帧窗口算错，滚动必白屏"
    assert edge_mismatches == 0, "行边界归属判错，滚动时会反复抖一行"
    assert clamp_bad == 0, "clamp 失效：窗口起止会被夹出合法区间"


def test_paper_question_stream_renders_only_the_visible_window():
    """题库流必须走虚拟滚动：万题时 DOM 只能有视口内那几十张卡。"""

    virtual_source = _read(STATIC_JS_DIR / "virtual_list.js")
    paper_source = _read(STATIC_JS_DIR / "paper.js")
    index_source = _read(INDEX_PATH)
    css_source = _read(CSS_PATH)
    main_source = _read(PROJECT_ROOT / "main.py")

    # 加载顺序：虚拟滚动器必须在 paper.js 之前可用，且要吃缓存击穿参数
    assert 'src="/static/js/virtual_list.js' in index_source
    assert index_source.index("/static/js/virtual_list.js") < index_source.index(
        "/static/js/paper.js"
    ), "virtual_list.js 晚于 paper.js 加载时，首次渲染拿不到构造器"
    assert '"virtual_list.js"' in main_source, "漏登记就没有缓存击穿，用户浏览器会一直跑旧脚本"

    assert "window.MathBankVirtualList = VirtualList" in virtual_source
    assert "ResizeObserver" in virtual_source, "变高列表靠 ResizeObserver 量回真实高度"
    assert "_heightByKey" in virtual_source, "高度要按题目 key 持久化，翻页/换筛选才不会来回抖"
    assert "scrollTop +=" in virtual_source, "量高之后必须补偿锚点位移，否则往上滚会整屏跳动"
    assert "VirtualList.prototype.setDragLock" in virtual_source
    assert "VirtualList.prototype.invalidateKeys" in virtual_source
    assert "this.disabled" in virtual_source, "压测 A/B 需要一个不裁剪窗口的整表渲染模式"

    assert "new window.MathBankVirtualList(" in paper_source
    for marker in (
        "buildPaperStreamScaffold",
        "renderPaperStreamRow",
        "estimatePaperStreamRowHeight",
        "onPaperStreamRangeChange",
        "paperStreamVirtualList.invalidateKeys(",
        'paperStreamVirtualList.setDragLock(true)',
        'paperStreamVirtualList.setDragLock(false)',
    ):
        assert marker in paper_source, f"组卷流缺少虚拟滚动接线：{marker}"

    # 拖拽排序与虚拟窗口的冲突：源节点被回收就等于拖拽作废
    drag_start = paper_source[paper_source.index("window.onPaperCanvasDragStart =") :]
    drag_end = paper_source[paper_source.index("window.onPaperCanvasDragEnd =") :]
    assert "setDragLock(true)" in drag_start[: drag_start.index("\n    };")], (
        "拖拽开始必须锁窗口"
    )
    assert "setDragLock(false)" in drag_end[: drag_end.index("\n    };")], (
        "拖拽结束必须解锁，否则窗口再也不增删行"
    )

    assert "position: relative" in css_source[css_source.index("#paperQuestionStream") :]
    assert "will-change: transform" in css_source[css_source.index(".paper-stream-row") :]


def test_paper_stream_list_requests_are_paginated_and_lean():
    """列表请求必须把分页、排序、过滤和 lean 序列化全部下推到 SQL。"""

    paper_source = _read(STATIC_JS_DIR / "paper.js")
    fetch_body = paper_source[
        paper_source.index("async function fetchBankQuestions") : paper_source.index(
            "window.retryPaperStreamPage"
        )
    ]

    for marker in (
        "params.set('page', String(targetPage))",
        "params.set('page_size', String(PAPER_STREAM_PAGE_SIZE))",
        "params.set('mode', 'lean')",
        "params.set('sort', 'desc')",
    ):
        assert marker in fetch_body, f"分页请求缺少 {marker}"

    assert "new AbortController()" in fetch_body and "bankQuestionsRequestSeq" in fetch_body, (
        "连点筛选条件时后发先至会让旧页盖掉新页，序号 + abort 是这里唯一的防线"
    )
    assert "append" in fetch_body, "无限滚动要在旧数据后面追加，而不是整页替换"
    assert "pagination.error" in paper_source and "retryPaperStreamPage" in paper_source, (
        "翻页失败要给得出重试入口，不能留一片空白列表"
    )


def test_api_status_probe_no_longer_downloads_the_whole_bank():
    """右上角那个小绿点，原先每开一次页面都要拖回整库 JSON。"""

    api_source = _read(STATIC_JS_DIR / "api.js")
    probe_start = api_source.index("const indicator = document.getElementById('apiStatusIndicator')")
    probe_body = api_source[probe_start : probe_start + 900]
    # 只扫代码行：紧邻的注释正好在解释「这里以前 fetch 的是 /api/questions」，逐字扫会永远误报
    probe_code = "\n".join(
        line for line in probe_body.splitlines() if not line.strip().startswith("//")
    )

    assert "fetch('/healthz')" in probe_code, "就绪探针必须走 /healthz"
    assert "fetch('/api/questions')" not in probe_code, (
        "探针不许回到 /api/questions：不带 page 时它命中的是整库数组分支，十万题就是几十 MB"
    )


def test_paper_cart_hydration_separates_deleted_from_unreachable():
    """购物车三态水合（移植自上游）：读不到 ≠ 不存在，绝不能静默丢题。"""

    paper_source = _read(STATIC_JS_DIR / "paper.js")

    for marker in (
        "missingIds: [],",
        "confirmedMissingIds: [],",
        "failedIds: []",
        "function getMissingCartQuestionIds()",
        "async function ensureCartQuestionsLoaded(",
        "window.removeMissingPaperCartQuestions",
        "window.revalidatePaperCartQuestions",
        "function renderCartQuestionLoadBanner()",
        "function buildCartQuestionLoadError(",
    ):
        assert marker in paper_source, f"购物车水合缺少 {marker}"

    load_body = paper_source[
        paper_source.index("async function ensureCartQuestionsLoaded(") : paper_source.index(
            "window.revalidatePaperCartQuestions"
        )
    ]
    assert "/api/paper/questions?" in load_body, "水合必须走批量端点，一题一个请求会把浏览器打爆"
    assert "transportFailed" in load_body, (
        "整批请求失败时只能记进 failedIds，不能按「服务端说它不存在」处理"
    )
    assert "暂时读取失败" in paper_source and "已不存在" in paper_source, (
        "两类失败必须说给用户的两句话，重试按钮的语义完全不同"
    )

    # localStorage 里的老购物车：进页面就要补齐，补齐失败也不能丢
    workspace_start = paper_source.index("window.renderPaperWorkspace = async function")
    workspace_body = paper_source[workspace_start : workspace_start + 4000]
    assert "ensureCartQuestionsLoaded()" in workspace_body, (
        "刷新后购物车靠 /api/paper/questions 水合，分页改动绝不能让它丢题"
    )
    assert "Promise.all(" in workspace_body, "题库第一页与购物车水合并行，别让页面等两段串行网络"


def test_paper_export_and_save_are_gated_on_a_fully_loaded_cart():
    """导出前硬闸：卷面还有未水合的题就禁止出卷（A4 预览会静默跳过空 content 的题）。"""

    paper_source = _read(STATIC_JS_DIR / "paper.js")

    payload_body = paper_source[
        paper_source.index("function buildCartQuestionsPayload() {") : paper_source.index(
            "function buildCartQuestionsPayload() {"
        )
        + 400
    ]
    assert "assertPaperCartFullyLoaded();" in payload_body, (
        "PDF / Word / bundle 三条导出路径都从 buildCartQuestionsPayload 出发，闸口设在这里最省"
    )
    assert payload_body.index("assertPaperCartFullyLoaded();") < payload_body.index("return cart.map("), (
        "硬闸必须在拼 payload 之前：题面缺失时不能一边导出一边静默跳题"
    )

    gate_body = paper_source[
        paper_source.index("function assertPaperCartFullyLoaded()") : paper_source.index(
            "function buildCartQuestionsPayload()"
        )
    ]
    assert "!isPaperQuestionHydrated(qid)" in gate_body
    assert "throw new Error" in gate_body, "只 toast 不抛错的话，调用方会带着半空卷面继续导出"

    save_index = paper_source.index("window.savePaperToDb = async function")
    save_body = paper_source[save_index : save_index + 1600]
    assert "assertPaperCartFullyLoaded();" in save_body, (
        "保存走的是裸 cart，不经过 buildCartQuestionsPayload，必须单独补一次闸"
    )


def test_paper_detail_hydration_is_on_demand_and_bounded():
    """完整题面只在进入视口 / 加入试卷时才取，并且缓存要有上限，不能无限膨胀。"""

    paper_source = _read(STATIC_JS_DIR / "paper.js")

    for marker in (
        "new IntersectionObserver(",
        "function hydratePaperStreamDetails(",
        "function isPaperQuestionHydrated(",
        "function paperStreamTrackHydrated(",
    ):
        assert marker in paper_source, f"按需水合缺少 {marker}"

    assert "addToCart = async function" in paper_source, (
        "加题时必须先把这一题水合到位，否则 A4 卷面会把它当空题跳过"
    )
    hydrate_body = paper_source[
        paper_source.index("function hydratePaperStreamDetails(") : paper_source.index(
            "function hydratePaperStreamDetails("
        )
        + 3000
    ]
    assert "slice(" in hydrate_body and "ids" in hydrate_body, "水合要分批下发，一屏几十题不能一个请求全要"
    track_body = paper_source[
        paper_source.index("function paperStreamTrackHydrated(") : paper_source.index(
            "function paperStreamTrackHydrated("
        )
        + 1200
    ]
    assert "isInCart" in track_body, "购物车里的题永不淘汰：它随时要参与导出硬闸"
    assert "delete " in track_body, "水合缓存必须有上限，否则长会话滚十万题会把内存吃穿"

def _tailwind_gate_stub_source() -> str:
    """闸门脚本只依赖 window / document / MutationObserver，全部用桩替代。"""

    return r"""
const observeLog = [];
const disconnectLog = [];
const classLog = [];

function StubObserver(callback) {
    this.callback = callback;
}
StubObserver.prototype.observe = function (target, options) {
    observeLog.push((options.attributeFilter || []).join('+'));
};
StubObserver.prototype.disconnect = function () {
    disconnectLog.push(1);
};

const documentElement = {
    name: 'html',
    classList: {
        add: function (name) { classLog.push('+' + name); },
        remove: function (name) { classLog.push('-' + name); }
    }
};
const window = { MutationObserver: StubObserver };
const document = { documentElement: documentElement };
const problems = [];
function expect(cond, msg) { if (!cond) problems.push(msg); }
const TW_OPTIONS = { attributes: true, attributeFilter: ['class'], childList: true, subtree: true };
const A11Y_OPTIONS = { attributes: true, attributeFilter: ['disabled'], childList: true, subtree: true };
"""


def test_tailwind_gate_pauses_and_rehooks_only_the_cdn_scanner():
    """样式闸门的两个致命坑必须用可执行回归钉死（都真踩过）。

    ① 误关业务观察器：api.js 里同步按钮无障碍态的那个盯的是 disabled，它没有「开闸补扫」
       这一步，被关掉就是真的永久漏同步；
    ② pause 走包装版 disconnect：包装版会把登记项一起删掉，resume 再无对象可重挂，
       表现是 CDN 全量重扫被永久关停、样式静默缺失，而压测数字好看得离谱。
    """

    script = (
        _tailwind_gate_stub_source()
        + _read(STATIC_JS_DIR / "tailwind_gate.js")
        + r"""
const gate = window.MathBankTailwindGate;
expect(typeof gate === 'object' && gate, '闸门没导出 MathBankTailwindGate，paper.js 拿不到它');

const cdn = new window.MutationObserver(function () {});
cdn.observe(documentElement, TW_OPTIONS);
expect(gate.watcherCount() === 1, 'Tailwind 的全站 class 观察器没被登记，pause() 无从下手');

const a11y = new window.MutationObserver(function () {});
observeLog.length = 0;
a11y.observe(documentElement, A11Y_OPTIONS);
expect(gate.watcherCount() === 1, '非 class 签名的观察器被误登记，会被闸门静默关掉');
expect(observeLog.indexOf('disabled') !== -1, '没被登记的观察器必须照常启动');

observeLog.length = 0;
disconnectLog.length = 0;
expect(gate.pause() === true, '首次 pause 应返回 true');
expect(disconnectLog.length === 1, '关闸只断掉那一个全站观察器');
expect(gate.watcherCount() === 1,
    'pause 之后 watcherCount 掉到 0 = 用的是包装版 disconnect，resume 挂不回来');

const late = new window.MutationObserver(function () {});
observeLog.length = 0;
late.observe(documentElement, TW_OPTIONS);
expect(observeLog.length === 0, '暂停期间新挂的全站观察器启动了，闸门被悄悄绕过');
expect(gate.watcherCount() === 2, '暂停期间新建的观察器也要登记，开闸时补挂');

observeLog.length = 0;
classLog.length = 0;
expect(gate.resume() === true, '首次 resume 应返回 true');
expect(observeLog.length === 2, '开闸要把暂停期间攒下的全站观察器全部重挂');
expect(gate.watcherCount() === 2, '重挂之后登记数不能掉');
expect(classLog.indexOf('+tw-gate-flush') !== -1 && classLog.indexOf('-tw-gate-flush') !== -1,
    '开闸必须补一次 class 增删，让 CDN 把暂停期漏掉的样式重扫回来');
expect(gate.resume() === false, '重复 resume 不该再补扫一遍');
expect(gate.isPaused() === false, 'resume 之后状态要落回未暂停');

gate.pause();
late.disconnect();
expect(gate.watcherCount() === 1, '主动 disconnect 的观察器必须从登记表摘除，否则越积越多');
gate.resume();
expect(gate.watcherCount() === 1, '摘除之后只剩 CDN 那一个登记项');
disconnectLog.length = 0;
observeLog.length = 0;
gate.pause();
expect(disconnectLog.length === 1,
    '再次关闸只能断掉仍在登记表里的那一个，摘掉的不能被重断：' + disconnectLog.length);
gate.resume();
expect(observeLog.length === 1, '再次开闸只重挂仍在登记表里的那一个：' + observeLog.length);

console.log(problems.length ? 'PROBLEMS: ' + problems.join(' | ') : 'OK');
process.exit(problems.length ? 1 : 0);
"""
    )

    result = _run_node_script(script)
    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")
    assert "OK" in (result.stdout or ""), (result.stdout or "")


def test_tailwind_gate_loads_before_the_play_cdn_and_is_registered():
    """加载位置与缓存击穿：晚一步、或吃旧缓存，闸门都会静默失效。"""

    gate_source = _read(STATIC_JS_DIR / "tailwind_gate.js")
    index_source = _read(INDEX_PATH)
    main_source = _read(PROJECT_ROOT / "main.py")

    assert index_source.index("/static/js/tailwind_gate.js") < index_source.index(
        "/static/lib/tailwind/tailwind.min.js"
    ), "闸门晚于 Play CDN 加载时抢不到 MutationObserver，watcherCount 永远是 0"
    assert '"tailwind_gate.js"' in main_source, "漏登记就没有缓存击穿，用户浏览器会一直跑旧闸门"
    assert 'src="/static/js/tailwind_gate.js' in index_source

    for marker in (
        "window.MathBankTailwindGate",
        "window.MutationObserver = GatedObserver",
        "watched[index].stop()",
        "stop: originalDisconnect",
        "tw-gate-flush",
        "filter.indexOf('class')",
    ):
        assert marker in gate_source, f"闸门实现缺少 {marker}"

def _extract_paper_stream_function(source: str, signature: str) -> str:
    """按 paper.js 的统一缩进（函数顶格 4 空格、闭合也是 `    }`）抠出一个完整函数。

    宁可抽取失败报错，也不要静默拿到半截函数——那会让断言永远为真。
    """

    start = source.index(signature)
    end = source.index("\n    }", start) + len("\n    }")
    block = source[start:end]
    assert block.endswith("\n    }"), f"{signature} 的闭合形状变了，抽取逻辑要跟着改"
    return block


_PAPER_STREAM_NODE_STUB = r"""
const observeLog = [];
const problems = [];
function expect(cond, msg) { if (!cond) problems.push(msg); }
function report() {
    console.log(problems.length ? 'PROBLEMS: ' + problems.join(' | ') : 'OK');
    process.exit(problems.length ? 1 : 0);
}
"""


def test_paper_stream_choice_grids_are_deferred_while_scrolling():
    """选项网格试列数要读宽度（强制重排），卡片飞掠过视口时算它纯属白付。"""

    paper_source = _read(STATIC_JS_DIR / "paper.js")
    start = paper_source.index("function paperStreamIsScrolling() {")
    end = paper_source.index("function paperStreamGateOpen() {")
    block = paper_source[start:end]
    for required in (
        "function paperStreamMeasureChoices(",
        "function paperStreamFlushDeferredGrids(",
    ):
        assert required in block, f"顺延逻辑被挪走了，测试要跟着改：{required}"

    script = _PAPER_STREAM_NODE_STUB + r"""
const NOW = { value: 1000 };
const gridBatches = [];
const window = {
    adaptChoicesGridNodes: function (nodes) { gridBatches.push(nodes.slice()); }
};
const performance = { now: function () { return NOW.value; } };
const PAPER_STREAM_SCROLLING_MS = 180;
const PAPER_STREAM_DEFERRED_GRID_LIMIT = 240;
let paperStreamLastScrollTs = 0;
let paperStreamDeferredGrids = [];
""" + block + r"""
function makeNode(id, connected) { return { id: id, isConnected: connected }; }

expect(paperStreamIsScrolling() === false, '没有滚动记录时不该判定为滚动中');
paperStreamLastScrollTs = NOW.value;
expect(paperStreamIsScrolling() === true, '刚滚动过必须判定为滚动中');

paperStreamMeasureChoices([makeNode('a', true), makeNode('b', true)]);
expect(gridBatches.length === 0, '滚动中绝不能读宽度：强制重排正是实测长任务的大头');
expect(paperStreamDeferredGrids.length === 2, '滚动中的网格要攒进顺延表');

paperStreamLastScrollTs = NOW.value - 200;
paperStreamFlushDeferredGrids();
expect(gridBatches.length === 1, '补算要合并成整批一次，不能一格一次');
expect(gridBatches[0].length === 2, '顺延下来的两个节点都要补算');
expect(paperStreamDeferredGrids.length === 0, '补完必须清空顺延表');

paperStreamDeferredGrids = [makeNode('live', true), makeNode('gone', false)];
gridBatches.length = 0;
paperStreamFlushDeferredGrids();
expect(gridBatches.length === 1 && gridBatches[0].length === 1,
    '已被窗口回收的节点不该再出现在补算批次里');

gridBatches.length = 0;
paperStreamLastScrollTs = NOW.value;
const flood = [];
for (let i = 0; i <= PAPER_STREAM_DEFERRED_GRID_LIMIT; i += 1) flood.push(makeNode('f' + i, true));
paperStreamMeasureChoices(flood);
expect(gridBatches.length >= 1, '顺延表超上限必须立刻落盘，狂滚不能把内存吃穿');
expect(paperStreamDeferredGrids.length <= PAPER_STREAM_DEFERRED_GRID_LIMIT,
    '顺延表长度受硬上限约束');

paperStreamLastScrollTs = NOW.value - 200;
paperStreamDeferredGrids = [];
gridBatches.length = 0;
paperStreamMeasureChoices([makeNode('c', true)]);
expect(gridBatches.length === 1 && paperStreamDeferredGrids.length === 0,
    '不在滚动中就当场算完，别把已经停下的批次顺延下去');

gridBatches.length = 0;
paperStreamFlushDeferredGrids();
expect(gridBatches.length === 0, '没有待补算节点时不该产生空调用');
report();
"""

    result = _run_node_script(script)
    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")
    assert "OK" in (result.stdout or ""), (result.stdout or "")


def test_paper_stream_typeset_budget_spreads_across_frames():
    """一批发回几十题时，排版必须逐帧摊开，而不是一次阻塞主线程上百毫秒。"""

    paper_source = _read(STATIC_JS_DIR / "paper.js")
    script = _PAPER_STREAM_NODE_STUB + r"""
const rafQueue = [];
const window = {
    requestAnimationFrame: function (fn) {
        rafQueue.push(fn);
        return rafQueue.length;
    }
};
const PAPER_STREAM_TYPESET_BUDGET = 6;
let paperStreamTypesetQueue = [];
let paperStreamTypesetFrame = 0;
const typesetCalls = [];
const gridBatches = [];
const typesetCache = {};
function typesetPaperMath(node) { typesetCalls.push(node.key); }
function paperStreamRememberTypeset(key, html) { typesetCache[key] = html; }
function paperStreamMeasureChoices(nodes) { gridBatches.push(nodes.length); }
""" + _extract_paper_stream_function(paper_source, "function pumpPaperStreamTypeset() {") + r"""
function makeNode(key, connected) {
    return {
        key: key,
        isConnected: connected,
        innerHTML: 'H' + key,
        dataset: { typesetKey: key },
        removeAttribute: function () {}
    };
}
function runFrames(limit) {
    for (let i = 0; i < limit && rafQueue.length; i += 1) {
        const fn = rafQueue.shift();
        fn();
    }
}

for (let i = 0; i < 20; i += 1) paperStreamTypesetQueue.push(makeNode('n' + i, true));
paperStreamTypesetQueue.push(makeNode('stale', false));
pumpPaperStreamTypeset();
pumpPaperStreamTypeset();
expect(rafQueue.length === 1, '同一帧里重复泵不能排两次，否则每帧排版量翻倍');
runFrames(100);
expect(rafQueue.length === 0, '队列跑空之后不该继续排帧');
expect(paperStreamTypesetQueue.length === 0, '逐帧队列必须能被跑空');
expect(typesetCalls.length === 20,
    '被窗口回收的行不该再付排版钱，实际排版 ' + typesetCalls.length + ' 个');
let maxBatch = 0;
for (let i = 0; i < gridBatches.length; i += 1) maxBatch = Math.max(maxBatch, gridBatches[i]);
expect(maxBatch <= PAPER_STREAM_TYPESET_BUDGET,
    '每帧排版批次不得超过预算，实际最大 ' + maxBatch);
expect(gridBatches.length >= 4, '超出预算的部分要顺延到后续帧，不能一帧吞完');
expect(Object.keys(typesetCache).length === 20,
    '排版结果按 key 缓存，行回收再挂回来时零重排版');
report();
"""

    result = _run_node_script(script)
    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")
    assert "OK" in (result.stdout or ""), (result.stdout or "")

def test_paper_stream_row_rebuild_is_deferred_while_scrolling():
    """狂滚期间绝不重建行：140 字预览本来就是设计，停手后由 settle 落点统一补齐。"""

    paper_source = _read(STATIC_JS_DIR / "paper.js")
    script = _PAPER_STREAM_NODE_STUB + r"""
const rafQueue = [];
const window = {
    requestAnimationFrame: function (fn) {
        rafQueue.push(fn);
        return rafQueue.length;
    }
};
const NOW = { value: 1000 };
const performance = { now: function () { return NOW.value; } };
const PAPER_STREAM_SCROLLING_MS = 180;
const PAPER_STREAM_INVALIDATE_BUDGET = 3;
let paperStreamLastScrollTs = 0;
let paperStreamInvalidateQueue = [];
let paperStreamInvalidateFrame = 0;
let paperStreamVirtualList = null;
const invalidated = [];
const gateHolds = [];
function paperStreamHoldGate() { gateHolds.push(1); }
function runFrames(limit) {
    for (let i = 0; i < limit && rafQueue.length; i += 1) {
        const fn = rafQueue.shift();
        fn();
    }
}
""" + _extract_paper_stream_function(paper_source, "function paperStreamIsScrolling() {") + "\n" + _extract_paper_stream_function(paper_source, "function pumpPaperStreamInvalidations() {") + r"""
expect(pumpPaperStreamInvalidations.length === 0, '抽取结果不该带残留');
paperStreamVirtualList = {
    invalidateKeys: function (keys) { invalidated.push(keys.slice()); }
};

for (let i = 0; i < 9; i += 1) paperStreamInvalidateQueue.push('k' + i);
paperStreamLastScrollTs = NOW.value;
pumpPaperStreamInvalidations();
pumpPaperStreamInvalidations();
expect(rafQueue.length === 0, '滚动中绝不能重建行：拆行 + 重挂 + KaTeX 挤在一帧就是实测长任务');
expect(paperStreamInvalidateQueue.length === 9, '滚动中被顺延的重建要原封不动攒着，一题都不能漏');

paperStreamLastScrollTs = NOW.value - 200;
pumpPaperStreamInvalidations();
pumpPaperStreamInvalidations();
expect(rafQueue.length === 1, '停手之后要补重建，且同一帧只排一次');
runFrames(100);
expect(paperStreamInvalidateQueue.length === 0, '重建队列必须被逐帧跑空');
let maxBatch = 0;
for (let i = 0; i < invalidated.length; i += 1) maxBatch = Math.max(maxBatch, invalidated[i].length);
expect(maxBatch <= PAPER_STREAM_INVALIDATE_BUDGET,
    '每帧重建行数受预算约束，实际最大 ' + maxBatch);
expect(gateHolds.length === invalidated.length, '每批重建都要先关样式闸门再动 DOM');

paperStreamInvalidateQueue = ['orphan'];
paperStreamVirtualList = null;
rafQueue.length = 0;
pumpPaperStreamInvalidations();
expect(rafQueue.length === 0 && paperStreamInvalidateQueue.length === 0,
    '列表已经拆掉时直接丢队列，别让悬挂请求把下一次渲染拖住');
report();
"""

    result = _run_node_script(script)
    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")
    assert "OK" in (result.stdout or ""), (result.stdout or "")


def test_paper_stream_scroll_path_avoids_forced_reflows():
    """滚动路径上「读布局」的地方必须收进缓存或预算，这三处都实测过、会被改回去。"""

    virtual_source = _read(STATIC_JS_DIR / "virtual_list.js")
    paper_source = _read(STATIC_JS_DIR / "paper.js")
    editor_source = _read(STATIC_JS_DIR / "editor.js")

    # 每帧只读一次 scrollTop：多读一次就是一次强制同步布局（实测占滚动期 17%）
    assert "VirtualList.prototype._measureChrome" in virtual_source, "offsetTop/clientHeight 要缓存"
    assert "this._scrollTop = this.scroller.scrollTop;" in virtual_source
    assert virtual_source.count("this.scroller.scrollTop;") == 1, (
        "窗口计算里又直接读了一次 scrollTop：每帧多一次强制重排，滚动会退回卡顿"
    )

    assert "window.adaptChoicesGridNodes = adaptChoicesGridNodes;" in editor_source, (
        "逐帧只喂新节点的批量入口没了，选项列数会退回「每格强制重排一次」"
    )
    assert "function adaptChoicesGrids(grids)" in editor_source, (
        "整表与批量必须共用同一套列数算法，否则两条路径会给出不同结果"
    )

    for marker in (
        "PAPER_STREAM_TYPESET_BUDGET",
        "PAPER_STREAM_INVALIDATE_BUDGET",
        "PAPER_STREAM_SCROLLING_MS",
        "PAPER_STREAM_DEFERRED_GRID_LIMIT",
        "if (paperStreamIsScrolling()) return;",
        "window.adaptChoicesGridNodes(settled)",
        "paperStreamHoldGate()",
        "paperStreamBumpScroll",
    ):
        assert marker in paper_source, f"性能分层调度缺少 {marker}"

    # 闸门必须真的被用上：关闸拿 watcherCount 兜底，开闸走 settle 与硬上限两个落点
    gate_calls = paper_source[paper_source.index("function paperStreamHoldGate() {") : paper_source.index(
        "function paperStreamBumpScroll()"
    )]
    assert "gate.watcherCount()" in gate_calls, "闸门不可用（加载顺序被改过）时也要武装停手定时器"
    assert "PAPER_STREAM_GATE_MAX_PAUSE_MS" in gate_calls, "狂滚时样式不能一直欠着，必须有硬上限"

def _paper_figure_size_source() -> str:
    """抠出 paper.js 里插图尺寸的纯函数段（只读 PaperStore，不碰 DOM）。"""

    source = _read(STATIC_JS_DIR / "paper.js")
    start = source.index("const PAPER_FIGURE_SIZE_CM =")
    end = source.index("function hasCachedPaperAnswer(")
    block = source[start:end]
    assert "function getQuestionFigSize(q)" in block, "尺寸枚举与取值必须在同一段里"
    return block


def _paper_image_anchor_source() -> str:
    """抠出 paper.js 里「题末可分离图簇」那一小段（纯正则，不碰 DOM）。"""

    source = _read(STATIC_JS_DIR / "paper.js")
    start = source.index("const PAPER_IMAGE_ANCHOR_RE =")
    end = source.index("function formatQuestionContentHtml(")
    block = source[start:end]
    for required in (
        "function splitPaperImageAnchors(",
        "function shouldPreserveInlinePaperImages(",
    ):
        assert required in block, f"题末图簇判定被挪走了，测试要跟着改：{required}"
    return block


def test_paper_preview_image_cluster_rule_matches_the_backend():
    """网页预览必须和 LaTeX 导出用同一套判定，否则「选项拼图重复」只在一侧消失。

    直接把上游移植过来的 Python 实现当参照逐案对比：摘出来的正文、摘下来的图簇、
    「这张题的图该不该留在原位」三个结果都必须逐字一致。
    """

    from mathbank.image_layout import split_image_anchors
    from mathbank.paper_helper import _should_preserve_inline_image_positions

    cases = [
        "",
        "纯文字题目，没有插图",
        "题干 ![](uploads/a.png) ![](/static/uploads/b.png)",
        "题干 ![](a.png) 请据此求第二问",
        "已知图形如图\n![](/static/uploads/a.png)",
        "下列说法正确的是\n\\begin{choices}\n\\item ![](uploads/a.png)\n"
        "\\item ![](uploads/b.png)\n\\end{choices}\n",
        "题干 \\begin{tabular}{|c|}\\hline ![](uploads/t.png) \\\\ \\hline \\end{tabular}",
        "题干 \\begin{choices}\n\\item A\n\\end{choices} ![](uploads/fig.png)",
        "题干 ![](/static/uploads/a.png) 小问二 ![b](uploads/b.png) ![](c.png)",
        "\\begin{choices}\n\\item ![](x1.png)\n\\item ![](x2.png)\n\\end{choices}",
    ]
    payload = json.dumps(cases, ensure_ascii=False)
    script = (
        _PAPER_STREAM_NODE_STUB.rstrip("\n")
        + "\n"
        + _paper_image_anchor_source()
        + "\nconst cases = "
        + payload
        + ";\nconst out = cases.map(raw => {\n"
        + "    const parts = splitPaperImageAnchors(raw);\n"
        + "    return { raw: raw, anchored: parts.anchored, detached: parts.detached,\n"
        + "        preserve: shouldPreserveInlinePaperImages(raw) };\n"
        + "});\n"
        + "console.log(JSON.stringify(out));\n"
    )

    result = _run_node_script(script)
    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")
    rows = json.loads(result.stdout)
    assert len(rows) == len(cases)

    for case, row in zip(cases, rows):
        assert row["raw"] == case, "node 收到的用例文本被转码改动了，对比不可信"
        anchored, detached = split_image_anchors(case)
        assert row["anchored"] == anchored, (
            f"预览与导出的「留在原锚点正文」不一致：{case!r}"
        )
        assert row["detached"] == detached, (
            f"预览与导出的「题末可分离图簇」不一致：{case!r}"
        )
        assert row["preserve"] is _should_preserve_inline_image_positions(case), (
            f"预览与导出的「图片是否留在原位」不一致：{case!r}"
        )


def test_paper_figure_size_ui_persists_all_three_columns():
    """插图尺寸 UI：三列一起落库，且端点「没传就不动」的契约要在前端配合到位。"""

    paper_source = _read(STATIC_JS_DIR / "paper.js")

    for marker in (
        "PAPER_FIGURE_SIZE_CM",
        "window.setFigureSize = function",
        "paperFigureSizeCm",
        "applyPaperImageLayoutSizes",
        "题末插图尺寸",
    ):
        assert marker in paper_source, f"插图尺寸前端接线缺少 {marker}"

    align_body = paper_source[
        paper_source.index("window.setFigureAlign = function") : paper_source.index(
            "window.setFigureSize = function"
        )
    ]
    # 端点整体覆盖三列，所以没改动的字段必须原样带上，否则「只调位置」会清掉尺寸
    assert "formData.append('figure_size', getQuestionFigSize(persisted));" in align_body, (
        "只调插图位置时漏传 figure_size，会把老师设过的插图尺寸静默清回 auto"
    )
    assert "image_layouts" in align_body, "只调插图位置时逐图布局也必须原样回传"

    size_body = paper_source[
        paper_source.index("window.setFigureSize = function") : paper_source.index(
            "window.showFigureAlignPopover = function"
        )
    ]
    assert "figure_align" in size_body, "只调尺寸时也必须把当前位置一起落库"
    assert "/api/questions/${qid}/figure_align" in size_body

def test_fraction_style_button_previews_before_touching_the_editor():
    """「规范分式」必须预览后确认才写回编辑框，并且要走 input 事件带动实时预览。"""

    index_source = _read(INDEX_PATH)
    editor_source = _read(STATIC_JS_DIR / "editor.js")

    for btn_id, field in (
        ("normalizeContentFractionsBtn", "editContent"),
        ("normalizeAnswerFractionsBtn", "editAnswerMarkdown"),
    ):
        assert f'id="{btn_id}"' in index_source, f"录入页缺少按钮 {btn_id}"
        assert """onclick="applyFractionStyle('""" + field + """')""" in index_source
        start = index_source.index(f'id="{btn_id}"')
        tag = index_source[start - 60: start + 260]
        assert 'type="button"' in tag, "录入表单里的按钮必须显式声明 type，否则会把整页提交掉"

    body = editor_source[
        editor_source.index("window.applyFractionStyle = async function") : editor_source.index(
            "function syncFilterExamStageAvailability()"
        )
    ]
    assert "fetch('/api/format/fractions'" in body, "规范分式必须走后端纯函数，前端不许自己写正则改公式"
    assert "window.confirm(" in body, "改动公式之前必须让老师确认，预览后应用是这条功能的契约"
    assert body.index("window.confirm(") < body.index("box.value = data.text"), (
        "确认必须在写回编辑框之前：先写再问等于没有预览"
    )
    assert "dispatchEvent(new Event('input'" in body, "不派发 input 事件的话，实时预览与草稿缓存都不会跟着刷新"
    assert "fractionBusy" in body, "重复点击不能把两次结果叠加写回同一个编辑框"
    assert "insertText" in body, "整段替换要走 insertText，才能保留浏览器撤销栈"
    assert "尚未保存" in body, "必须说清这只改了编辑框，落库仍然是点保存那一次"


def test_related_question_dropdown_never_fetches_the_whole_bank():
    """十万题红线：录入页「关联题目」下拉不许再整库拉取。

    万题压测里曾观测到每次页面加载都发两次不带分页的 GET /api/questions
    （初始化直接调 refreshRelatedDropdown()），十万题时一次加载就要拖回
    139MB JSON 并生成十万个 <option>。这里钉住改造后的契约：
    1) 初始化不再直接调用，改成点开下拉时懒加载一次；
    2) 基础选项一律走 mode=lean + page_size 的分页摘要；
    3) 选中项不在最新一页里时只定向水合那一道，题号反查也只取 lean 单页。
    """
    import_source = _read(STATIC_JS_DIR / "import.js")
    assert "fetch('/api/questions')\n" not in import_source, "仍存在不带分页的整库裸 GET"
    assert "// Load and populate related questions dropdown\n            refreshRelatedDropdown();" not in import_source, \
        "初始化不许再无条件预取关联下拉"
    assert "primeRelatedDropdownOnce" in import_source, "关联下拉必须改成 focus/pointerdown 懒加载"

    start = import_source.index("function refreshRelatedDropdown")
    body = import_source[start:import_source.index("function associateRelatedQuestion", start)]
    assert "mode=lean" in body and "page_size=${RELATED_DROPDOWN_LEAN_SIZE}" in body, \
        "关联下拉基础选项必须走 lean 分页"
    assert "/api/paper/questions?ids=" in body, "选中题不在最新一页时必须走定向水合而不是整库回退"

    assert "mode=lean&q=${encodeURIComponent('#' + idVal)}" in import_source, \
        "ID 反查必须走 q=#ID 的 lean 单页"


def test_pdf_fast_path_diagnostics_reach_the_import_log():
    """R1 验收口径：PDF 快路径的裁决必须在前端可见。

    后端 routes_paper 在任务完成时把 diagnostics.pdf_fast_path 一起下发，
    但「写进 diagnostics」不等于「用户看得见」——历史上前端零命中 fallback_level/ai_ok。
    这里钉住最小契约：读得到、有门槛（没尝试就保持安静）、只在 PDF 分支说话、
    并且绝不把原始 JSON 甩给老师。
    """

    import_source = _read(STATIC_JS_DIR / "import.js")

    assert "const fast = task.diagnostics.pdf_fast_path || {};" in import_source, \
        "前端必须真的读取 diagnostics.pdf_fast_path，否则验收口径里的拒绝率/回退率仍然不可见"
    assert "if (!isWordTask && task.diagnostics) {" in import_source, \
        "PDF 分支必须显式排除 docx，避免和 Word 提取报告互相抢话"
    assert "if (fast && fast.attempted) {" in import_source, \
        "没进过快路径分支时（pdf_fast_path 是空 dict）必须完全安静"

    helper = _extract_import_js_function(import_source, "formatPdfFastPathDiagnostics")
    assert "if (!fast || !fast.attempted) return entries;" in helper, \
        "格式化函数自己也要兜住缺 attempted 的输入，不依赖调用方守卫"
    assert "JSON.stringify" not in helper and "JSON.stringify" not in import_source[
        import_source.index("if (!isWordTask && task.diagnostics)"):
        import_source.index("if (isWordTask && task.diagnostics)")
    ], "诊断日志只能输出中文句子，不得把原始 JSON 抖给用户"


def test_pdf_fast_path_fallback_levels_each_get_plain_language():
    """三种结局各有各的措辞，且每个键都可能缺失。

    - none / L0_microsplit + ai_ok：正面播报保留题数、分类耗时、token 与保留率；
    - L1_*：中性说明「回退整卷」是设计内路径，不暗示失败；
    - L2_degrade：说明正文已保、分类留空待补、未重烧整卷。
    """

    import_source = _read(STATIC_JS_DIR / "import.js")
    helper = _extract_import_js_function(import_source, "formatPdfFastPathDiagnostics")

    assert "fallbackLevel === 'L2_degrade'" in helper
    assert "fallbackLevel.indexOf('L1_') === 0" in helper
    assert "fallbackLevel === 'none' || fallbackLevel === 'L0_microsplit'" in helper
    assert "fast.ai_ok === true" in helper, "成功文案必须把 ai_ok 当证据，不能只看 fallback_level"

    assert "极速路径生效" in helper and "'success'" in helper
    assert "本地保留 " in helper and "可疑块 " in helper
    assert "已回退整卷拆解" in helper and "题目结果不受影响" in helper, \
        "回退是合法路径，措辞不得让用户以为导入失败"
    assert "裁判判定本卷结构不适合本地切块" in helper, \
        "trustworthy 为 false 时要给出人话解释，不展开技术细节"
    assert "待refine" in helper and "未重烧整卷" in helper

    duration = _extract_import_js_function(import_source, "formatPdfFastPathDuration")
    assert "value >= 1000" in duration and "' 秒'" in duration and "' 毫秒'" in duration, \
        "ai_ms 必须换算成易读时长，不能直接把毫秒数糊在句子里"

    clip = _extract_import_js_function(import_source, "pdfFastPathClip")
    assert "? maxChars : 80" in clip and "'…'" in clip, \
        "degrade_reason / exception_fallback 是 200 字符技术文本，展示前必须截到 80 字符"

    reasons = _extract_import_js_function(import_source, "formatPdfFastPathReasons")
    for key in ("gate_hits", "monotonic_break_at", "coverage_gap_group",
                "total_out_of_range", "answer_key_restart"):
        assert key in reasons, f"fallback_reasons.{key} 必须被翻译，否则原因栏永远是空的"
    assert "/^[A-Za-z]+\\d*_/" in reasons, "闸门代号前缀（G6_ 这类）对老师没有意义，展示前要剥掉"


def test_pdf_fast_path_degrade_pops_a_warning_toast():
    """降级保正文是唯一必须弹窗提醒的情形：分类留空了，老师得知道去补。"""

    import_source = _read(STATIC_JS_DIR / "import.js")
    helper = _extract_import_js_function(import_source, "formatPdfFastPathDiagnostics")

    degrade_branch = helper[helper.index("fallbackLevel === 'L2_degrade'"):
                            helper.index("fallbackLevel.indexOf('L1_')")]
    assert "'warning', true" in degrade_branch, "L2 分支必须把条目标记为需要弹窗"
    assert helper.count(", true)") == 1, "只有降级保正文需要弹窗，回退整卷不许打扰用户"

    caller = import_source[import_source.index("if (!isWordTask && task.diagnostics)"):
                           import_source.index("if (isWordTask && task.diagnostics)")]
    assert re.search(r"entry\.toast[\s\S]{0,160}showToast\(", caller), \
        "条目里的 toast 标记必须在调用侧接上 showToast，否则降级只在日志里躺着没人看见"


def test_word_diagnostics_report_survives_the_pdf_fast_path_change():
    """本次改动只许新增 PDF 分支，Word 那段既有提取报告一个字都不能少。"""

    import_source = _read(STATIC_JS_DIR / "import.js")
    assert "if (isWordTask && task.diagnostics) {" in import_source
    word_start = import_source.index("if (isWordTask && task.diagnostics)")
    word_block = import_source[word_start:import_source.index(
        "renderParsedQuestionsList(parsedQuestionsData)", word_start)]
    for marker in ("Word 提取报告：", "MathType 明细：", "Word 排版语义：",
                   "公式保真校验：", "${reviewCount} 处公式"):
        assert marker in word_block, f"Word 诊断输出被误伤：缺少 {marker}"
    assert import_source.index("if (!isWordTask && task.diagnostics)") < import_source.index(
        "if (isWordTask && task.diagnostics)"), "两段诊断必须互斥并列，PDF 段不得嵌进 Word 段里"


def test_pdf_fast_path_helpers_stay_module_scoped_and_unique():
    """新增助手留在 import.js 模块作用域内：不挂 window、不重复定义。

    级联加载下后加载者会覆盖同名函数，曾造成 showToast 无限递归那类事故；
    通用 UI 助手要么进 api.js，要么就像这里一样只留一份私有实现。
    """

    import_source = _read(STATIC_JS_DIR / "import.js")
    for name in ("pdfFastPathNumber", "pdfFastPathClip", "formatPdfFastPathDuration",
                 "formatPdfFastPathReasons", "formatPdfFastPathDiagnostics"):
        assert import_source.count("function " + name + "(") == 1, f"{name} 必须只定义一次"
        assert ("window." + name) not in import_source, f"{name} 不该挂到 window 上"


def test_pdf_fast_path_reason_never_ends_with_a_double_period():
    """降级/异常原因嵌进「原因：…。」模板，必须先剥掉原文句末标点。

    degrade_reason 由后端格式化成 ``类名: 消息``，而消息几乎总以句号收尾；
    模板又补一个句号，直接拼就是「…120 秒。。」这种给老师看的错别字。
    """

    import_source = _read(STATIC_JS_DIR / "import.js")
    helper = _extract_import_js_function(import_source, "formatPdfFastPathDiagnostics")
    strip = _extract_import_js_function(import_source, "pdfFastPathStripTail")

    assert "pdfFastPathStripTail" in helper, "原因必须走剥尾标点的助手，不能再裸拼 pdfFastPathClip"
    assert "pdfFastPathClip(fast.degrade_reason" not in helper, \
        "裸用 pdfFastPathClip 会让原文句号与模板句号叠加成「。。」"
    assert "[。．.！!？?；;]+$" in strip, "剥尾只吃句末标点，不能误删正文里的标点"
    assert "=== '…'" in strip, "截断已带省略号时不得再剥，否则会吃掉被截断的半句"


def test_choice_image_grid_preview_contract():
    """大庆第 4 题教训的预览层契约：选项图网格强制等宽两列 + 布局字段透传。"""
    js = (STATIC_JS_DIR / "import.js").read_text(encoding="utf-8")
    css = CSS_PATH.read_text(encoding="utf-8")
    assert "function applyParsedCardChoiceImageGrid" in js
    assert "applyParsedCardChoiceImageGrid(contentPrev);" in js
    assert "choices-image-grid" in js
    assert "formData.append('image_layouts'" in js
    assert ".choices-grid.choices-image-grid" in css
    assert "object-fit: contain" in css

def test_unlabeled_floating_image_is_not_glued_into_option_cell():
    """严格守卫：没有标号的整行图不许被吸附进「已经带图」的选项格。

    2024 大庆卷第 4 题实测：MinerU 丢 B 标号后，预览把那张无主图塞进了 A 的格子，
    卷面看着是四个选项配图齐全，源码里它谁都不属于——渲染层替后端圆谎，脏数据就永久
    查不出来。归属交回 figure_repair / 配图条，预览只按源码画。
    前一个选项本身没图时仍照常吸附（那是「标号段 + 尾随图」的正常形态，不是幽灵图）。
    """
    import_source = _read(STATIC_JS_DIR / "import.js")
    start = import_source.index("const PARSED_CHOICE_LABEL_RE")
    end = import_source.index("function parsedCardImageSlot(", start)
    source = import_source[start:end]
    for token in ("!last.hasImage", "hasImage: true"):
        assert token in source, "选项重组缺少幽灵图守卫关键字: " + token

    script = (
        "class FakeEl {\n"
        "  constructor(tag, text, hasImg) {\n"
        "    this.tagName = tag; this.nodes = []; this._text = text || '';\n"
        "    this._hasImg = !!hasImg; this.parentNode = null;\n"
        "  }\n"
        "  get children() { return this.nodes; }\n"
        "  get firstChild() { return this.nodes[0] || null; }\n"
        "  appendChild(n) { this.nodes.push(n); n.parentNode = this; return n; }\n"
        "  insertBefore(n, ref) {\n"
        "    const i = this.nodes.indexOf(ref);\n"
        "    if (i < 0) this.nodes.push(n); else this.nodes.splice(i, 0, n);\n"
        "    n.parentNode = this; return n;\n"
        "  }\n"
        "  removeChild(n) { const i = this.nodes.indexOf(n); if (i >= 0) this.nodes.splice(i, 1); return n; }\n"
        "  querySelector(sel) { return (sel === 'img' && this._hasImg) ? {} : null; }\n"
        "  querySelectorAll() { return []; }\n"
        "  setAttribute() {}\n"
        "  cloneNode() { return new FakeEl(this.tagName, this._text, false); }\n"
        "  get textContent() { return this._text; }\n"
        "  set textContent(v) { this._text = String(v); this.nodes = []; }\n"
        "}\n"
        + source.replace(
            "function groupParsedCardOptionParagraphs(container) {",
            "function groupParsedCardOptionParagraphs(container) {\n"
            "  const _gridMarker = (el) => { el._grid = true; return el; };\n"
            "  if (typeof document === 'undefined') {\n"
            "    globalThis.document = { createElement: (tag) => _gridMarker(new FakeEl(tag.toUpperCase())) };\n"
            "  }\n"
            "  return _groupParsedCardOptionParagraphsInner(container);\n"
            "}\n"
            "function _groupParsedCardOptionParagraphsInner(container) {",
        )
    ) + (
        "// 形态一：A 已经有图，后面又冒出一张无标号图 —— 绝不进 A 的格子\n"
        "const c1 = new FakeEl('DIV');\n"
        "const p1 = (t, img) => { const el = new FakeEl('P', t, img); c1.appendChild(el); return el; };\n"
        "p1('函数在区间上的大致图象为（ ）');\n"
        "p1('A'); p1('', true); p1('', true);\n"
        "p1('B'); p1('', true); p1('C'); p1('', true); p1('D'); p1('', true);\n"
        "groupParsedCardOptionParagraphs(c1);\n"
        "const grids1 = c1.nodes.filter(n => n._grid);\n"
        "if (grids1.length !== 1) throw new Error('应重组出 1 个网格: ' + grids1.length);\n"
        "if (grids1[0].nodes.length !== 4) throw new Error('网格仍应是 4 个选项格: ' + grids1[0].nodes.length);\n"
        "const ghost = c1.nodes.filter(n => !n._grid && n._hasImg && !n._text);\n"
        "if (ghost.length !== 1) throw new Error('无标号图应留在网格之外，实际 ' + ghost.length);\n"
        "// 形态二：文字选项 + 尾随独立配图 —— 正常吸附，不算幽灵图\n"
        "const c2 = new FakeEl('DIV');\n"
        "const p2 = (t, img) => { const el = new FakeEl('P', t, img); c2.appendChild(el); return el; };\n"
        "p2('如图（ ）');\n"
        "p2('A．106'); p2('', true); p2('B．105'); p2('C．104'); p2('D．103');\n"
        "groupParsedCardOptionParagraphs(c2);\n"
        "const grids2 = c2.nodes.filter(n => n._grid);\n"
        "if (grids2.length !== 1) throw new Error('形态二应重组: ' + grids2.length);\n"
        "if (grids2[0].nodes.length !== 4) throw new Error('形态二网格应 4 格: ' + grids2[0].nodes.length);\n"
        "if (c2.nodes.filter(n => !n._grid && n._hasImg).length !== 0) {\n"
        "  throw new Error('文字选项后的尾随图应被收进该选项格');\n"
        "}\n"
        "console.log('OK');\n"
    )
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
