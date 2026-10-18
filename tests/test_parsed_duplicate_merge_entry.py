# -*- coding: utf-8 -*-
"""查重审查弹窗「并入已有题（仅追加来源）」入口的回归测试。

背景：拆卷整批导入时走 `openParsedDuplicateReviewModal(..., 'batch')`，而底部公共
并入按钮原先只对 mode === 'single' 显示，导致批量流程看不到任何「追加来源」入口。
现在改为每个疑似题区块自带并入按钮（目标明确），底部公共按钮在整批下也可见且作用
对象随点击的区块同步；被并入的题目要从整批入库清单里剔除，不能作为新题再写一遍。
"""

from pathlib import Path
import shutil
import subprocess
import tempfile
from modal_transition_stub import MODAL_TRANSITION_STUB_JS  # 弹窗助手在桩里同步揭示


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = PROJECT_ROOT / "static"
STATIC_JS_DIR = STATIC_DIR / "js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _import_source() -> str:
    return _read(STATIC_JS_DIR / "import.js")


def _extract(start_marker: str, stop_marker: str) -> str:
    source = _import_source()
    start = source.index(start_marker)
    stop = source.index(stop_marker, start)
    return source[start:stop]


# ---------------------------------------------------------------------------
# 静态结构断言
# ---------------------------------------------------------------------------


def test_each_section_renders_its_own_merge_button():
    """阶段 1：逐题队列把【追加来源】【去重】接到当前冲突题的候选；
    单题模式并入成功后以 'merged' 结束审查（保存流程据此中止导入）。"""
    source = _import_source()
    # 队列控制器与动作分发必须存在且按钮绑定到逐题处理
    assert "renderParsedDuplicateQueueEntry" in source
    assert "handleParsedDuplicateQueueAction" in source
    assert "closeParsedDuplicateReviewModal('merged')" in source


def test_merged_indices_map_back_to_parsed_question_index():
    """并入登记的是拆卷结果下标，必须能反查到题目真实下标，否则整批剔除会剔错题。"""
    source = _import_source()
    # entries 要带 index，openParsedDuplicateReviewEntryMerge 才能登记正确下标
    assert "index: index,\n                result:" in source
    assert "Number.isInteger(entry.index)" in source
    # 整批导入据此把并入过的题从入库清单剔除
    assert "indicesToSave = indicesToSave.filter(index => !mergedSet.has(index));" in source


def test_merge_entry_rows_only_when_candidate_has_id():
    """没有带 id 的候选（例如纯批内重复）不渲染并入按钮，避免点开后空转。"""
    source = _import_source()
    assert "const bestCandidate = parsedDuplicateReviewBestCandidate(result);" in source
    # 阶段 1：并入/去重按钮只在当前冲突题存在带 id 候选时出现
    assert "const hasTarget = Boolean(candidate && candidate.id);" in source
    assert "const mergeAvailable = mode !== 'inspect' && hasTarget" in source


# ---------------------------------------------------------------------------
# 真实 JS 执行
# ---------------------------------------------------------------------------

_SHIM = r"""
// 被测函数体共用的模块级状态（真实代码里声明在 import.js 的 IIFE 顶部）
let parsedDuplicateReviewResolver = null;
let parsedDuplicateQueueEntries = [];
let parsedDuplicateQueueMode = 'batch';
let parsedDuplicateQueueIndex = 0;
let parsedDuplicateQueueCloseMergeOnSuccess = true;
const parsedDuplicateIndependentIndices = new Set();
const parsedDuplicateSkippedIndices = new Set();
const parsedDuplicateAssociationRequests = [];
let parsedDuplicateReviewMergeIndex = -1;
let parsedDuplicateReviewMergeBtnEl = null;
const parsedDuplicateMergedIndices = new Set();
// 双栏对照后 renderParsedDuplicateReview 会读拆卷数据取左栏正文，替身里补一份
let parsedQuestionsData = [];

function makeClassList() {
    const set = new Set();
    return {
        add(...cs) { cs.forEach(c => set.add(c)); },
        remove(...cs) { cs.forEach(c => set.delete(c)); },
        contains(c) { return set.has(c); },
        toggle(c, force) {
            const on = force === undefined ? !set.has(c) : Boolean(force);
            if (on) set.add(c); else set.delete(c);
            return on;
        }
    };
}
class El {
    constructor(tag) {
        this.tagName = String(tag).toUpperCase();
        this.children = [];
        this.attrs = {};
        this.listeners = {};
        this.dataset = {};
        this.classList = makeClassList();
        this.className = '';
        this._text = '';
    }
    appendChild(child) { this.children.push(child); child.parentElement = this; return child; }
    append(...cs) { cs.forEach(c => this.appendChild(c)); }
    removeChild(child) { this.children = this.children.filter(c => c !== child); }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
    click() {
        (this.listeners.click || []).forEach(fn => fn({ target: this }));
        if (typeof this.onclick === 'function') this.onclick();
    }
    setAttribute(k, v) { this.attrs[k] = v; }
    getAttribute(k) { return this.attrs[k]; }
    removeAttribute(k) { delete this.attrs[k]; }
    querySelector() { return null; }
}
function findButton(node, keyword) {
    if (node && node.tagName === 'BUTTON' && String(node.textContent).indexOf(keyword) >= 0) {
        return node;
    }
    const kids = node && node.children ? node.children : [];
    for (const child of kids) {
        const hit = findButton(child, keyword);
        if (hit) return hit;
    }
    return null;
}
"""


def _run_node(script: str) -> None:
    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    # 命令行 8191 字符上限：阶段 1 代码块必须落临时文件执行。
    with tempfile.NamedTemporaryFile(
        "w", suffix=".js", encoding="utf-8", delete=False
    ) as handle:
        handle.write(script)
        path = handle.name
    try:
        result = subprocess.run(
            [node, path],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr + result.stdout
    finally:
        Path(path).unlink(missing_ok=True)


def _extract_func(source: str, name: str) -> str:
    """按花括号配平切出**整个函数**（含结尾换行）。"""
    sig = "        function " + name + "("
    assert source.count(sig) == 1, f"锚点不唯一: {name}"
    start = source.index(sig)
    depth = 0
    k = start
    while True:
        line_end = source.index(chr(10), k)
        line = source[k:line_end]
        depth += line.count("{") - line.count("}")
        if depth == 0 and k > start:
            return source[start:line_end + 1] + chr(10)
        k = line_end + 1


def _render_harness() -> str:
    """渲染区块并入按钮所需的真实代码 + 依赖替身。"""
    block = _extract(
        "function renderParsedDuplicateReview(", "function openMergeSourceModal("
    )
    return (
        _SHIM
        + block
        + r"""
let toasts = [];
let closedActions = [];
let mergeCalls = [];
let mergeOnSuccess = null;
function showToast(message, kind) { toasts.push({ message: message, kind: kind }); }
function closeParsedDuplicateReviewModal(action) { closedActions.push(action); }
function openMergeSourceModal(id, seq, onSuccess) {
    mergeCalls.push({ id: id, seq: seq });
    mergeOnSuccess = onSuccess;
}
function prepareParsedDuplicateCandidateRendering() {}
function appendDuplicateCandidate() {}
function duplicateBatchMatchCount() { return 0; }
const document = {
    createElement(tag) { return new El(tag); },
    createTextNode(text) { return { textContent: String(text) }; },
    __cache: {},
    // 同一个 id 必须返回同一个替身，否则断言拿到的不是被测代码操作的那个元素
    getElementById(id) {
        if (!document.__cache[id]) document.__cache[id] = new El('div');
        return document.__cache[id];
    }
};
"""
    )


def _batch_entries():
    # 阶段 1 徽标口径：exact + EXACT_TEXT（无来源可比 → 维持 exact），
    # probable + 无来源 → 同题异源；两者都开放【追加来源】入口。
    return [
        {
            "label": "拆解结果第 2 题",
            "index": 2,
            "result": {
                "level": "exact",
                "candidates": [
                    {"id": 12, "seq_num": 12, "level": "exact", "score": 0.99,
                     "content": "aaa", "reasons": ["EXACT_TEXT"]},
                    {"id": 13, "seq_num": 13, "level": "exact", "score": 0.97,
                     "content": "bbb", "reasons": ["EXACT_TEXT"]},
                ],
                "batch_matches": [],
            },
        },
        {
            "label": "拆解结果第 5 题",
            "index": 5,
            "result": {
                "candidates": [{"id": 34, "seq_num": 34, "score": 0.9, "content": "ccc"}],
                "batch_matches": [],
            },
        },
        {
            "label": "拆解结果第 7 题",
            "index": 7,
            "result": {"candidates": [], "batch_matches": []},
        },
    ]


def _queue_merge_harness() -> str:
    """逐题队列里【追加来源】所需的真实代码 + 依赖替身（阶段 1）。"""
    block = _extract(
        "        // 查重审查弹窗·阶段 1",
        "        function applySaveTimeDuplicateConflict(",
    )
    helper_block = _extract(
        "        function duplicateReasonLabel(reason) {",
        "        function resetParsedDuplicateCandidateRendering()",
    )
    source = _import_source()
    support_block = (
        _extract(
            "        // lg 断点 = 1024px；窄屏把 data-compare 切成 stack",
            "        let parsedDuplicateReviewResizeBound = false;",
        )
        + _extract_func(source, "parsedDuplicateReviewImageStrip")
        + _extract_func(source, "parsedDuplicateReviewCurrentSource")
        + _extract_func(source, "buildParsedDuplicateReviewCurrentColumn")
        + _extract_func(source, "parsedDuplicateReviewBestCandidate")
        + _extract_func(source, "collectImportSourceFields")
    )
    return (
        _SHIM
        + helper_block
        + support_block
        + block
        + r"""
let toasts = [];
let closedActions = [];
let mergeCalls = [];
let mergeOnSuccess = null;
function showToast(message, kind) { toasts.push({ message: message, kind: kind }); }
function closeParsedDuplicateReviewModal(action) { closedActions.push(action); }
function openMergeSourceModal(id, seq, onSuccess, preset) {
    mergeCalls.push({ id: id, seq: seq, preset: preset });
    mergeOnSuccess = onSuccess;
}
function prepareParsedDuplicateCandidateRendering() {}
function resetParsedDuplicateCandidateRendering() {}
function bindParsedDuplicateReviewResize() {}
function appendDuplicateCandidate() {}
function duplicateBatchMatchCount() { return 0; }
const window = { MathBankModal: { open() {}, close() {} }, matchMedia() { return { matches: true }; }, addEventListener() {}, MathBankSafe: { safeImageUrl: p => p, sanitizePlainText: s => String(s || '') } };
const document = {
    createElement(tag) { return new El(tag); },
    createTextNode(text) { return { textContent: String(text) }; },
    __cache: {},
    getElementById(id) {
        if (!document.__cache[id]) document.__cache[id] = new El('div');
        return document.__cache[id];
    }
};
""" + MODAL_TRANSITION_STUB_JS
    )


def test_batch_queue_merge_targets_current_candidate_and_advances():
    """整批队列：【追加来源】只作用于当前冲突题的候选；并入成功后登记题目下标、
    自动前进到下一道冲突题，弹窗保持打开（不关闭）。"""
    script = (
        _queue_merge_harness()
        + r"""
const entries = __ENTRIES__;
openParsedDuplicateReviewModal(entries, 'batch');
if (mergeCalls.length !== 0) throw new Error('未点击不得打开并入弹窗');
const mergeBtn = document.getElementById('duplicateReviewMergeBtn');
if (mergeBtn.classList.contains('hidden')) throw new Error('首题有候选时追加来源按钮应可见');
mergeBtn.click();
if (mergeCalls.length !== 1 || mergeCalls[0].id !== 12) {
    throw new Error('第一道应并入库第 12 题，实际 ' + JSON.stringify(mergeCalls));
}
mergeOnSuccess();
if (!parsedDuplicateMergedIndices.has(2)) {
    throw new Error('并入后未登记题目下标 2: ' + Array.from(parsedDuplicateMergedIndices));
}
if (closedActions.length !== 0) {
    throw new Error('整批并入不得关闭审查弹窗，实际 ' + JSON.stringify(closedActions));
}
// 前进到第二道冲突题：目标切到 34
mergeBtn.click();
if (mergeCalls[1].id !== 34) {
    throw new Error('第二道应并入库第 34 题，实际 ' + JSON.stringify(mergeCalls[1]));
}
mergeOnSuccess();
if (!parsedDuplicateMergedIndices.has(5)) {
    throw new Error('并入后未登记题目下标 5');
}
if (parsedDuplicateMergedIndices.size !== 2) {
    throw new Error('并入登记表应累积 2 条，实际 ' + parsedDuplicateMergedIndices.size);
}
""".replace("__ENTRIES__", _repr_js(_batch_entries()))
    )
    _run_node(script)


def test_single_mode_merge_closes_review_modal():
    """单题：并入成功后必须结束审查（返回 'merged'），保存流程据此中止本次导入。"""
    script = (
        _queue_merge_harness()
        + r"""
const entry = {
    label: '拆解结果第 1 题',
    index: 0,
    result: { level: 'probable', candidates: [{ id: 7, seq_num: 7, level: 'probable', score: 1, reasons: ['HIGH_TEXT_SIMILARITY'] }], batch_matches: [] }
};
openParsedDuplicateReviewModal([entry], 'single');
const mergeBtn = document.getElementById('duplicateReviewMergeBtn');
if (mergeBtn.classList.contains('hidden')) throw new Error('单题模式应显示追加来源入口');
mergeBtn.click();
if (mergeCalls.length !== 1 || mergeCalls[0].id !== 7) {
    throw new Error('单题并入目标错误: ' + JSON.stringify(mergeCalls));
}
mergeOnSuccess();
if (closedActions.length !== 1 || closedActions[0] !== 'merged') {
    throw new Error('单题并入后应以 merged 结束审查，实际 ' + JSON.stringify(closedActions));
}
"""
    )
    _run_node(script)


def test_inspect_mode_hides_all_decision_buttons():
    """查重依据（inspect）是只读视图：并入/去重/相似题/独立保存一律隐藏。"""
    script = (
        _queue_merge_harness()
        + r"""
const entries = __ENTRIES__;
openParsedDuplicateReviewModal(entries, 'inspect');
['duplicateReviewMergeBtn', 'duplicateReviewDedupeBtn', 'duplicateReviewAssociateBtn',
 'duplicateReviewIndependentBtn', 'duplicateReviewSkipBtn', 'duplicateReviewSkipAllBtn']
    .forEach(id => {
        if (!document.getElementById(id).classList.contains('hidden')) {
            throw new Error('inspect 模式应隐藏按钮: ' + id);
        }
    });
""".replace("__ENTRIES__", _repr_js(_batch_entries()))
    )
    _run_node(script)


def _repr_js(value) -> str:
    """把 Python 侧的测试常量转成 JS 字面量（结构固定，逐字符写出更稳）。"""
    return repr(value).replace("'", '"').replace("True", "true").replace("False", "false")
