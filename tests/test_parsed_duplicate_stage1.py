# -*- coding: utf-8 -*-
"""查重审查弹窗·阶段 1（逐题队列 + 四级徽标 + 相似题关联）的回归测试。

钉死七件事（用户验收口径）：
1. 只有冲突题进队列；逐题处理时弹窗不关，内容整块换掉；
2. 进度「第 N / M 道冲突题」随队列推进；
3. 四级徽标映射正确（完全相同/同题异源/答案冲突/变式相关），
   同源判据 = 年份+学校+卷名全等，答案冲突 = 题干相同理由 + ANSWER_DIFF；
4. 理由行逐字段状态（✅一致/⚠️相似/❌不同/—缺失），普通字号深色文字，
   禁止半透明浅灰（text-slate-400 / opacity）；
5. 按钮可见性跟徽标走，系统建议按钮高亮（dup-suggested）；
6. 「标记为相似题」→ 独立入库 + 关闭后 POST /api/questions/{id}/associate（form target_id）；
7. 【跳过全部】= 整条队列一起丢弃、以 skip 收尾（**绝不发 POST /api/questions**）；
   【去重】= merged 语义中止本题入库。
   ⚠️ 旧语义是「默认操作（独立保存）套到剩余全部」并 resolve('independent')，于是按钮名叫
   跳过、实际却把题写进题库——文案与网络后果相反，实测会在生产库造出垃圾数据（已改）。

聚合动作契约不变：'review' / 'skip' / 'independent' / 'merged'，三个调用方零改动。
"""

from pathlib import Path
import json
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


def _repr_js(value) -> str:
    return repr(value).replace("'", '"').replace("True", "true").replace("False", "false")


def _run_node(script: str) -> None:
    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    # 命令行 8191 字符上限：队列代码块远超内联预算，一律落临时文件执行。
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
# DOM 替身：getElementById 按 id 稳定返回同一元素，querySelector 支持属性选择器
# ---------------------------------------------------------------------------

_SHIM = r"""
let parsedDuplicateReviewResolver = null;
const parsedDuplicateMergedIndices = new Set();
let parsedDuplicateQueueEntries = [];
let parsedDuplicateQueueMode = 'batch';
let parsedDuplicateQueueIndex = 0;
let parsedDuplicateQueueCloseMergeOnSuccess = true;
const parsedDuplicateIndependentIndices = new Set();
const parsedDuplicateSkippedIndices = new Set();
const parsedDuplicateAssociationRequests = [];
let parsedQuestionsData = [];
let independentOverrideIndices = new Set();
let indicesToSave = [2, 5];

function makeClassList(node) {
    const set = new Set();
    return {
        add(...cs) { cs.forEach(c => set.add(c)); },
        remove(...cs) { cs.forEach(c => set.delete(c)); },
        contains(c) { return set.has(c); },
        toggle(c, force) {
            const on = force === undefined ? !set.has(c) : Boolean(force);
            if (on) set.add(c); else set.delete(c);
            return on;
        },
        _set: set
    };
}
class El {
    constructor(tag) {
        this.tagName = String(tag).toUpperCase();
        this.children = [];
        this.attrs = {};
        this.listeners = {};
        // 真实 DOM 里 className 与 classList 是同一份视图；替身必须同步，
        // 否则被测代码只写 className 时测试永远看不到类（假阴性）。
        const classSet = new Set();
        this.classList = {
            add(...cs) { cs.forEach(c => classSet.add(c)); },
            remove(...cs) { cs.forEach(c => classSet.delete(c)); },
            contains(c) { return classSet.has(c); },
            toggle(c, force) {
                const on = force === undefined ? !classSet.has(c) : Boolean(force);
                if (on) classSet.add(c); else classSet.delete(c);
                return on;
            },
            _set: classSet
        };
        Object.defineProperty(this, 'className', {
            get() { return Array.from(classSet).join(' '); },
            set(value) {
                classSet.clear();
                String(value || '').split(/\s+/).filter(Boolean).forEach(c => classSet.add(c));
            }
        });
        this.value = '';
        this._text = '';
        this.onclick = null;
    }
    appendChild(child) { this.children.push(child); child.parentElement = this; return child; }
    append(...cs) { cs.forEach(c => this.appendChild(c)); }
    removeChild(child) { this.children = this.children.filter(c => c !== child); }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
    fire(type) { (this.listeners[type] || []).forEach(fn => fn({ target: this })); }
    click() {
        (this.listeners.click || []).forEach(fn => fn({ target: this }));
        if (typeof this.onclick === 'function') this.onclick();
    }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
    hasAttribute(k) { return k in this.attrs; }
    removeAttribute(k) { delete this.attrs[k]; }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    querySelectorAll(selector) {
        const found = [];
        const walk = node => (node.children || []).forEach(child => {
            if (child && typeof child.getAttribute === 'function') {
                if (selector.indexOf('[') === 0) {
                    const key = selector.slice(1, -1).split('=')[0];
                    if (child.getAttribute(key) !== null) found.push(child);
                }
            }
            walk(child);
        });
        walk(this);
        return found;
    }
}
"""

_WINDOW_DOC = r"""
let toasts = [];
let mergeCalls = [];
let mergeOnSuccess = null;
let associateCalls = [];
let renderedPreviews = [];
const __byId = {};
function el(id) {
    if (!__byId[id]) __byId[id] = new El('div');
    return __byId[id];
}
function showToast(message, kind) { toasts.push({ message: String(message), kind: kind }); }
function openMergeSourceModal(id, seq, onSuccess, preset) {
    mergeCalls.push({ id: id, seq: seq, preset: preset });
    mergeOnSuccess = onSuccess;
}
function prepareParsedDuplicateCandidateRendering() {}
function resetParsedDuplicateCandidateRendering() {}
function bindParsedDuplicateReviewResize() {}
function duplicateBatchMatchCount(result) {
    return Array.isArray(result && result.batch_matches) ? result.batch_matches.length : 0;
}
function appendDuplicateCandidate(container, candidate) {
    container.textContent = 'CAND:' + candidate.id;
}
// 卡片替身：正文框/解答框/题型/来源框
function makeCard(index, fields) {
    const box = name => {
        const node = new El('textarea');
        node.value = fields[name] || '';
        return node;
    };
    const card = new El('div');
    const boxes = {
        '.card-content-textarea': box('content'),
        '.card-answer-textarea': box('answer'),
        '.card-qtype': box('qtype'),
        '.card-source': box('source')
    };
    card.querySelector = selector => boxes[selector] || null;
    __byId['parsed-card-' + index] = card;
}
const document = {
    createElement(tag) { return new El(tag); },
    createTextNode(text) { return { textContent: String(text) }; },
    getElementById(id) { return el(id); },
    body: new El('body')
};
const window = {
    MathBankModal: { open() {}, close() {} },
    matchMedia() { return { matches: true }; },
    addEventListener() {},
    MathBankSafe: {
        safeImageUrl: p => p,
        sanitizePlainText: s => String(s || '')
    },
    renderQuestionPreviewContent(container, text) {
        renderedPreviews.push(String(text));
        container.textContent = 'PREVIEW:' + String(text);
    }
};
const localStorage = { getItem: () => 'test-token' };
function fetch(url, options) {
    associateCalls.push({ url: url, options: options });
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ status: 'success' }) });
}
""" + MODAL_TRANSITION_STUB_JS


def _queue_harness() -> str:
    """真实代码切片 + 依赖替身。垫片必须整体在前：makeCard 引用 El（TDZ）。"""
    source = _import_source().replace("\r\n", "\n")

    def func_sig(name: str) -> str:
        sig = "        function " + name + "("
        assert source.count(sig) == 1, f"锚点不唯一: {name}"
        return sig

    def extract_func(name: str) -> str:
        """按花括号配平切出**整个函数**。行级扫描，字符串里的花括号不影响计数
        （本项目函数体里 {} 只出现在成对模板串/对象字面量中）。"""
        sig = func_sig(name)
        start = source.index(sig)
        depth = 0
        k = start
        while True:
            line_end = source.index("\n", k)
            line = source[k:line_end]
            depth += line.count("{") - line.count("}")
            if depth == 0 and k > start:
                return source[start:line_end + 1] + "\n"
            k = line_end + 1

    def extract_range(start_sig: str, stop_sig: str) -> str:
        a = source.index(start_sig)
        b = source.index(stop_sig, a)
        return source[a:b]

    stage1_block = extract_range(
        "        // 查重审查弹窗·阶段 1",
        "        function applySaveTimeDuplicateConflict(",
    )
    helper_block = extract_range(
        func_sig("duplicateReasonLabel"),
        "        function resetParsedDuplicateCandidateRendering()",
    )
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
    return _SHIM + _WINDOW_DOC + helper_block + support_block + stage1_block


# ---------------------------------------------------------------------------
# 夹具数据
# ---------------------------------------------------------------------------

_CARD_SETUP = chr(10).join([
    "['importSrcYear', 'importSrcRegion', 'importSrcExamType'].forEach(id => el(id).value = "
    "({ importSrcYear: '2026', importSrcRegion: '哈三中', importSrcExamType: '三模' })[id]);",
    "makeCard(2, { content: CARD2_STEM, answer: '【答案】$(-2,2)$', qtype: 'fill_in_blank',"
    " source: '2026年哈三中三模数学试卷' });",
    "makeCard(5, { content: CARD5_STEM, answer: '【答案】$(-3,3)$', qtype: 'fill_in_blank',"
    " source: '2026年哈三中三模数学试卷' });",
    "parsedQuestionsData[2] = { content: 'x', answer_markdown: 'y', image_paths: [],"
    " source: '2026年哈三中三模数学试卷' };",
    "parsedQuestionsData[5] = { content: 'x', answer_markdown: 'y', image_paths: [],"
    " source: '2025年省实验期末数学试卷' };",
]) + chr(10)

ENTRIES = [
    {
        "label": "拆解结果第 2 题",
        "index": 2,
        "result": {
            "level": "exact",
            "snapshot_hash": "h2",
            "candidates": [
                {
                    "id": 12, "seq_num": 12, "level": "exact", "score": 0.99,
                    "content": "已知集合 $A=\\{x\\mid x^2<4\\}$，求 $A$。",
                    "source": "2026年哈三中三模数学试卷",
                    "reasons": ["EXACT_TEXT"],
                }
            ],
            "batch_matches": [],
            "needs_visual_review": False,
        },
    },
    {
        "label": "拆解结果第 5 题",
        "index": 5,
        "result": {
            "level": "possible_variant",
            "snapshot_hash": "h5",
            "candidates": [
                {
                    "id": 34, "seq_num": 34, "level": "possible_variant", "score": 0.72,
                    "content": "已知集合 $A=\\{x\\mid x^2<9\\}$，求 $A$。",
                    "source": "2025年省实验期末数学试卷",
                    "reasons": ["NUMBER_CHANGED"],
                }
            ],
            "batch_matches": [],
            "needs_visual_review": False,
        },
    },
]

CONFLICT_ENTRY = [{
    "label": "拆解结果第 3 题",
    "index": 3,
    "result": {
        "level": "probable",
        "candidates": [{
            "id": 21, "seq_num": 21, "level": "probable", "score": 0.95,
            "content": "计算 $1+1$。",
            "source": "2026年哈三中三模数学试卷",
            "reasons": ["CRITICAL_MATH_MATCH", "ANSWER_DIFF"],
        }],
        "batch_matches": [],
    },
}]

CROSS_SOURCE_ENTRY = [{
    "label": "拆解结果第 4 题",
    "index": 4,
    "result": {
        "level": "exact",
        "candidates": [{
            "id": 55, "seq_num": 55, "level": "exact", "score": 1.0,
            "content": "求 $\\sin 30^\\circ$。",
            "source": "2024年省实验期中数学试卷",
            "reasons": ["EXACT_TEXT"],
        }],
        "batch_matches": [],
    },
}]




def _script(setup: str, body: str) -> str:
    # 垫片（含 El/makeCard）必须整体在前，随后才是夹具与断言。
    return _queue_harness() + setup + body


def _entries_script(entries_literal: str, setup: str, body: str) -> str:
    """ENTRIES 必须先于 makeCard/断言求值：_CARD_SETUP 引用 CARD2_STEM/CARD5_STEM，
    卡片正文与候选题干必须逐字节一致，否则「题干」状态片会判错。"""
    consts = (
        "const ENTRIES = " + entries_literal + ";\n"
        "const CARD2_STEM = ENTRIES[0].result.candidates[0].content;\n"
        "const CARD5_STEM = ENTRIES[1].result.candidates[0].content;\n"
    )
    return _queue_harness() + consts + setup + body


# ---------------------------------------------------------------------------
# 真实 JS 执行
# ---------------------------------------------------------------------------


def test_queue_processes_conflict_items_without_closing_modal():
    """逐题队列：打开只渲染第一道冲突题；独立保存后弹窗不关、内容换成第二道；
    最后一道处理完以 'independent' 收尾；进度文案从 第 1 / 2 走到 第 2 / 2。"""
    body = r"""
const entries = __E__;
let resolved = null;
const promise = openParsedDuplicateReviewModal(entries, 'batch');
promise.then(action => { resolved = action; });
const list = document.getElementById('parsedDuplicateReviewList');
const sections = list.children.filter(node => node.tagName === 'SECTION');
if (sections.length !== 1) {
    throw new Error('队列形态下每屏只允许一道题区块，实际 ' + sections.length);
}
if (String(list.textContent).indexOf('第 2 题') < 0) {
    throw new Error('首屏应是拆解结果第 2 题');
}
const progress = document.getElementById('duplicateReviewProgress');
if (progress.textContent !== '第 1 / 2 道冲突题') {
    throw new Error('初始进度错误: ' + progress.textContent);
}
if (progress.classList.contains('hidden')) throw new Error('批量模式进度条必须可见');
const independentBtn = document.getElementById('duplicateReviewIndependentBtn');
if (independentBtn.classList.contains('hidden')) throw new Error('独立保存按钮应可见');
independentBtn.click();
if (resolved !== null) throw new Error('第一道处理完不得提前关闭弹窗');
if (progress.textContent !== '第 2 / 2 道冲突题') {
    throw new Error('处理后进度未推进: ' + progress.textContent);
}
if (String(list.textContent).indexOf('第 5 题') < 0) {
    throw new Error('第二屏应换成拆解结果第 5 题');
}
document.getElementById('duplicateReviewIndependentBtn').click();
Promise.resolve().then(() => {
    if (resolved !== 'independent') {
        throw new Error('队列走完应以 independent 收尾，实际 ' + resolved);
    }
});
""".replace("__E__", _repr_js(ENTRIES))
    _run_node(_entries_script(_repr_js(ENTRIES), _CARD_SETUP, body))


def test_badge_levels_and_reason_line_states():
    """四级徽标映射 + 理由行逐字段状态：exact+同源 → 完全相同；variant → 变式相关；
    理由行必须是普通字号深色文字（font-semibold + text-slate-800），
    且带 data-dup-field/data-dup-state 的 ✅/⚠️/❌/— 状态片。"""
    body = r"""
const entries = __E__;
openParsedDuplicateReviewModal(entries, 'batch');
const conclusion = document.getElementById('duplicateReviewConclusion');
if (conclusion.getAttribute('data-dup-badge') !== 'exact') {
    throw new Error('第一题应为完全相同徽标，实际 ' + conclusion.getAttribute('data-dup-badge'));
}
if (String(conclusion.textContent).indexOf('建议直接去重') < 0) {
    throw new Error('徽标旁必须给出建议动作：' + conclusion.textContent);
}
const list = document.getElementById('parsedDuplicateReviewList');
const reasonLine = list.querySelector('[data-dup-reason-line]');
if (!reasonLine) throw new Error('缺少逐字段理由行');
if (String(reasonLine.className).indexOf('text-slate-400') >= 0
    || String(reasonLine.className).indexOf('opacity') >= 0) {
    throw new Error('理由行禁止半透明浅灰: ' + reasonLine.className);
}
if (String(reasonLine.className).indexOf('font-semibold') < 0
    || String(reasonLine.className).indexOf('text-slate-800') < 0) {
    throw new Error('理由行必须普通字号深色文字: ' + reasonLine.className);
}
const states = {};
reasonLine.querySelectorAll('[data-dup-field]').forEach(chip => {
    states[chip.getAttribute('data-dup-field')] = chip.getAttribute('data-dup-state');
});
if (states['题干'] !== 'same') throw new Error('题干应判一致: ' + JSON.stringify(states));
if (states['来源'] !== 'same') throw new Error('同源应判一致: ' + JSON.stringify(states));
if (states['答案'] === undefined) throw new Error('理由行缺答案槽位: ' + JSON.stringify(states));
document.getElementById('duplicateReviewIndependentBtn').click();
if (conclusion.getAttribute('data-dup-badge') !== 'variant') {
    throw new Error('第二题应为变式相关徽标，实际 ' + conclusion.getAttribute('data-dup-badge'));
}
if (String(conclusion.textContent).indexOf('建议标记为相似题') < 0) {
    throw new Error('变式徽标建议动作错误: ' + conclusion.textContent);
}
const secondLine = document.getElementById('parsedDuplicateReviewList')
    .querySelector('[data-dup-reason-line]');
const secondStates = {};
secondLine.querySelectorAll('[data-dup-field]').forEach(chip => {
    secondStates[chip.getAttribute('data-dup-field')] = chip.getAttribute('data-dup-state');
});
if (secondStates['题干'] !== 'same') {
    throw new Error('同文题干应判 ✅一致: ' + JSON.stringify(secondStates));
}
if (secondStates['来源'] !== 'diff') {
    throw new Error('异源应判 ❌不同: ' + JSON.stringify(secondStates));
}
// 同源判据的另一半：卡片与候选整串完全相等时，duplicateSourcesSame 必须为 true
// （变异「return true」恒真 / 「raw 短路失效」都会在这里露出来）
const sigA = duplicateSourceSignatureFromText('2026年哈三中三模数学试卷');
const sigB = duplicateSourceSignatureFromText('2026年哈三中三模数学试卷');
const sigC = duplicateSourceSignatureFromText('2025年省实验期末数学试卷');
if (!duplicateSourcesSame(sigA, [sigB])) throw new Error('同文必须判同源');
if (duplicateSourcesSame(sigA, [sigC])) throw new Error('异文必须判异源');
""".replace("__E__", _repr_js(ENTRIES))
    _run_node(_entries_script(_repr_js(ENTRIES), _CARD_SETUP, body))


def test_button_availability_follows_badge_and_suggested_is_highlighted():
    """🔴完全相同 → 去重高亮可点、相似题隐藏；🔵变式 → 相似题高亮可点、去重隐藏。"""
    body = r"""
const entries = __E__;
openParsedDuplicateReviewModal(entries, 'batch');
const dedupe = document.getElementById('duplicateReviewDedupeBtn');
const associate = document.getElementById('duplicateReviewAssociateBtn');
const merge = document.getElementById('duplicateReviewMergeBtn');
if (dedupe.classList.contains('hidden')) throw new Error('完全相同应显示去重按钮');
if (!dedupe.classList.contains('dup-suggested')) throw new Error('去重是系统建议项，必须高亮');
if (merge.classList.contains('hidden')) throw new Error('追加来源入口必须常驻可见（异源 exact 的唯一出口）');
if (!!merge.classList.contains('dup-suggested')) throw new Error('完全相同的建议项只能是去重');
if (!associate.classList.contains('hidden')) throw new Error('完全相同不应出现相似题按钮');
document.getElementById('duplicateReviewIndependentBtn').click();
if (associate.classList.contains('hidden')) throw new Error('变式应显示相似题按钮');
if (!associate.classList.contains('dup-suggested')) throw new Error('相似题是系统建议项，必须高亮');
if (!dedupe.classList.contains('hidden')) throw new Error('变式不得出现去重按钮');
""".replace("__E__", _repr_js(ENTRIES))
    _run_node(_entries_script(_repr_js(ENTRIES), _CARD_SETUP, body))


def test_answer_conflict_badge_and_only_independent_action():
    """题干相同 + ANSWER_DIFF → 🟡答案冲突，警告人工核对，只留独立保存（阶段 1 不代做取舍）。"""
    setup = (
        # 卡片来源与候选故意不同：若 ANSWER_DIFF/同源判据失效，徽标会落到 probable
        # （异源兜底）而不是 conflict——变异必须能在这里露出来。
        "makeCard(3, { content: '计算 $1+1$。', answer: '【答案】3', qtype: 'fill_in_blank',"
        " source: '2025年省实验期末数学试卷' });\n"
        "parsedQuestionsData[3] = { content: 'x', answer_markdown: 'y', image_paths: [],"
        " source: '2025年省实验期末数学试卷' };\n"
    )
    body = r"""
const entries = __E__;
openParsedDuplicateReviewModal(entries, 'single');
const conclusion = document.getElementById('duplicateReviewConclusion');
if (conclusion.getAttribute('data-dup-badge') !== 'conflict') {
    throw new Error('答案冲突徽标判定失败: ' + conclusion.getAttribute('data-dup-badge'));
}
if (String(conclusion.textContent).indexOf('请人工核对') < 0) {
    throw new Error('答案冲突必须给出人工核对警告: ' + conclusion.textContent);
}
const dedupe = document.getElementById('duplicateReviewDedupeBtn');
const merge = document.getElementById('duplicateReviewMergeBtn');
const associate = document.getElementById('duplicateReviewAssociateBtn');
if (!dedupe.classList.contains('hidden')) throw new Error('答案冲突不得提供去重');
if (!merge.classList.contains('hidden')) throw new Error('答案冲突不得提供追加来源');
if (!associate.classList.contains('hidden')) throw new Error('答案冲突不得提供相似题');
const independent = document.getElementById('duplicateReviewIndependentBtn');
if (independent.classList.contains('hidden')) throw new Error('答案冲突必须保留独立保存');
if (!independent.classList.contains('dup-suggested')) throw new Error('答案冲突默认建议独立保存');
""".replace("__E__", _repr_js(CONFLICT_ENTRY))
    _run_node(_script(setup, body))


def test_cross_source_exact_promotes_to_same_question_different_source():
    """完全相同但来源不同（年份/学校/卷名任一不等）→ 🟠同题异源，建议追加来源。"""
    setup = (
        "makeCard(4, { content: '求 $\\sin 30^\\circ$。', answer: '', qtype: 'fill_in_blank',"
        " source: '2026年哈三中三模数学试卷' });\n"
        "parsedQuestionsData[4] = { content: 'x', answer_markdown: '', image_paths: [] };\n"
    )
    body = r"""
const entries = __E__;
openParsedDuplicateReviewModal(entries, 'batch');
const conclusion = document.getElementById('duplicateReviewConclusion');
if (conclusion.getAttribute('data-dup-badge') !== 'probable') {
    throw new Error('异源的 exact 应升格为同题异源: ' + conclusion.getAttribute('data-dup-badge'));
}
if (String(conclusion.textContent).indexOf('建议追加来源') < 0) {
    throw new Error('同题异源建议动作错误: ' + conclusion.textContent);
}
const merge = document.getElementById('duplicateReviewMergeBtn');
if (merge.classList.contains('hidden')) throw new Error('同题异源必须给出追加来源入口');
if (!merge.classList.contains('dup-suggested')) throw new Error('追加来源应是高亮建议项');
// 对照半边：同文 + 同源的 exact 必须维持 🔴完全相同（raw 全等判据不得只测异源侧）
const sameSourceEntry = [{
    label: '拆解结果第 6 题', index: 6,
    result: { level: 'exact', candidates: [{ id: 77, seq_num: 77, level: 'exact', score: 1,
        content: '求 $\\sin 30^\\circ$。', source: '2026年哈三中三模数学试卷', reasons: ['EXACT_TEXT'] }],
        batch_matches: [] }
}];
makeCard(6, { content: '求 $\\sin 30^\\circ$。', answer: '', qtype: 'fill_in_blank',
    source: '2026年哈三中三模数学试卷' });
parsedQuestionsData[6] = { content: 'x', answer_markdown: '', image_paths: [],
    source: '2026年哈三中三模数学试卷' };
openParsedDuplicateReviewModal(sameSourceEntry, 'batch');
if (conclusion.getAttribute('data-dup-badge') !== 'exact') {
    throw new Error('同文同源的 exact 必须保持完全相同徽标: ' + conclusion.getAttribute('data-dup-badge'));
}
""".replace("__E__", _repr_js(CROSS_SOURCE_ENTRY))
    _run_node(_script(setup, body))


def test_dedupe_and_merge_and_skip_all_aggregate_actions():
    """【去重】→ merged 并登记剔除下标；【追加来源】→ 并入弹窗预填当前题来源；
    【跳过全部】→ 以 skip 收尾并把整条队列打上 skipped（一道都不入库）。"""
    body = r"""
const entries = __E__;
let resolved = null;
const promise = openParsedDuplicateReviewModal(entries, 'batch');
promise.then(action => { resolved = action; });
document.getElementById('duplicateReviewDedupeBtn').click();
Promise.resolve().then(() => {
    if (resolved !== 'merged') {
        throw new Error('去重必须以 merged 结束审查，实际 ' + resolved);
    }
    if (!parsedDuplicateMergedIndices.has(2)) {
        throw new Error('去重必须登记待剔除的题目下标: ' + Array.from(parsedDuplicateMergedIndices));
    }
    // --- 第二轮：追加来源走并入弹窗并预填本题来源 ---
    resolved = null;
    independentOverrideIndices.clear();
    parsedDuplicateIndependentIndices.clear();
    const second = openParsedDuplicateReviewModal(entries, 'batch');
    second.then(action => { resolved = action; });
    document.getElementById('duplicateReviewMergeBtn').click();
    if (mergeCalls.length !== 1 || mergeCalls[0].id !== 12) {
        throw new Error('追加来源必须作用于本题候选: ' + JSON.stringify(mergeCalls));
    }
const wantPaperTitle = parsedQuestionsData[2] && parsedQuestionsData[2].source
        ? parsedQuestionsData[2].source : '2026';
    if (String(mergeCalls[0].preset.year) !== '2026') {
        throw new Error('并入弹窗必须预填年份: ' + JSON.stringify(mergeCalls[0].preset));
    }
    if (String(mergeCalls[0].preset.region) !== '哈三中') {
        throw new Error('并入弹窗必须预填学校/地区: ' + JSON.stringify(mergeCalls[0].preset));
    }
    if (!mergeCalls[0].preset.paper_title) {
        throw new Error('并入弹窗必须带上整卷出处: ' + JSON.stringify(mergeCalls[0].preset));
    }
    mergeOnSuccess();
    // 追加来源只登记本题为「已并入」并前进到下一道冲突题；整批的收尾动作由调用方结算。
    if (!parsedDuplicateMergedIndices.has(2)) {
        throw new Error('并入必须登记题目下标: ' + Array.from(parsedDuplicateMergedIndices));
    }
    if (parsedDuplicateQueueIndex !== 1) {
        throw new Error('并入后应自动前进到下一道冲突题，实际停在 ' + parsedDuplicateQueueIndex);
    }
    // --- 第三轮：跳过全部 = 整条队列一起丢弃，一道都不入库 ---
    resolved = null;
    independentOverrideIndices.clear();
    parsedDuplicateIndependentIndices.clear();
    const third = openParsedDuplicateReviewModal(entries, 'batch');
    third.then(action => { resolved = action; });
    document.getElementById('duplicateReviewSkipAllBtn').click();
    Promise.resolve().then(() => {
        if (resolved !== 'skip') {
            throw new Error('跳过全部必须以 skip 收尾（绝不能是 independent，那会让调用方继续 POST 入库），实际 ' + resolved);
        }
        if (parsedDuplicateIndependentIndices.size !== 0) {
            throw new Error('跳过全部不得把任何题登记为独立保存: ' + Array.from(parsedDuplicateIndependentIndices));
        }
        const stillSkipped = parsedDuplicateQueueEntries.filter(item => item && item.skipped).map(item => item.index);
        if (stillSkipped.length !== parsedDuplicateQueueEntries.length) {
            throw new Error('跳过全部必须给整条队列打上 skipped，供批量流程剔除: ' + JSON.stringify(stillSkipped));
        }
    });
});
""".replace("__E__", _repr_js(ENTRIES))
    _run_node(_entries_script(_repr_js(ENTRIES), _CARD_SETUP, body))


def test_associate_marks_similar_and_uses_existing_endpoint_after_import():
    """【标记为相似题】：题目照常独立入库（independent 决策），弹窗不关并推进；
    入库回填 saved_id 后兑现 POST /api/questions/{id}/associate（form target_id）。"""
    body = r"""
const entries = __E__;
let resolved = null;
const promise = openParsedDuplicateReviewModal(entries, 'batch');
promise.then(action => { resolved = action; });
// 第一题没有相似题按钮；推进到第二题（变式）。saved_id 必须在点击前就位
// （模拟批量入库回填主键的时序），否则兑现时读不到主键只会报「尚未入库」。
parsedQuestionsData[5].saved_id = 400;
document.getElementById('duplicateReviewIndependentBtn').click();
const associate = document.getElementById('duplicateReviewAssociateBtn');
if (associate.classList.contains('hidden')) throw new Error('变式题必须显示相似题按钮');
associate.click();
if (parsedDuplicateQueueIndex !== parsedDuplicateQueueEntries.length - 1) {
    throw new Error('最后一道决策后队列应停在末位');
}
if (!independentOverrideIndices.has(5)) {
    throw new Error('相似题必须登记独立保存决策: ' + Array.from(independentOverrideIndices));
}
if (associateCalls.length !== 0) {
    throw new Error('关联必须在入库拿到主键后才发起，弹窗内不得抢跑');
}
drainParsedDuplicateAssociationRequests();
if (associateCalls.length !== 1) {
    throw new Error('兑现后必须恰好发起一次关联请求: ' + JSON.stringify(associateCalls.map(c => c.url)));
}
const call = associateCalls[0];
if (call.url !== '/api/questions/400/associate') {
    throw new Error('关联接口路径错误: ' + call.url);
}
if (call.options.method !== 'POST') throw new Error('关联必须是 POST');
if (String(call.options.body).indexOf('target_id=34') < 0) {
    throw new Error('关联参数必须沿用 target_id 表单口径: ' + call.options.body);
}
if (String(call.options.headers['X-Local-Token']) !== 'test-token') {
    throw new Error('POST 必须携带本地 Token');
}
""".replace("__E__", _repr_js(ENTRIES))
    _run_node(_entries_script(_repr_js(ENTRIES), _CARD_SETUP, body))


def test_single_mode_last_decision_resolves_independent_for_caller():
    """单题入口：最后一题选独立保存后队列自动收尾，必须以 'independent' 交付调用方
    （否则 saveParsedQuestion 会把它当「未决定」而中止导入）。"""
    body = r"""
const entries = __E__;
let resolved = null;
const promise = openParsedDuplicateReviewModal([entries[0]], 'single');
promise.then(action => { resolved = action; });
const skipBtn = document.getElementById('duplicateReviewSkipBtn');
if (!skipBtn.classList.contains('hidden')) throw new Error('单题模式不得出现跳过按钮');
const skipAll = document.getElementById('duplicateReviewSkipAllBtn');
if (!skipAll.classList.contains('hidden')) throw new Error('单题模式不得出现跳过全部');
document.getElementById('duplicateReviewIndependentBtn').click();
Promise.resolve().then(() => {
    if (resolved !== 'independent') {
        throw new Error('单题独立保存后应自动以 independent 收尾，实际 ' + resolved);
    }
});
""".replace("__E__", _repr_js(ENTRIES))
    _run_node(_entries_script(_repr_js(ENTRIES), _CARD_SETUP, body))


def test_inspect_mode_is_read_only_and_hides_decision_buttons():
    """查重依据（inspect）：只读——所有决策按钮隐藏、进度隐藏、返回键文案为「关闭」。"""
    body = r"""
const entries = __E__;
openParsedDuplicateReviewModal([entries[0]], 'inspect');
['duplicateReviewDedupeBtn', 'duplicateReviewMergeBtn', 'duplicateReviewAssociateBtn',
 'duplicateReviewIndependentBtn', 'duplicateReviewSkipBtn', 'duplicateReviewSkipAllBtn']
    .forEach(id => {
        if (!document.getElementById(id).classList.contains('hidden')) {
            throw new Error('inspect 模式必须隐藏决策按钮: ' + id);
        }
    });
if (!document.getElementById('duplicateReviewProgress').classList.contains('hidden')) {
    throw new Error('inspect 模式不得显示队列进度');
}
if (document.getElementById('duplicateReviewReturnBtn').textContent !== '关闭') {
    throw new Error('inspect 返回键文案错误');
}
""".replace("__E__", _repr_js(ENTRIES))
    _run_node(_entries_script(_repr_js(ENTRIES), _CARD_SETUP, body))


def test_skip_all_excludes_every_conflict_but_keeps_clean_questions():
    """调用侧剔除：【跳过全部】后整批冲突题都不得进入 indicesToSave，干净题照常入库。

    弹窗只负责给队列条目打 skipped，真正「不写库」发生在批量保存流程按 entry.index
    过滤那一段。只测弹窗会漏掉这半条链路——上一版就是这里语义反了。
    """
    source = _import_source()
    start = source.index("const action = await openParsedDuplicateReviewModal(")
    end = source.index("// 本次审查里被「并入已有题」的疑似题不入库", start)
    block_src = source[start:end]

    assert "action === 'skip'" in block_src, "批量流程必须处理 skip 收尾"
    assert "queueSkipped" in block_src, "必须按队列条目的 skipped 标记剔除冲突题"
    # skip_all 走的是 'skip' 分支：suspectIndices 全量剔除；若所有题都是冲突题则直接中止导入
    skip_branch = block_src[block_src.index("if (action === 'skip') {"):]
    skip_branch = skip_branch[:skip_branch.index("} else if")]
    assert "indicesToSave = selectedIndices.filter(index => !suspectSet.has(index))" in skip_branch, (
        "旧的逐题 skip 语义（只剔被跳过的）必须仍兼容"
    )
    assert "return false" in skip_branch, "全部冲突时必须中止，不得继续 POST 入库"

    # 关键：跳过全部的收尾必须是 'skip' 而不是 'independent'
    handler_start = source.index("case 'skip_all':")
    handler_end = source.index("case 'abort':", handler_start)
    # 只查代码行：本 case 的注释里引用了旧写法 resolve('independent') 来说明为什么改，
    # 若把注释也算进来，这条守卫会被自己的解释文字判成违规（实测踩过）。
    handler_code = "\n".join(
        line for line in source[handler_start:handler_end].splitlines()
        if not line.strip().startswith("//")
    )
    assert "closeParsedDuplicateReviewModal('skip')" in handler_code, (
        "跳过全部必须以 skip 收尾；resolve('independent') 会让调用方继续发 POST /api/questions"
    )
    assert "'independent'" not in handler_code, "跳过全部不得再登记为独立保存"


# ---------------------------------------------------------------------------
# 静态结构断言（HTML 骨架 + 安全红线）
# ---------------------------------------------------------------------------


def test_modal_skeleton_has_queue_controls():
    html = _read(STATIC_DIR / "index.html")
    start = html.index('id="parsedDuplicateReviewModal"')
    block = html[start:start + 6000]
    for bid in (
        "duplicateReviewConclusion",
        "duplicateReviewProgress",
        "duplicateReviewDedupeBtn",
        "duplicateReviewMergeBtn",
        "duplicateReviewAssociateBtn",
        "duplicateReviewIndependentBtn",
        "duplicateReviewSkipBtn",
        "duplicateReviewSkipAllBtn",
    ):
        assert f'id="{bid}"' in block, f"弹窗骨架缺少 {bid}"
    assert 'aria-live="polite"' in block, "进度条必须是 live region"
    # 底部按钮一律 JS 绑定，onclick 只剩 × 关闭
    footer = block[block.index('id="duplicateReviewReturnBtn"'):]
    assert "onclick=" not in footer.split("</div>")[0], "队列按钮不得再用内联 onclick"


def test_reason_line_never_uses_translucent_gray():
    source = _import_source()
    start = source.index("function buildParsedDuplicateReasonLine")
    end = source.index("function parsedDuplicateReviewEntries")
    segment = source[start:end]
    assert "text-slate-400" not in segment
    assert "opacity-" not in segment
    assert "font-semibold" in segment


def test_ui_wording_uses_similar_question_term():
    source = _import_source()
    assert "标记为相似题" in source
    assert "已把本题与题库第" in source
    # 底层字段与接口参数名不得被改名
    assert "/associate" in source
    assert "target_id" in source
