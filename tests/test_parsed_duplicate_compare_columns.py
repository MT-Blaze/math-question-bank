# -*- coding: utf-8 -*-
"""查重审查弹窗「左右双栏对照」的回归测试。

背景：查重弹窗原先是单栏候选列表，用户要在「当前待导入题」和「已收录候选」之间来回
滚动比对。现在每个疑似题区块内部改成 grid 双栏：左栏 sticky 展示当前待导入题，
右栏（独立滚动容器）展示候选列表（多候选用序号 tab 一次对照一道）。

两处必须锁死的坑：
1. 候选题干是懒渲染的，IntersectionObserver 的 root 必须与右栏的滚动容器一致，
   否则候选题干永远停在「进入可见区域后自动渲染题干…」。
2. 被切到隐藏的候选 pane 不会触发观察器，所以切到哪张才渲染哪张。
"""

from pathlib import Path
import shutil
import subprocess
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


def _repr_js(value) -> str:
    return repr(value).replace("'", '"').replace("True", "true").replace("False", "false")


def _run_node(script: str) -> None:
    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    # 命令行 8191 字符上限：阶段 1 代码块必须落临时文件执行。
    import tempfile
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


# ---------------------------------------------------------------------------
# 静态结构断言
# ---------------------------------------------------------------------------


def test_modal_is_wide_enough_for_two_columns():
    """弹窗必须加宽到 max-w-6xl，否则 768px 放不下左右两栏。"""
    index_source = _read(STATIC_DIR / "index.html")
    start = index_source.index('id="parsedDuplicateReviewModal"')
    block = index_source[start:start + 800]
    assert "max-w-6xl" in block, "查重审查弹窗应加宽到 max-w-6xl"
    assert "max-h-[88vh]" in block, "弹窗高度约束保持不变"
    assert "max-w-3xl" not in block, "不应再退回窄弹窗"


def test_two_column_layout_markers_present():
    """阶段 1 双栏语义翻转（左=已有题，右=待导入题），但 grid/左右栏标记必须还在。"""
    source = _import_source()
    assert "grid.className = 'mt-3 grid gap-3 lg:grid-cols-2';" in source
    assert "left.setAttribute('data-compare-left', 'true');" in source
    assert "right.setAttribute('data-compare-right', 'true');" in source
    assert "已有题（题库）" in source
    assert "待导入题（本次拆解）" in source
    assert "parsedDuplicateReviewApplyResponsive" in source


def test_candidate_observer_root_is_the_modal_list():
    """懒渲染 root 固定为弹窗列表容器，不再依赖 data-candidate-scroll 祖先。

    旧断言钉的是「root 靠向上找 data-candidate-scroll 祖先解析」——但全项目从未有
    任何地方设置过该属性（只有消费者读取），导致 root 恒为 null、观察器永不建立、
    左栏题干永远停在占位文字。那是 bug，不是契约，故随修复一并改写。
    """
    source = _import_source()
    assert "const parsedDuplicateCandidateObservers = new Map();" in source
    assert "function parsedDuplicateCandidateObserverFor(root) {" in source
    # root 解析改为直接取弹窗列表容器；旧的按属性找祖先的函数必须已移除
    assert "function parsedDuplicateCandidateListRoot() {" in source
    assert "return document.getElementById('parsedDuplicateReviewList');" in source
    assert "parsedDuplicateCandidateScrollRoot" not in source
    # data-candidate-scroll 只允许作为历史说明出现在注释里，不得再有读取代码
    code_lines = [
        line for line in source.splitlines()
        if "data-candidate-scroll" in line and not line.strip().startswith("//")
    ]
    assert code_lines == [], f"data-candidate-scroll 仍被代码引用: {code_lines}"
    # 调度顺序：首屏 forceSync → 视口内同步 → 观察器懒渲染
    assert "function scheduleParsedDuplicateCandidateRender(container, text, forceSync) {" in source
    assert "if (forceSync) {" in source
    assert "if (parsedDuplicateCandidateInViewport(container)) {" in source
    assert "const root = parsedDuplicateCandidateListRoot();" in source
    assert "const observer = parsedDuplicateCandidateObserverFor(root);" in source
    # 首屏 pane 认领链：renderParsedDuplicateQueueEntry 造标记 → 比较网格认领 → appendDuplicateCandidate 传 forceSync
    assert "parsedDuplicateQueueFirstPaneEl = document.createElement('div');" in source
    assert "const pane = parsedDuplicateQueueFirstPaneEl || document.createElement('div');" in source
    assert "container === parsedDuplicateQueueFirstPaneEl" in source


# ---------------------------------------------------------------------------
# 真实 JS 执行：区块渲染
# ---------------------------------------------------------------------------

_BASE_SHIM = r"""
let parsedDuplicateReviewResolver = null;
let parsedDuplicateQueueEntries = [];
let parsedDuplicateQueueFirstPaneEl = null;
let parsedDuplicateQueueMode = 'batch';
let parsedDuplicateQueueIndex = 0;
let parsedDuplicateQueueCloseMergeOnSuccess = true;
const parsedDuplicateIndependentIndices = new Set();
const parsedDuplicateSkippedIndices = new Set();
const parsedDuplicateAssociationRequests = [];
let parsedDuplicateReviewMergeIndex = -1;
let parsedDuplicateReviewMergeBtnEl = null;
const parsedDuplicateMergedIndices = new Set();
let parsedQuestionsData = [];

let WIDE = true;
const created = [];
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
        this.value = '';
        this._text = '';
        // 视口判定替身：默认给一个落在 window.innerHeight(800) 内的矩形，
        // 具体用例可覆盖 __rect 模拟「在视口内 / 视口外」。
        this.__rect = { top: 10, bottom: 30, left: 0, right: 100 };
    }
    getBoundingClientRect() { return this.__rect; }
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
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
    hasAttribute(k) { return k in this.attrs; }
    removeAttribute(k) { delete this.attrs[k]; }
    // 只支持 [attr] 形态的选择器（代码里用它找 data-compare-root）
    querySelector(selector) {
        const all = this.querySelectorAll(selector);
        return all[0] || null;
    }
    querySelectorAll(selector) {
        if (typeof selector === 'string' && selector.indexOf('[') === 0) {
            const key = selector.slice(1, -1).split('=')[0];
            const found = [];
            const walk = node => (node.children || []).forEach(child => {
                if (child && typeof child.getAttribute === 'function' && child.getAttribute(key) !== null) {
                    found.push(child);
                }
                walk(child);
            });
            walk(this);
            return found;
        }
        return [];
    }
}
"""


_WINDOW_SHIM = r"""
const window = {
    matchMedia(query) { return { matches: WIDE }; },
    addEventListener() {},
    MathBankModal: { open() {}, close() {} },
    MathBankSafe: { safeImageUrl: p => p, sanitizePlainText: s => String(s || '') },
    renderQuestionPreviewContent(container, text) {
        renderedPreviews.push(String(text));
        container.textContent = 'PREVIEW:' + String(text);
    }
};
""" + MODAL_TRANSITION_STUB_JS


_DOC_STUB = r"""
const __cardNodes = {};
// 拆卷卡片替身：卡内正文框的值必须能拿到（左栏数据源第一优先级）
function makeCard(index, content) {
    const textarea = new El('textarea');
    textarea.value = content;
    const card = new El('div');
    card.querySelector = selector => (selector.indexOf('textarea') >= 0 ? textarea : null);
    __cardNodes[`parsed-card-${index}`] = card;
}
const document = {
    createElement(tag) { const el = new El(tag); created.push(el); return el; },
    createTextNode(text) { return { textContent: String(text) }; },
    __cache: {},
    getElementById(id) {
        if (__cardNodes[id]) return __cardNodes[id];
        if (!document.__cache[id]) document.__cache[id] = new El('div');
        return document.__cache[id];
    }
};
"""


_RENDER_STUBS = r"""
function showToast() {}
function closeParsedDuplicateReviewModal() {}
function openMergeSourceModal(id, seq, onSuccess) {}
function prepareParsedDuplicateCandidateRendering() {}
function duplicateBatchMatchCount() { return 0; }
function renderQuestionPreviewContent(container, text) {
    renderedPreviews.push(String(text));
    container.textContent = 'PREVIEW:' + String(text);
}
function appendDuplicateCandidate(container, candidate) {
    appendedCandidates.push({ pane: container.getAttribute('data-candidate-pane'), id: candidate.id });
    container.textContent = 'CAND:' + candidate.id;
}
"""


def _queue_harness() -> str:
    """阶段 1 逐题队列的渲染切片 + 依赖替身（双栏/懒渲染口径不变）。"""
    source = _import_source().replace("\r\n", "\n")

    def func_sig(name):
        sig = "        function " + name + "("
        assert source.count(sig) == 1, f"锚点不唯一: {name}"
        return sig

    def extract_func(name):
        sig = func_sig(name)
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

    def extract_range(a, b):
        i0 = source.index(a)
        i1 = source.index(b, i0)
        return source[i0:i1]

    helper_block = extract_range(func_sig("duplicateReasonLabel"),
                                 "        function resetParsedDuplicateCandidateRendering()")
    support_block = (
        extract_func("parsedDuplicateReviewImageStrip")
        + extract_func("parsedDuplicateReviewCurrentSource")
        + extract_func("buildParsedDuplicateReviewCurrentColumn")
        + extract_func("parsedDuplicateReviewBestCandidate")
        + extract_func("collectImportSourceFields")
        + extract_range(
            "        // lg 断点 = 1024px；窄屏把 data-compare 切成 stack",
            "        let parsedDuplicateReviewResizeBound = false;",
        )
    )
    stage1_block = extract_range(
        "        // 查重审查弹窗·阶段 1",
        "        function applySaveTimeDuplicateConflict(",
    )
    return (_BASE_SHIM
            + "let renderedPreviews = [];\nlet appendedCandidates = [];\n"
            + _WINDOW_SHIM
            + helper_block + support_block + stage1_block
            + _RENDER_STUBS
            + _DOC_STUB)


_ENTRIES = [
    {
        "label": "拆解结果第 1 题",
        "index": 0,
        "result": {
            "candidates": [
                {"id": 12, "seq_num": 12, "score": 0.99, "content": "aaa"},
                {"id": 13, "seq_num": 13, "score": 0.97, "content": "bbb"},
                {"id": 14, "seq_num": 14, "score": 0.95, "content": "ccc"}
            ],
            "batch_matches": []
        }
    },
    {
        "label": "拆解结果第 5 题",
        "index": 5,
        "result": {
            "candidates": [{"id": 34, "seq_num": 34, "score": 0.9, "content": "ddd"}],
            "batch_matches": ["parsed-5-2"]
        }
    },
    {
        "label": "拆解结果第 7 题",
        "index": 7,
        "result": {"candidates": [], "batch_matches": []}
    }
]


def _entries_literal() -> str:
    return _repr_js(_ENTRIES)


def test_queue_renders_one_conflict_at_a_time_in_two_columns():
    """阶段 1：每屏只渲染当前冲突题；区块内部仍是双栏（左=已有题候选，右=待导入题）。"""
    script = (
        _queue_harness()
        + r"""
const entries = __ENTRIES__;
makeCard(0, '卡片里的当前题干');
makeCard(5, '第二题题干');
parsedQuestionsData[0] = { content: 'parsedQuestionsData 里的旧题干', image_paths: ['/uploads/a.png'] };
parsedQuestionsData[5] = { content: '第二题旧题干', image_paths: [] };
openParsedDuplicateReviewModal(entries, 'batch');
const list = document.getElementById('parsedDuplicateReviewList');
const sections = list.children.filter(node => node.tagName === 'SECTION');
if (sections.length !== 1) {
    throw new Error('队列形态每屏只允许一道题区块，实际 ' + sections.length);
}
const grid = sections[0].querySelector('[data-compare-root]');
if (!grid) throw new Error('缺少双栏根节点');
if (grid.getAttribute('data-compare') !== 'two') throw new Error('宽屏应为双栏');
const left = grid.querySelector('[data-compare-left]');
const right = grid.querySelector('[data-compare-right]');
if (!left || !right) throw new Error('缺少左右栏标记');
if (String(left.textContent).indexOf('已有题（题库）') < 0) throw new Error('左栏应标为已有题');
if (String(right.textContent).indexOf('待导入题') < 0) throw new Error('右栏应标为待导入题');
// 右栏取卡片正文框当前值，而不是 parsedQuestionsData 的旧值
if (renderedPreviews[0] !== '卡片里的当前题干') {
    throw new Error('右栏数据源未取卡片正文: ' + renderedPreviews[0]);
}
if (created.filter(el => el.tagName === 'IMG').length < 1) throw new Error('右栏应展示该题配图');
""".replace("__ENTRIES__", _entries_literal())
    )
    _run_node(script)


def test_multiple_candidates_switch_via_chooser_and_lazy_panes():
    """多候选：对照候选下拉切换整屏；单候选不加下拉；切过去才渲染对应候选。"""
    script = (
        _queue_harness()
        + r"""
const entries = __ENTRIES__;
makeCard(0, '卡片里的当前题干');
parsedQuestionsData[0] = { content: '旧题干', image_paths: [] };
openParsedDuplicateReviewModal(entries, 'batch');
const list = document.getElementById('parsedDuplicateReviewList');
const selects = created.filter(el => el.tagName === 'SELECT');
if (selects.length !== 1) throw new Error('多候选区块应有且只有一个对照下拉，实际 ' + selects.length);
let panes = list.querySelectorAll('[data-candidate-pane]');
if (panes.length !== 1) throw new Error('每屏只渲染一张候选 pane，实际 ' + panes.length);
if (appendedCandidates.length !== 1 || appendedCandidates[0].id !== 12) {
    throw new Error('默认渲染第一张候选: ' + JSON.stringify(appendedCandidates));
}
// 切到第三张候选：整屏重渲染，pane 换成 14
const changeHandlers = selects[0].listeners.change || [];
selects[0].value = '2';
changeHandlers.forEach(fn => fn({ target: selects[0] }));
panes = document.getElementById('parsedDuplicateReviewList').querySelectorAll('[data-candidate-pane]');
if (appendedCandidates.length !== 2 || appendedCandidates[1].id !== 14) {
    throw new Error('切换候选后应渲染第 3 张: ' + JSON.stringify(appendedCandidates));
}
// 阶段 1 的对照下拉是「一次性视图」：选择器随整屏重建，不存在旧 pane 被重复渲染的路径
""".replace("__ENTRIES__", _entries_literal())
    )
    _run_node(script)


def test_narrow_screen_degrades_to_stacked():
    """窄屏（<1024px）：data-compare 降级为 stack。"""
    script = (
        _queue_harness()
        + r"""
const entries = __ENTRIES__;
WIDE = false;
makeCard(0, '卡片里的当前题干');
parsedQuestionsData[0] = { content: '旧题干', image_paths: [] };
openParsedDuplicateReviewModal(entries, 'batch');
const list = document.getElementById('parsedDuplicateReviewList');
const grid = list.children.filter(node => node.tagName === 'SECTION')[0]
    .querySelector('[data-compare-root]');
const value = grid.getAttribute('data-compare');
if (value !== 'stack') throw new Error('窄屏应降级为 stack，实际 ' + value);
""".replace("__ENTRIES__", _entries_literal())
    )
    _run_node(script)


# ---------------------------------------------------------------------------
# 真实 JS 执行：候选懒渲染观察器
# ---------------------------------------------------------------------------

_OBSERVER_SHIM = r"""
let parsedDuplicateCandidateObserver = null;
let parsedDuplicateCandidateRenderJobs = new WeakMap();
let parsedDuplicateCandidateFallbackQueue = [];
let parsedDuplicateCandidateFallbackFrame = null;
let parsedDuplicateCandidateRenderGeneration = 0;
const parsedDuplicateCandidateObservers = new Map();

const observers = [];
const window = {
    addEventListener() {},
    MathBankModal: { open() {}, close() {} },
    // 兜底队列靠 rAF 驱动，这个用例里让它同步跑完即可（真实浏览器下一帧执行）
    requestAnimationFrame(fn) { fn(); return 0; },
    matchMedia(query) { return { matches: true }; },
    innerHeight: 800,
    MathBankSafe: { safeImageUrl: p => p, sanitizePlainText: s => String(s || '') },
    renderQuestionPreviewContent(container, text) {
        container.textContent = 'PREVIEW:' + String(text);
    },
    IntersectionObserver: class {
        constructor(callback, options) {
            this.callback = callback;
            this.options = options || {};
            this.targets = [];
            observers.push(this);
        }
        observe(target) { this.targets.push(target); }
        unobserve(target) { this.targets = this.targets.filter(item => item !== target); }
        disconnect() { this.disconnected = true; }
    }
};
""" + MODAL_TRANSITION_STUB_JS


def _observer_harness() -> str:
    """阶段 1：候选 pane 直接挂左栏（不再有 data-candidate-scroll 容器），
    懒渲染调度器本体口径不变——观察器仍只认 data-candidate-scroll 祖先。"""
    source = _import_source().replace("\r\n", "\n")

    def slice_between(a, b):
        try:
            i0 = source.index(a)
        except ValueError as exc:
            raise AssertionError(f"起始锚点失配: {a[:60]!r}") from exc
        if b is None:
            return source[i0:]
        try:
            i1 = source.index(b, i0)
        except ValueError as exc:
            raise AssertionError(f"结束锚点失配: {b[:60]!r}（起点 {a[:40]!r}）") from exc
        return source[i0:i1]

    def func_block(name, stop=None):
        """按花括号配平切出整个函数；stop 给定时截到该签名前（含中间空行）。"""
        sig = "        function " + name + "("
        start = source.index(sig)
        depth = 0
        k = start
        while True:
            line_end = source.index(chr(10), k)
            line = source[k:line_end]
            depth += line.count("{") - line.count("}")
            if depth == 0 and k > start:
                end = line_end + 1
                break
            k = line_end + 1
        if stop is not None:
            nxt = source.index(stop, end)
            return source[start:nxt]
        return source[start:end] + chr(10)

    lazy_block = slice_between(
        "        function resetParsedDuplicateCandidateRendering()",
        "        function appendDuplicateCandidate(",
    )
    queue_shim = ""
    support = (
        slice_between(
            "        function duplicateReasonLabel(reason) {",
            "        function resetParsedDuplicateCandidateRendering()",
        )
        + lazy_block
        + func_block("parsedDuplicateReviewImageStrip")
        + func_block("parsedDuplicateReviewCurrentSource")
        + func_block("buildParsedDuplicateReviewCurrentColumn")
        + func_block("parsedDuplicateReviewBestCandidate")
        + func_block("collectImportSourceFields")
        + slice_between(
            "        // lg 断点 = 1024px；窄屏把 data-compare 切成 stack",
            "        let parsedDuplicateReviewResizeBound = false;",
        )
    )
    stage1 = slice_between(
        "        // 查重审查弹窗·阶段 1",
        "        function applySaveTimeDuplicateConflict(",
    )
    stubs = r"""
let toasts = [];
function showToast() {}
function closeParsedDuplicateReviewModal() {}
function openMergeSourceModal() {}
function bindParsedDuplicateReviewResize() {}
function duplicateBatchMatchCount(result) {
    return Array.isArray(result && result.batch_matches) ? result.batch_matches.length : 0;
}
// 与真实 appendDuplicateCandidate 同构：首屏 pane（container === FirstPaneEl）传 forceSync，
// 无条件同步渲染；其余容器不传，走视口/观察器/兜底判定。
function appendDuplicateCandidate(container, candidate) {
    const rendered = document.createElement('div');
    rendered.textContent = '进入可见区域后自动渲染题干…';
    scheduleParsedDuplicateCandidateRender(
        rendered,
        String(candidate.content || ''),
        container === parsedDuplicateQueueFirstPaneEl
    );
    container.appendChild(rendered);
}
"""
    return (_BASE_SHIM.replace("let WIDE = true;", "let WIDE = true;")
            + queue_shim
            + _OBSERVER_SHIM
            + support
            + stage1
            + stubs
            + _DOC_STUB)


def test_candidate_lazy_render_follows_the_current_conflict_pane():
    """阶段 1 队列 + root 固定为弹窗列表容器后的懒渲染口径：
    - 首屏只调度当前冲突题的第一张候选；
    - 已在视口内的候选立即同步渲染（左栏题干当场可见，不再停在占位文字）；
    - 视口外候选以 #parsedDuplicateReviewList 为 root 挂观察器，滚到才渲染；
    - reset 后观察器逐个断开、注册表清空。"""
    script = (
        _observer_harness()
        + r"""
makeCard(0, '卡片里的当前题干');
parsedQuestionsData[0] = { content: '旧题干', image_paths: [] };
openParsedDuplicateReviewModal(__ENTRIES__, 'batch');
const list = document.getElementById('parsedDuplicateReviewList');

// —— 首屏：视口内候选必须已被同步渲染成 PREVIEW，而不是停在占位文字 ——
const firstPaneHost = list.querySelectorAll('[data-candidate-pane]')[0];
if (!firstPaneHost) throw new Error('首屏未渲染候选 pane');
const renderedNodes = (firstPaneHost.children || []).filter(
    node => String(node.textContent).indexOf('PREVIEW:') === 0
);
if (renderedNodes.length < 1) {
    throw new Error('视口内候选应同步渲染出题干，实际 pane 文本: '
        + JSON.stringify(String(firstPaneHost.textContent).slice(0, 40)));
}
if (String(firstPaneHost.textContent).indexOf('进入可见区域后自动渲染') >= 0) {
    throw new Error('视口内候选仍停在占位文字，说明同步渲染没生效');
}

// —— 视口外候选：不立即渲染，改由以弹窗列表为 root 的观察器接管 ——
const offscreen = document.createElement('div');
offscreen.__rect = { top: 5000, bottom: 5020, left: 0, right: 100 };
scheduleParsedDuplicateCandidateRender(offscreen, '视口外题干');
if (String(offscreen.textContent).indexOf('PREVIEW:') === 0) {
    throw new Error('视口外候选不应立即渲染');
}
const observer = parsedDuplicateCandidateObservers.get(list);
if (!observer) throw new Error('未以弹窗列表容器为 root 建立观察器');
if (observer.options.root !== list) throw new Error('观察器 root 应为弹窗列表容器');
if (observer.options.rootMargin !== '120px 0px') throw new Error('rootMargin 不应被改动');
if (observer.targets.indexOf(offscreen) < 0) throw new Error('视口外候选未被观察');

// —— 观察器回调命中后补渲染 ——
observer.callback([{ isIntersecting: true, target: offscreen }], observer);
if (String(offscreen.textContent).indexOf('PREVIEW:') !== 0) {
    throw new Error('观察器命中后应渲染题干，实际: ' + offscreen.textContent);
}

// —— reset 断开并清空注册表 ——
resetParsedDuplicateCandidateRendering();
if (!observer.disconnected) throw new Error('重置后观察器应断开');
if (parsedDuplicateCandidateObservers.size !== 0) throw new Error('重置后注册表应清空');
""".replace("__ENTRIES__", _entries_literal())
    )
    _run_node(script)


def test_in_viewport_candidate_renders_without_observer():
    """视口内候选走同步渲染分支，绝不进观察器、也不进 rAF 兜底。"""
    script = (
        _observer_harness()
        + r"""
const before = observers.length;
const container = document.createElement('div');
container.__rect = { top: 40, bottom: 80, left: 0, right: 100 };  // 视口内
scheduleParsedDuplicateCandidateRender(container, '题干甲');
if (String(container.textContent) !== 'PREVIEW:题干甲') {
    throw new Error('视口内候选未同步渲染，实际: ' + container.textContent);
}
if (observers.length !== before) {
    throw new Error('视口内候选不应触发新建观察器');
}
if (container.getAttribute('aria-busy') !== 'false') {
    throw new Error('同步渲染后应清掉 aria-busy');
}
""".replace("__ENTRIES__", _entries_literal())
    )
    _run_node(script)


def test_first_pane_candidate_renders_even_when_offscreen():
    """反向钉住 (b) 修复本体：首屏 pane（container === parsedDuplicateQueueFirstPaneEl）
    即便被放到视口外，也必须无条件同步渲染、且不新建观察器。
    摘掉 `if (forceSync)` 分支后此测试必红——这正是 IntersectionObserver
    在某些环境不触发时「左栏永远停在占位文字」的根因防线。"""
    script = (
        _observer_harness()
        + r"""
const before = observers.length;
// 手工模拟 renderParsedDuplicateQueueEntry 的首屏认领协议
const firstPane = document.createElement('div');
firstPane.__rect = { top: 5000, bottom: 5020, left: 0, right: 100 };  // 故意放视口外
parsedDuplicateQueueFirstPaneEl = firstPane;
const rendered = document.createElement('div');
rendered.textContent = '进入可见区域后自动渲染题干…';
rendered.__rect = { top: 5000, bottom: 5020, left: 0, right: 100 };   // 也放视口外
firstPane.appendChild(rendered);
scheduleParsedDuplicateCandidateRender(
    rendered, '首屏题干', rendered.parentElement === parsedDuplicateQueueFirstPaneEl
);
if (String(rendered.textContent) !== 'PREVIEW:首屏题干') {
    throw new Error('首屏候选即使在视口外也应同步渲染，实际: ' + rendered.textContent);
}
if (observers.length !== before) {
    throw new Error('首屏 forceSync 路径不应新建观察器');
}
""".replace("__ENTRIES__", _entries_literal())
    )
    _run_node(script)


def test_open_modal_renders_first_candidate_via_force_sync_chain():
    """端到端：openParsedDuplicateReviewModal → renderParsedDuplicateQueueEntry 造标记
    → buildComparisonGrid 认领 pane → appendDuplicateCandidate 传 forceSync →
    首屏候选题干立即 PREVIEW，且此时尚未建立任何懒渲染观察器（证明没走 IO）。"""
    script = (
        _observer_harness()
        + r"""
makeCard(0, '卡片里的当前题干');
parsedQuestionsData[0] = { content: '旧题干', image_paths: [] };
openParsedDuplicateReviewModal(__ENTRIES__, 'batch');
const list = document.getElementById('parsedDuplicateReviewList');
const firstPaneHost = list.querySelectorAll('[data-candidate-pane]')[0];
if (!firstPaneHost) throw new Error('首屏未渲染候选 pane');
const txt = String(firstPaneHost.textContent);
if (txt.indexOf('PREVIEW:') < 0) {
    throw new Error('首屏候选未同步渲染出题干: ' + txt.slice(0, 40));
}
if (txt.indexOf('进入可见区域后自动渲染') >= 0) {
    throw new Error('首屏候选仍停在占位文字: ' + txt.slice(0, 40));
}
// 关键：观察器在渲染阶段就预建（prepare），但首屏 pane 从未被 observe——
// 证明它走的是 forceSync 同步路径，而不是 IntersectionObserver。
const listObserver = parsedDuplicateCandidateObservers.get(list);
if (listObserver && listObserver.targets.length !== 0) {
    throw new Error('首屏候选不应被挂进观察器 targets（应走 forceSync），实际 targets='
        + listObserver.targets.length);
}
""".replace("__ENTRIES__", _entries_literal())
    )
    _run_node(script)
