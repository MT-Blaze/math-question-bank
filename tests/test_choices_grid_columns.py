"""选择题网格「列数自适应」的回归锁。

背景（2026-09-25 修）：

组卷预览里一条 4 选项都较长的选择题，本该 4 列一行排，实际却排成 2 列 2 行；
同时超长选项被拦腰折成两行。根因是两条各自都能自圆其说、合在一起彻底失效的判据：

1. **首选列数按源码字符数估算**（`\\begin{choices}` → HTML 那一步）：
   `>18 字符 → 2 列`、`>40 字符 → 1 列`。拿源码字节当像素用，而候选表又被
   `[4,2,1].filter(cols => cols <= preferred)` 按这个估算值裁剪过 ——
   于是「4 列到底装不装得下」这一问**从来没被问过**。
2. **防折行的 `white-space: nowrap` 从未生效**：规则写的是
   `.choices-grid .choices-content > .katex`（直接子选择器），但
   `renderMathInElement` 渲染出的真实 DOM 是
   `.choices-content > span > span.katex`（KaTeX 外面还包了一层无 class 的
   `<span>`）。选择器匹配不到 → white-space 一直是 normal → 内容选择折行 →
   `scrollWidth === clientWidth` → `choicesGridOverflows` 每次都看到「没溢出」

两条叠加的结果：内容折了行、检测器又测不出溢出，列数就此卡死。

修法：首选列数固定 4（交给真实 DOM 测量），测量期强制不折行（`.is-measuring`），
落地后撤掉 nowrap —— 降到 1 列还装不下时才允许折行，与导出侧 exam-zh
「减列、绝不折行」的排印语义对齐。
"""

import json
import re
import shutil
import subprocess
import tempfile

import pytest

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = PROJECT_ROOT / "static"
CSS_PATH = STATIC_DIR / "css" / "app.css"
EDITOR_PATH = STATIC_DIR / "js" / "editor.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _run_node_script(script: str) -> str:
    """把生成好的脚本落盘再交给 node（命令行长度上限，见既有测试同名函数）。"""

    node = shutil.which("node")
    assert node, "Node.js is required for the choices-grid regression"
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


def _extract_function(source: str, signature: str) -> str:
    """按行缩进抠出整个函数文本。

    不走花括号配平：源码里的注释带着成对的引号（`data-preferred-columns="4"`），
    配平法会把引号里的东西当字符串、连带跳过真正的 `}`，一路抠到文件尾。
    本项目这些模块函数一律顶格在 8 个空格、闭合花括号独立成行，按行取更稳。
    """

    lines = source.splitlines()
    for idx, line in enumerate(lines):
        if signature in line:
            indent = len(line) - len(line.lstrip())
            end = idx + 1
            while end < len(lines) and (
                lines[end].strip() != "}"
                or len(lines[end]) - len(lines[end].lstrip()) != indent
            ):
                end += 1
            assert end < len(lines), f"未找到闭合行：{signature}"
            return "\n".join(lines[idx : end + 1])
    raise AssertionError(f"未找到函数：{signature}")


# --------------------------------------------------------------------------
# 可执行部分：把真实的 adaptSingleChoicesGrid 拿出来，用桩 DOM 跑
# --------------------------------------------------------------------------

# 桩要忠实还原一件事：**内容自然宽度是常量，随列数变的是单元格宽度**。
# 4 列时限死在 ~144px，降到 2 列涨到 ~296px，1 列涨到 600px —— 所以「装不下
# 4 列」不意味着「装不下 2 列」，这正是旧实现按字符数一刀切砍掉候选的原因。
_GRID_STUB = r"""
function makeGrid(naturalWidths, gridWidth, hasKatex) {
    var GAP = 8;
    var classes = new Set();
    var trace = [];
    var cells = naturalWidths.map(function (nw) {
        // content 对应真实 DOM 的 .choices-content —— 元素上带着 querySelector，
        // 兜底函数要确认格子里有没有 .katex，桩必须也提供，否则源码跑不起来。
        // 调用方显式传 false 时才模拟「这一题不含公式」（此时守卫生效应拦下缩放）。
        return {
            content: {
                clientWidth: 0,
                scrollWidth: nw,
                querySelector: function (sel) {
                    return (sel === '.katex' && hasKatex !== false) ? {} : null;
                }
            }
        };
    });
    function applyColumns(n) {
        var cellWidth = Math.floor((gridWidth - GAP * (n - 1)) / n);
        cells.forEach(function (c) { c.content.clientWidth = cellWidth; });
    }
    var props = {};
    var grid = {
        isConnected: true,
        clientWidth: gridWidth,
        dataset: { preferredColumns: '4', choiceColumns: null },
        // 收尾还有一趟「超宽公式加横向滚动类」的标记扫描（markUnshrinkableChoiceMath）。
        // 这个桩负责的是列数与缩放口径，公式级标记另有专门用例，这里返回空集即可。
        querySelectorAll: function () { return []; },
        children: cells.map(function (c) {
            return { querySelector: function () { return c.content; } };
        }),
        style: {
            setProperty: function (key, value) {
                // --choices-scale 与列数无关，不影响单元格宽度，只记账。
                if (key === '--choices-scale') {
                    props[key] = value;
                    trace.push({
                        columns: null, scale: value,
                        phase: classes.has('is-measuring') ? 'measure' : 'final',
                        measuring: classes.has('is-measuring')
                    });
                    return;
                }
                applyColumns(parseInt(value, 10));
                // 收尾那次 setProperty 走在 classList.remove 之后，phase 记为 final；
                // 循环的每一次才是「测量」。assert 只挑 phase==='measure' 的看。
                trace.push({
                    columns: value,
                    phase: classes.has('is-measuring') ? 'measure' : 'final',
                    measuring: classes.has('is-measuring')
                });
                grid.dataset.choiceColumns = value;
            },
            // 兜底函数第一步就要回到 1:1，桩必须支持，否则真实源码跑不起来。
            removeProperty: function (key) { delete props[key]; }
        },
        _props: props,
        classList: {
            add: function (c) { classes.add(c); },
            remove: function (c) { classes.delete(c); },
            contains: function (c) { return classes.has(c); }
        },
        _classes: classes,
        _trace: trace
    };
    applyColumns(4);
    return grid;
}
"""


# 兜底专用桩：只测「1 列态下内容宽 vs 可用宽」，所以 clientWidth 由调用方直接给定，
# 不随列数变。内容是否带 .katex 也可单独指定 —— 守卫生效与否全看这一位。
_FIT_GRID_STUB = r"""
function makeFitGrid(cellSpecs) {
    var props = {};
    var log = [];
    var contents = cellSpecs.map(function (spec) {
        return {
            clientWidth: spec.clientW,
            scrollWidth: spec.scrollW,
            querySelector: function (sel) {
                return (sel === '.katex' && spec.hasKatex) ? {} : null;
            }
        };
    });
    var grid = {
        isConnected: true,
        clientWidth: 714,
        querySelectorAll: function () { return []; },
        children: contents.map(function (content) {
            return {
                querySelector: function (sel) {
                    return sel === '.choices-content' ? content : null;
                }
            };
        }),
        style: {
            removeProperty: function (key) {
                delete props[key];
                log.push({ op: 'remove', key: key });
            },
            setProperty: function (key, value) {
                props[key] = value;
                log.push({ op: 'set', key: key, value: value });
            }
        },
        _props: props,
        _log: log
    };
    return grid;
}
"""


def _fit_case_snippet() -> str:
    editor_source = _read(EDITOR_PATH)
    fit = _extract_function(editor_source, "function fitOversizedChoicesContent(grid) {")

    return (
        "\n"
        + _FIT_GRID_STUB
        + "\n"
        + fit
        + "\n"
        "const out = {};\n"
        # A4 一列的可用宽：容器 714px 减去 "A." 标签约 24px
        "const AVAIL = 690;\n"
        "function fit(name, specs) {\n"
        "    const grid = makeFitGrid(specs);\n"
        "    fitOversizedChoicesContent(grid);\n"
        "    out[name] = grid._props['--choices-scale'] || null;\n"
        "}\n"
        # 1238px 的公式：1 列装不下 → 应缩到 690/1238 ≈ 0.557
        "fit('wideFormula', [\n"
        "    { clientW: AVAIL, scrollW: 1238, hasKatex: true },\n"
        "    { clientW: AVAIL, scrollW: 1238, hasKatex: true },\n"
        "    { clientW: AVAIL, scrollW: 1238, hasKatex: true },\n"
        "    { clientW: AVAIL, scrollW: 1238, hasKatex: true }]);\n"
        # 短选项：没溢出就别动字号
        "fit('shortOptions', [\n"
        "    { clientW: AVAIL, scrollW: 100, hasKatex: true },\n"
        "    { clientW: AVAIL, scrollW: 100, hasKatex: true },\n"
        "    { clientW: AVAIL, scrollW: 60, hasKatex: true },\n"
        "    { clientW: AVAIL, scrollW: 80, hasKatex: true }]);\n"
        # 1200px 宽图、格里没有 .katex → 守卫拦下，font-size 缩它不动
        "fit('oversizedImage', [\n"
        "    { clientW: AVAIL, scrollW: 1200, hasKatex: false },\n"
        "    { clientW: AVAIL, scrollW: 1200, hasKatex: false },\n"
        "    { clientW: AVAIL, scrollW: 1200, hasKatex: false },\n"
        "    { clientW: AVAIL, scrollW: 1200, hasKatex: false }]);\n"
        # 1673px：需要的缩放 0.413 < FLOOR 0.5 → 卡在下限
        "fit('beyondFloor', [\n"
        "    { clientW: AVAIL, scrollW: 1673, hasKatex: true },\n"
        "    { clientW: AVAIL, scrollW: 1673, hasKatex: true },\n"
        "    { clientW: AVAIL, scrollW: 1673, hasKatex: true },\n"
        "    { clientW: AVAIL, scrollW: 1673, hasKatex: true }]);\n"
        # 只有 A 格溢出、B/C/D 都装得下 → 仍按最宽那格算，且只写一次（整行统一）
        "fit('onlyOneCellOverflowing', [\n"
        "    { clientW: AVAIL, scrollW: 1238, hasKatex: true },\n"
        "    { clientW: AVAIL, scrollW: 100, hasKatex: true },\n"
        "    { clientW: AVAIL, scrollW: 100, hasKatex: true },\n"
        "    { clientW: AVAIL, scrollW: 100, hasKatex: true }]);\n"
        "console.log('###RESULT###' + JSON.stringify(out) + '###END###');\n"
    )


def _run_fit_cases() -> dict:
    stdout = _run_node_script(_fit_case_snippet())
    match = re.search(r"###RESULT###(.*?)###END###", stdout, re.S)
    assert match, "node 侧没有产出结果"
    return json.loads(match.group(1))


def _cases_snippet() -> str:
    editor_source = _read(EDITOR_PATH)
    # 2026-09-25 性能修复后，单格适配被拆成「整批」路径上的几个小函数 ——
    # adaptSingleChoicesGrid 现在只是 adaptChoicesGridsBatch([grid]) 的薄封装。
    # 回归锁必须跟着抽取**真正干活的那几个**，否则抽到的只是转发那一行，
    # 桩里也找不到 adaptChoicesGridsBatch 而直接 ReferenceError。
    adapt = _extract_function(editor_source, "function adaptSingleChoicesGrid(grid) {")
    batch = _extract_function(editor_source, "function adaptChoicesGridsBatch(grids) {")
    measure = _extract_function(editor_source, "function measureChoicesGridsBatch(live) {")
    apply_cols = _extract_function(editor_source, "function applyChoicesGridColumns(grid, selected) {")
    can_adapt = _extract_function(editor_source, "function canAdaptChoicesGrid(grid) {")
    overflow = _extract_function(editor_source, "function choicesGridOverflows(grid) {")
    preferred = _extract_function(editor_source, "function getPreferredChoicesColumns(grid) {")
    fit = _extract_function(editor_source, "function fitOversizedChoicesContent(grid) {")
    mark = _extract_function(editor_source, "function markUnshrinkableChoiceMath(grid) {")
    assert "is-measuring" in batch, "测量期必须切到 .is-measuring（nowrap 才生效）"
    assert "--choices-scale" in fit, "兜底必须写入 --choices-scale"

    # 批量化是这次性能修复的核心，必须锁住，否则很容易被改回逐格：
    # 每格各加各摘 .is-measuring = 每格触发一次 Tailwind JIT 的全文档扫描。
    # 组卷页实测近 200 格 × 2 ≈ 400 次扫描，直接吃掉 40% CPU；整批后只剩 2 次。
    add_at = batch.index("classList.add('is-measuring')")
    measure_at = batch.index("measureChoicesGridsBatch(live)")
    remove_at = batch.index("classList.remove('is-measuring')")
    assert batch.count("classList.add('is-measuring')") == 1, "测量态只许整批进一次"
    assert batch.count("classList.remove('is-measuring')") == 1, "测量态只许整批出一次"
    assert add_at < measure_at < remove_at, (
        "顺序必须是「整批进测量态 → 整批测量 → 整批撤测量态」；"
        "收尾若还挂着 nowrap，降到 1 列装不下时会退化成横向滚动条"
    )
    # 试列数同样要整批：逐格试会让每格各触发一次强制布局。
    assert "for (const columns of [4, 2, 1])" in measure, "列数候选表必须恒定 [4,2,1]"
    assert "live.map" not in batch, "不得退回逐格 map 测量"

    return (
        "\n"
        + _GRID_STUB
        + "\n"
        + preferred
        + "\n"
        + overflow
        + "\n"
        + fit
        + "\n"
        + mark
        + "\n"
        + can_adapt
        + "\n"
        + apply_cols
        + "\n"
        + measure
        + "\n"
        + batch
        + "\n"
        + adapt
        + "\n"
        "const out = {};\n"
"function run(name, naturalWidths, gridWidth, hasKatex) {\n"
"    const grid = makeGrid(naturalWidths, gridWidth, hasKatex);\n"
        "    adaptSingleChoicesGrid(grid);\n"
        "    out[name] = {\n"
        "        selected: grid.dataset.choiceColumns,\n"
        "        stillMeasuring: grid._classes.has('is-measuring'),\n"
        "        trace: grid._trace,\n"
        "        scales: grid._props['--choices-scale'] || null\n"
        "    };\n"
        "}\n"
        # 容器 600：4 列 cell≈144、2 列 cell≈296、1 列 cell=600
        # 四个选项各 100px → 4 列装得下
        "run('fits4', [100, 100, 100, 100], 600);\n"
        # 各 250px → 4 列挤不下、2 列装得下（旧实现会被字符数判据直接砍到 2 列，
        # 这里则验证它确实量过 4 列才降）
        "run('overflowAt4', [250, 250, 250, 250], 600);\n"
        # 各 900px → 连 1 列都装不下 → 选 1 后撤掉 nowrap，允许折行
        "run('overflowAt1', [900, 900, 900, 900], 600);\n"
        "console.log('###RESULT###' + JSON.stringify(out) + '###END###');\n"
    )


def test_choices_grid_measures_columns_without_wrapping():
    """降列必须建立在「真的不折行」之上，量完再放折行。"""

    stdout = _run_node_script(_cases_snippet())
    match = re.search(r"###RESULT###(.*?)###END###", stdout, re.S)
    assert match, "node 侧没有产出结果"
    data = json.loads(match.group(1))

    # 三种情形落地后都必须摘掉 .is-measuring，否则 1 列装不下时会变成横向滚动条。
    for name in ("fits4", "overflowAt4", "overflowAt1"):
        assert data[name]["stillMeasuring"] is False, name

    # 测量每一步都必须带着 .is-measuring（nowrap 生效，scrollWidth 才可信）
    for name in ("fits4", "overflowAt4", "overflowAt1"):
        measures = [s for s in data[name]["trace"] if s["phase"] == "measure"]
        assert measures, f"{name}：一次测量都没做"
        for step in measures:
            assert step["measuring"] is True, f"{name} 在测 {step['columns']} 列时没开 nowrap"
        # 收尾必须撤掉测量态，否则降到 1 列装不下时会退化成横向滚动条
        finals = [s for s in data[name]["trace"] if s["phase"] == "final"]
        assert finals and finals[-1]["measuring"] is False, f"{name}：落地态还挂着 nowrap"

    assert data["fits4"]["selected"] == "4"
    assert data["overflowAt4"]["selected"] == "2"
    assert data["overflowAt1"]["selected"] == "1"


def test_choices_grid_never_starts_from_a_character_count_estimate():
    """首选列数固定 4；不得再出现按源码字符数分档的阈值得出 grid-cols-1/2。"""

    editor_source = _read(EDITOR_PATH)

    # 旧的字符数分档必须彻底消失（留下的话 preferred=2，候选就再也进不了 4）
    assert "maxLen > 40" not in editor_source, "字符数 >40 → 1 列 的判据已废弃"
    assert "maxLen > 18" not in editor_source, "字符数 >18 → 2 列 的判据已废弃"

    # 首选列数固定写死为 4，且真的被写进 data-preferred-columns
    decision = re.search(
        r"const preferredColumns = (\d+);\s*\n\s*const gridCols = '([^']+)'", editor_source
    )
    assert decision, "未找到 \\begin{choices} 的列数决策"
    assert decision.group(1) == "4", "首选列数必须放开到 4，交给真实测量"
    assert decision.group(2) == "grid-cols-4"

    emitted = re.search(
        r'data-preferred-columns="\$\{(preferredColumns)\}"', editor_source
    )
    assert emitted, "data-preferred-columns 必须来自上面的首选值"


def test_choices_nowrap_rule_matches_the_real_katex_nesting():
    """nowrap 必须写成**后代**选择器：真实 DOM 是 .choices-content > span > .katex。"""

    css = _read(CSS_PATH)

    # 直接子选择器那条（当初一直没生效的元凶）不许再回来
    assert not re.search(r"\.choices-content\s*>\s*\.katex\s*\{", css), (
        ".choices-content > .katex 是失效写法：renderMathInElement 会在 .katex "
        "外面多包一层无 class 的 <span>"
    )

    # 生效的现在必须是「测量期」限定 + 后代选择器
    rule = re.search(
        r"\.choices-grid\.is-measuring \.choices-content \.katex\s*\{[^}]*\}", css
    )
    assert rule, "缺少 .choices-grid.is-measuring .choices-content .katex 规则"
    assert "white-space: nowrap" in rule.group(0)


# --------------------------------------------------------------------------
# 超宽公式兜底（2026-09-25）
# --------------------------------------------------------------------------


def test_oversized_formula_gets_a_scale_factor():
    """降到 1 列仍装不下的公式 → 写入 --choices-scale，且落在 [0.5, 1)。

    物理限制：KaTeX 的 `.katex .base` 自带 white-space:nowrap，公式折不了行，
    1 列装不下时只会剩一条横向滚动条。缩放是唯一的出路。
    """

    data = _run_fit_cases()
    scale = data["wideFormula"]
    assert scale is not None, "超宽公式没有触发缩放兜底"
    value = float(scale)
    assert 0.5 <= value < 1, f"缩放因子应落在 [0.5, 1)，实际 {scale}"
    # 690/1238 ≈ 0.557
    assert abs(value - 690 / 1238) < 0.01, f"缩放因子应等于可用宽/内容宽，实际 {scale}"


def test_short_options_are_left_untouched():
    """没溢出的选项不许动字号——兜底不能给正常题帮倒忙。"""

    data = _run_fit_cases()
    assert data["shortOptions"] is None, "短选项不该被缩小"
    assert data["wideFormula"] is not None, "对照组：同一批用例里超宽公式必须仍被缩小"


def test_oversized_image_without_katex_is_left_untouched():
    """守卫：溢出的格子里没有 .katex（超宽图片）→ 不写 --choices-scale。

    font-size 缩不了 <img>，写了只会白设一个没人用的变量，还让人误以为处理过了。
    """

    data = _run_fit_cases()
    assert data["oversizedImage"] is None, "无 .katex 的溢出内容不该触发缩放"


def test_scale_factor_is_floored_at_half():
    """FLOOR=0.5：宽到 1/0.5 之外的式子停在下限，宁可留滚动条也不缩成糊。"""

    data = _run_fit_cases()
    scale = data["beyondFloor"]
    assert scale is not None, "超下限的式子应当仍设一个值（卡在下限）"
    assert float(scale) == 0.5, f"应卡在 FLOOR=0.5，实际 {scale}"


def test_scale_uses_the_widest_option_and_applies_once_per_row():
    """整行统一缩放：按最宽那格算，整格写一次，保证 A/B/C/D 字号一致。"""

    data = _run_fit_cases()
    only_one = data["onlyOneCellOverflowing"]
    assert only_one is not None, "只溢出 A 格时也要缩"
    assert float(only_one) == pytest.approx(690 / 1238, abs=0.01)


def test_adapt_single_grid_applies_the_scale_fallback():
    """兜底必须挂在 adaptSingleChoicesGrid 收尾：这样 ResizeObserver 重入时会重算。

    这里的 overflowAt1 是「连 1 列都装不下」（600px 容器 / 900px 内容），正是
    要兜底的场景；fits4 与 overflowAt4 没溢出，一个 scale 都不该有。
    """

    stdout = _run_node_script(_cases_snippet())
    match = re.search(r"###RESULT###(.*?)###END###", stdout, re.S)
    assert match, "node 侧没有产出结果"
    data = json.loads(match.group(1))

    assert data["overflowAt1"]["selected"] == "1", "前置条件：这一格确实降到了 1 列"
    assert data["overflowAt1"]["scales"] is not None, (
        "降到 1 列仍装不下时，adaptSingleChoicesGrid 应当调用兜底写入 --choices-scale"
    )
    for name in ("fits4", "overflowAt4"):
        assert data[name]["scales"] is None, f"{name} 没溢出，不该缩字号"


def test_choices_content_font_size_follows_the_grid_scale_variable():
    """CSS 出口：.choices-grid .choices-content 必须真的用 --choices-scale 算字号。

    用 font-size 而不是 transform: scale() —— 后者不改变布局宽度，
    scrollWidth 依旧溢出、滚动条不会消失，缩放文字还容易发虚。
    """

    css = _read(CSS_PATH)
    rule = re.search(
        r"\.choices-grid \.choices-content\s*\{[^}]*\}", css
    )
    assert rule, "找不到 .choices-grid .choices-content 规则"
    block = rule.group(0)
    assert "font-size: calc(1em * var(--choices-scale, 1))" in block, (
        ".choices-content 必须按 --choices-scale 缩字号，否则 JS 写的变量没人消费"
    )
    assert "var(--choices-scale" in block, "缩放变量的读取点缺失"
    # 先剥掉注释再查：注释里恰好在讲为什么不用 transform，别让它自己骗自己。
    declarations = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
    assert "transform:" not in declarations, (
        "transform: scale() 不改变布局宽度，scrollWidth 依旧溢出、滚动条不会消失，别再用"
    )


# --------------------------------------------------------------------------
# 观察器记账：整卷重渲染后必须放掉脱离文档的网格（待办 #28）
# --------------------------------------------------------------------------

_OBSERVER_STUB = r"""
const OBSERVE_CALLS = [];
const UNOBSERVE_CALLS = [];
class FakeResizeObserver {
    constructor(callback) { this.callback = callback; this.targets = []; }
    observe(el) { if (!this.targets.includes(el)) this.targets.push(el); OBSERVE_CALLS.push(el); }
    unobserve(el) {
        const i = this.targets.indexOf(el);
        if (i >= 0) this.targets.splice(i, 1);
        UNOBSERVE_CALLS.push(el);
    }
    disconnect() { this.targets.length = 0; }
}
globalThis.ResizeObserver = FakeResizeObserver;
globalThis.window = globalThis;

// 弹窗/画布容器桩：每次只报告「当前这一代」网格，模拟 innerHTML 整片替换。
function makeRoot(grids) {
    return {
        matches: function () { return false; },
        querySelectorAll: function (sel) {
            return sel === '.choices-grid' ? grids.slice() : [];
        }
    };
}
"""


def _observer_cluster_snippet() -> str:
    editor_source = _read(EDITOR_PATH)
    start = editor_source.index("const choicesGridResizeObservers = new WeakMap();")
    end = editor_source.index("window.adaptChoicesGridLayout = adaptChoicesGridLayout;")
    cluster = editor_source[start:end]

    # 先锁静态契约：登记表、回收函数、调用点、登记点四样缺一不可。
    assert "const choicesGridObserved = new Set();" in cluster, "缺少已 observe 目标登记表"
    assert "function pruneDetachedChoicesGridTargets()" in cluster, "缺少僵尸目标回收函数"
    assert "pruneDetachedChoicesGridTargets();" in cluster, "adapt 路径上没有调用回收"
    assert "choicesGridObserved.add(grid);" in cluster, "observe 时没有登记目标"
    # 回收必须发生在登记之前，否则本轮新登记的活目标会被下一轮误杀。
    assert cluster.index("pruneDetachedChoicesGridTargets();") < cluster.index(
        "choicesGridObserved.add(grid);"
    ), "回收必须排在登记之前"

    return (
        "\n"
        + _GRID_STUB
        + "\n"
        + _OBSERVER_STUB
        + "\n"
        + cluster
        + "\n"
        "const out = { rounds: [], sameGenerationReadapt: null };\n"
        "let current = [];\n"
        "function rerender(count) {\n"
        "    // 整卷重渲染：上一代网格整体脱离文档，新一代顶上（与 innerHTML 替换同形）\n"
        "    current.forEach(function (grid) { grid.isConnected = false; });\n"
        "    current = [];\n"
        "    for (var i = 0; i < count; i++) current.push(makeGrid([100, 100, 100, 100], 600));\n"
        "    adaptChoicesGridLayout(makeRoot(current));\n"
        "    out.rounds.push({\n"
        "        observeTotal: OBSERVE_CALLS.length,\n"
        "        unobserveTotal: UNOBSERVE_CALLS.length,\n"
        "        roTargets: choicesGridResizeObserver.targets.length,\n"
        "        registrySize: choicesGridObserved.size\n"
        "    });\n"
        "}\n"
        "for (var round = 0; round < 5; round++) rerender(2);\n"
        # 同一代网格重复 adapt（编辑器预览里的常态）不该放掉任何活目标
        "const liveBefore = choicesGridResizeObserver.targets.length;\n"
        "adaptChoicesGridLayout(makeRoot(current));\n"
        "out.sameGenerationReadapt = { liveBefore: liveBefore, liveAfter: choicesGridResizeObserver.targets.length, unobserveTotal: UNOBSERVE_CALLS.length };\n"
        "console.log('###RESULT###' + JSON.stringify(out) + '###END###');\n"
    )


def test_detached_choices_grids_are_unobserved_on_rerender():
    """#28：重渲染不能把脱离文档的网格留在 ResizeObserver 里。

    浏览器实测（44 题长卷 / 22 个网格）：11 轮整卷重渲染后 observe 调用 264 次、
    unobserve 0 次 —— 242 个僵尸目标连同子树被观察器强引用攥着，页面越用越沉。
    这里用桩 DOM 复现同一序列：观察表规模必须始终等于当前代网格数。
    """

    stdout = _run_node_script(_observer_cluster_snippet())
    match = re.search(r"###RESULT###(.*?)###END###", stdout, re.S)
    assert match, "node 侧没有产出结果"
    data = json.loads(match.group(1))
    rounds = data["rounds"]

    assert len(rounds) == 5
    for index, row in enumerate(rounds, start=1):
        assert row["observeTotal"] == 2 * index, f"第 {index} 轮 observe 记账不符：{row}"
        assert row["roTargets"] == 2, (
            f"第 {index} 轮观察器仍持有 {row['roTargets']} 个目标，僵尸目标没回收：{row}"
        )
        assert row["registrySize"] == 2, f"第 {index} 轮登记表与实观不符：{row}"
    assert rounds[-1]["unobserveTotal"] == 8, (
        "5 轮 × 每轮放掉上一代 2 个 = 8 次 unobserve，多一分说明误杀了活目标"
    )

    again = data["sameGenerationReadapt"]
    assert again["liveBefore"] == again["liveAfter"] == 2, (
        "同一代网格重复 adapt 不该动已有目标（编辑器预览靠这条保持观察连续）"
    )
    assert again["unobserveTotal"] == 8, "活目标被误 unobserve：回收判据必须只看 isConnected"


# --------------------------------------------------------------------------
# 超宽公式横向滚动（移植自上游 V2.7.0）：只标真正装不下的那一条
# --------------------------------------------------------------------------

_MARK_JS = r"""
function makeMarkMath(spec) {
    const item = {
        classes: (spec.classes || []).slice(),
        closest: function (sel) {
            if (sel === ".katex-display") return spec.inDisplay ? { nodeType: 1 } : null;
            if (sel === ".choices-content") {
                return spec.noContent ? null : { clientWidth: spec.contentWidth };
            }
            return null;
        },
        getBoundingClientRect: function () { return { width: spec.width }; },
    };
    item.classList = {
        add: function (name) { if (item.classes.indexOf(name) === -1) item.classes.push(name); },
        remove: function (name) {
            const at = item.classes.indexOf(name);
            if (at !== -1) item.classes.splice(at, 1);
        },
        contains: function (name) { return item.classes.indexOf(name) !== -1; },
    };
    return item;
}

function makeMarkGrid(specs, connected) {
    const grid = {
        isConnected: connected !== false,
        queryCalls: 0,
        clientWidth: 600,
        items: specs.map(makeMarkMath),
    };
    grid.querySelectorAll = function (sel) {
        grid.queryCalls += 1;
        if (sel === ".choices-math-scroll") {
            return grid.items.filter(function (m) {
                return m.classList.contains("choices-math-scroll");
            });
        }
        return grid.items.slice();
    };
    return grid;
}

function markedFlags(grid) {
    return grid.items.map(function (m) { return m.classList.contains("choices-math-scroll"); });
}
"""


def _mark_snippet() -> str:
    editor_source = _read(EDITOR_PATH)
    mark = _extract_function(editor_source, "function markUnshrinkableChoiceMath(grid) {")
    assert "querySelectorAll('.choices-math-scroll')" in mark, (
        "清理必须走同一条选择器，否则上一次的滚动态会赖在 DOM 上摘不掉"
    )

    return (
        "\n"
        + mark
        + "\n"
        + _MARK_JS
        + "\n"
        "const out = {};\n"
        "const mixed = makeMarkGrid([{ width: 260, contentWidth: 200 }, "
        "{ width: 120, contentWidth: 200 }]);\n"
        "markUnshrinkableChoiceMath(mixed);\n"
        "out.mixedRow = markedFlags(mixed);\n"
        "out.mixedQueries = mixed.queryCalls;\n"
        "const display = makeMarkGrid([{ width: 999, contentWidth: 200, inDisplay: true }]);\n"
        "markUnshrinkableChoiceMath(display);\n"
        "out.displayFormula = markedFlags(display);\n"
        "const orphans = makeMarkGrid([{ width: 900, contentWidth: 200, noContent: true }]);\n"
        "markUnshrinkableChoiceMath(orphans);\n"
        "out.orphanFormula = markedFlags(orphans);\n"
        "const stale = makeMarkGrid([\n"
        "    { width: 100, contentWidth: 200, classes: ['choices-math-scroll'] },\n"
        "    { width: 900, contentWidth: 200, inDisplay: true, "
        "classes: ['choices-math-scroll'] },\n"
        "    { width: 900, contentWidth: 200, noContent: true, "
        "classes: ['choices-math-scroll'] },\n"
        "]);\n"
        "markUnshrinkableChoiceMath(stale);\n"
        "out.staleClassCleared = markedFlags(stale);\n"
        "const detached = makeMarkGrid(\n"
        "    [{ width: 900, contentWidth: 200, classes: ['choices-math-scroll'] }], false\n"
        ");\n"
        "markUnshrinkableChoiceMath(detached);\n"
        "out.detachedQueries = detached.queryCalls;\n"
        "out.detachedFlags = markedFlags(detached);\n"
        "console.log('###RESULT###' + JSON.stringify(out) + '###END###');\n"
    )


def test_mark_unshrinkable_choice_math_marks_only_real_overflow():
    """抄自上游 V2.7.0：1 列 + 缩到字号下限仍装不下的行内公式，给它自己加横向滚动。

    重点是"只标真溢出"：块级公式（.katex-display）本来就该整行铺开、不归这条路径管；
    量不到 .choices-content 宽度的孤儿节点不能瞎标；每次调用先整体清一遍再重标，
    否则容器变宽或撤掉缩放之后，旧的滚动态会一直残留。脱离文档的网格直接返回，
    一次查询都不该发 —— 组卷页整卷重渲染时这里是热路径。
    """

    stdout = _run_node_script(_mark_snippet())
    match = re.search(r"###RESULT###(.*?)###END###", stdout, re.S)
    assert match, "node 侧没有产出结果"
    data = json.loads(match.group(1))

    assert data["mixedRow"] == [True, False], (
        f"只有真溢出（260 > 200）的那条该进滚动态：{data['mixedRow']}"
    )
    assert data["mixedQueries"] == 2, "一轮标记 = 一次清理 + 一次扫描，多一趟就是重复遍历"
    assert data["displayFormula"] == [False], ".katex-display 是块级公式，不参与横向滚动"
    assert data["orphanFormula"] == [False], "量不到 .choices-content 宽度的公式不许标"
    assert data["staleClassCleared"] == [False, False, False], (
        f"陈旧滚动态必须整体清掉，含 display / 孤儿两条早退分支：{data['staleClassCleared']}"
    )
    assert data["detachedQueries"] == 0, "脱离文档的网格一次查询都不该发"
    assert data["detachedFlags"] == [True], "脱离文档的网格不该被改动（早退，不清也不标）"
