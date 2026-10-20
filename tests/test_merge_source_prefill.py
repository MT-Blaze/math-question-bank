"""「并入已有题 · 追加来源」弹窗必须预填拆卷时已填的来源（待办 #23）。

用户路径：拆卷面板填了年份/学校/考试类型 → 查重命中 → 点【并入已有题（仅追加来源）】
→ 弹窗四个框从零重填。根因不是数据丢了：面板控件与 parsedQuestionsData[index].source
都还在内存里，只是 openParsedDuplicateReviewEntryMerge 没把它们传给弹窗。

这里用最小 DOM 桩真跑一遍两个函数（抽取源码片段喂给 node），不用文本断言糊过去——
上一轮 #6 的教训就是静态断言全绿而链路是死的。

⚠️ 片段一律走临时文件喂给 node，不拼进 JS 字符串字面量：static/js 各文件换行符
不统一（import.js 是 CRLF），CR 进单引号字符串会直接 SyntaxError。
"""

import shutil
import subprocess
import tempfile
from pathlib import Path
from modal_transition_stub import MODAL_TRANSITION_STUB_JS  # 弹窗助手在桩里同步揭示


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMPORT_JS = PROJECT_ROOT / "static" / "js" / "import.js"


def _extract(source: str, start_marker: str, end_marker: str) -> str:
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def _extract_function(source: str, signature: str) -> str:
    """按花括号配平切出**整个函数体**。

    拿下一个函数的签名当结束锚点，会把夹在两者之间的「下一函数的块前注释」一起
    吞进来；那段注释若以 // 结尾且后面紧跟拼接出的 return 语句，就会把 return
    一起注释掉 —— new Function 返回 undefined，测试静默失真（本轮实测踩过）。
    """
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    k = brace
    while k < len(source):
        if source[k] == "{":
            depth += 1
        elif source[k] == "}":
            depth -= 1
            if depth == 0:
                return source[start:k + 1]
        k += 1
    raise AssertionError("unbalanced braces after " + signature)


# --------------------------------------------------------------------- DOM 桩

_DOM_STUB_LINES = r"""
class El {
  constructor(tag) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.children = [];
    this.attrs = {};
    this.dataset = {};
    this.classListValues = new Set();
    this.style = {};
    this.parent = null;
    this.value = '';
    this.innerHTML = '';
    this.textContent = '';
    this.onclick = null;
  }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === 'id') this.id = this.attrs[k]; }
  hasAttribute(k) { return k in this.attrs; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(type, fn) { (this.__listeners || (this.__listeners = {}))[type] = fn; }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  removeChild(c) {
    const i = this.children.indexOf(c);
    if (i >= 0) this.children.splice(i, 1);
    c.parent = null;
    return c;
  }
  get parentNode() { return this.parent; }
  get parentElement() { return this.parent; }
  all() {
    const out = [];
    const walk = n => n.children.forEach(c => { out.push(c); walk(c); });
    walk(this);
    return out;
  }
  querySelector(sel) { return this.all().find(n => n.matches(sel)) || null; }
  querySelectorAll(sel) { return this.all().filter(n => n.matches(sel)); }
  matches(sel) {
    if (sel.startsWith('.')) return (this.className || '').split(/\s+/).includes(sel.slice(1));
    if (sel.startsWith('#')) return this.id === sel.slice(1);
    return this.tagName === sel.toUpperCase();
  }
  add() {}
  remove() {}
  contains() { return false; }
}
// classList：弹窗打开时会 toggle hidden/flex，桩必须能记账。
Object.defineProperty(El.prototype, 'classList', {
  get() {
    const self = this;
    return {
      remove(...names) { names.forEach(n => self.classListValues.delete(n)); },
      add(...names) { names.forEach(n => self.classListValues.add(n)); },
      toggle(name, force) {
        if (force === undefined) {
          self.classListValues.has(name)
            ? self.classListValues.delete(name) : self.classListValues.add(name);
        } else if (force) { self.classListValues.add(name); }
        else { self.classListValues.delete(name); }
      }
    };
  }
});
""".strip()


_SCRIPT_HEAD = """
const fs = require('fs');
__DOM_STUB__
const byId = new Map();
function register(node) {
  // innerHTML 在桩里只是字符串，不会真的解析出子节点；弹窗那四个 <input> 因此
  // 必须由测试显式造出来并登记 id —— 被测代码只按 id 取值，不依赖解析结果。
  if (node.id) byId.set(node.id, node);
  node.all().forEach(register);
  return node;
}
global.document = {
  createElement: tag => new El(tag),
  getElementById: id => byId.get(id) || null,
  body: Object.assign(new El('body'), {
    appendChild(child) { child.parent = this; this.children.push(child); register(child); return child; }
  })
};
const win = {
  MathBankModal: { open() {}, close() {} },
  showToast: function () {},
  localStorage: { getItem: () => '' }
};
global.window = win;
global.showToast = win.showToast;
__MODAL_STUB__
global.localStorage = win.localStorage;
global.escapeHtml = value => String(value);
global.setTimeout = (fn) => 0;   // 动画帧不需要跑
const __modalSrc = fs.readFileSync(process.argv[2], 'utf8');
const __entrySrc = fs.readFileSync(process.argv[3], 'utf8');
const __collectSrc = fs.readFileSync(process.argv[4], 'utf8');
const openMergeSourceModal = new Function(
  'document', 'window', 'escapeHtml', 'setTimeout',
  __modalSrc + '\\nreturn openMergeSourceModal;'
)(global.document, win, global.escapeHtml, global.setTimeout);
let capturedPreset = null;      // 必须先声明：包装器在构造期就会执行赋值
const parsedQuestionsData = [];
const parsedDuplicateMergedIndices = new Set();
const closeParsedDuplicateReviewModal = function () {};
const openParsedDuplicateReviewEntryMerge = new Function(
  'document', 'window', 'escapeHtml', 'setTimeout', 'showToast',
  'openMergeSourceModal', 'parsedQuestionsData', 'parsedDuplicateMergedIndices',
  'closeParsedDuplicateReviewModal',
  __entrySrc + '\\n' + __collectSrc + '\\nreturn openParsedDuplicateReviewEntryMerge;'
)(global.document, win, global.escapeHtml, global.setTimeout, win.showToast,
  function (id, seq, cb, preset) { capturedPreset = preset; openMergeSourceModal(id, seq, cb, preset); },
  parsedQuestionsData, parsedDuplicateMergedIndices, closeParsedDuplicateReviewModal);
// 造一个带来源面板控件 + 一张卡片的假页面。
function makePage(fields) {
  byId.clear();
  ['importSrcYear', 'importSrcRegion', 'importSrcExamType'].forEach(id => {
    const el = new El('input');
    el.setAttribute('id', id);
    el.value = fields[id] === undefined ? '' : fields[id];
    global.document.body.appendChild(el);
  });
  const modalInputs = {};
  ['mergeSrcYear', 'mergeSrcRegion', 'mergeSrcExamType', 'mergeSrcPaperTitle', 'mergeSrcQuestionNo'].forEach(id => {
    const el = new El('input');
    el.setAttribute('id', id);
    modalInputs[id] = el;
    // 必须挂进 body：桩的 appendChild 会 register()，弹窗随后按 id 找得到它们。
    global.document.body.appendChild(el);
  });
  // 取消/确认两个按钮同理：函数末尾会给它们绑 onclick，取不到就抛错。
  ['mergeSrcCancel', 'mergeSrcSubmit'].forEach(id => {
    const el = new El('button');
    el.setAttribute('id', id);
    modalInputs[id] = el;
    global.document.body.appendChild(el);
  });
  return modalInputs;
}
"""


def _write(tmp: Path, name: str, text: str) -> Path:
    path = tmp / name
    path.write_text(text, encoding="utf-8", newline="")
    return path


def _run(body: str) -> subprocess.CompletedProcess:
    node = shutil.which("node")
    assert node, "Node.js is required for the frontend regression"
    source = IMPORT_JS.read_text(encoding="utf-8")
    modal_src = _extract_function(source, "function openMergeSourceModal(")
    # 阶段 1：entry-merge 的成功回调不再直接 close，而是走队列包装器；
    # 沙箱里把包装器替成「登记下标即结束审查」的最小实现，保持旧契约可测。
    entry_src = (
        "function queueMergeSuccessFor(entry, closeOnSuccess) {" + chr(10)
        + "    const index = entry && Number.isInteger(entry.index) ? entry.index : null;" + chr(10)
        + "    if (closeOnSuccess) {" + chr(10)
        + "        if (index !== null) parsedDuplicateMergedIndices.add(index);" + chr(10)
        + "        return () => closeParsedDuplicateReviewModal('merged');" + chr(10)
        + "    }" + chr(10)
        + "    return () => closeParsedDuplicateReviewModal('advanced');" + chr(10)
        + "}" + chr(10)
        + _extract_function(
            source, "function openParsedDuplicateReviewEntryMerge("
        )
    )
    collect_src = _extract_function(
        source, "function collectImportSourceFields() {"
    )
    head = _SCRIPT_HEAD.replace("__DOM_STUB_LINES", "_").replace(
        "__DOM_STUB__", _DOM_STUB_LINES
    ).replace("__MODAL_STUB__", MODAL_TRANSITION_STUB_JS)
    with tempfile.TemporaryDirectory(prefix="merge-prefill-") as raw:
        tmp = Path(raw)
        files = [
            _write(tmp, "modal.js", modal_src),
            _write(tmp, "entry.js", entry_src),
            _write(tmp, "collect.js", collect_src),
        ]
        script = _write(tmp, "run.js", head + "\n" + body)
        return subprocess.run(
            [node, str(script), *[str(f) for f in files]],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )


# ----------------------------------------------------------------------- 用例

_ASSERT_PREFILLED = """
const inputs = makePage({
  importSrcYear: '2026',
  importSrcRegion: '哈三中',
  importSrcExamType: '三模'
});
parsedQuestionsData.length = 0;
parsedQuestionsData[7] = { source: '2026届哈三中高三三模数学试卷', paper_question_no: '18' };
openParsedDuplicateReviewEntryMerge(
  { label: '拆解结果第 8 题', index: 7, result: {} },
  { id: 89, seq_num: 89 }, false, null
);
const failures = [];
function expect(id, want) {
  const got = inputs[id].value;
  console.log((got === want ? '  ok  ' : '  FAIL') + ' ' + id + ' = ' + JSON.stringify(got));
  if (got !== want) failures.push(id + ': 期望 ' + JSON.stringify(want) + '，实际 ' + JSON.stringify(got));
}
expect('mergeSrcYear', '2026');
expect('mergeSrcRegion', '哈三中');
expect('mergeSrcExamType', '三模');
expect('mergeSrcPaperTitle', '2026届哈三中高三三模数学试卷');
expect('mergeSrcQuestionNo', '18');
const p = capturedPreset || {};
console.log('PRESET=' + JSON.stringify(p));
if (p.year !== '2026' || p.region !== '哈三中' || p.exam_type !== '三模'
    || p.paper_title !== '2026届哈三中高三三模数学试卷' || p.question_no !== '18') {
  failures.push('preset 未按要求透传：' + JSON.stringify(p));
}
if (failures.length) { console.log('FAILED:\\n - ' + failures.join('\\n - ')); process.exit(1); }
console.log('PREFILL_OK');
"""

_ASSERT_EMPTY_PANEL = """
const inputs = makePage({});   // 面板三项全空、也没有卡片来源
parsedQuestionsData.length = 0;
openParsedDuplicateReviewEntryMerge(
  { label: '拆解结果第 1 题', index: 0, result: {} },
  { id: 5, seq_num: 5 }, false, null
);
const vals = ['mergeSrcYear', 'mergeSrcRegion', 'mergeSrcExamType', 'mergeSrcPaperTitle', 'mergeSrcQuestionNo']
  .map(id => JSON.stringify(inputs[id].value));
console.log('VALUES=' + vals.join(','));
if (vals.some(v => v !== '""')) { console.log('SHOULD_BE_EMPTY'); process.exit(1); }
console.log('EMPTY_OK');
"""

_ASSERT_PANEL_ONLY = """
const inputs = makePage({
  importSrcYear: '2025',
  importSrcRegion: '省实验',
  importSrcExamType: '期末'
});
parsedQuestionsData.length = 0;   // 卡片没有来源文本
openParsedDuplicateReviewEntryMerge(
  { label: '拆解结果第 3 题', index: 2, result: {} },
  { id: 12, seq_num: 12 }, false, null
);
const failures = [];
function expect(id, want) {
  if (inputs[id].value !== want) {
    failures.push(id + ': 期望 ' + JSON.stringify(want) + '，实际 ' + JSON.stringify(inputs[id].value));
  }
  console.log((inputs[id].value === want ? '  ok  ' : '  FAIL') + ' ' + id + ' = ' + JSON.stringify(inputs[id].value));
}
expect('mergeSrcYear', '2025');
expect('mergeSrcRegion', '省实验');
expect('mergeSrcExamType', '期末');
expect('mergeSrcPaperTitle', '');
expect('mergeSrcQuestionNo', '');
if (failures.length) { console.log('FAILED:\\n - ' + failures.join('\\n - ')); process.exit(1); }
console.log('PANEL_ONLY_OK');
"""

_ASSERT_NO_INJECTION = r"""
// 来源文本是用户可控内容：必须以 .value 赋值，绝不能被当成 HTML 解析。
const evil = '<img src=x onerror=alert(1)>';
const inputs = makePage({ importSrcYear: '', importSrcRegion: evil, importSrcExamType: '' });
parsedQuestionsData.length = 0;
parsedQuestionsData[0] = { source: evil };
openParsedDuplicateReviewEntryMerge(
  { label: 'x', index: 0, result: {} }, { id: 1, seq_num: 1 }, false, null
);
const region = inputs.mergeSrcRegion, title = inputs.mergeSrcPaperTitle;
console.log('region.value=' + JSON.stringify(region.value));
const failures = [];
if (region.value !== evil) failures.push('region 值未原样带入');
if (title.value !== evil) failures.push('paper_title 值未原样带入');
if ((region.innerHTML || '').indexOf('<img') >= 0) failures.push('用户内容被拼进了 HTML');
if ((title.innerHTML || '').indexOf('<img') >= 0) failures.push('用户内容被拼进了 HTML(title)');
if (region.children.length || title.children.length) failures.push('生成了真实子元素（注入面）');
if (failures.length) { console.log('FAILED:\n - ' + failures.join('\n - ')); process.exit(1); }
console.log('NO_INJECTION');
"""


_ASSERT_SUBMIT = r"""
global.fetch = function (url, opts) {
  global.__posted = { url: url, body: JSON.parse(opts.body) };
  return Promise.resolve({
    ok: true,
    json: function () { return Promise.resolve({ status: 'success', sources: [] }); }
  });
};
const inputs = makePage({
  importSrcYear: '2026',
  importSrcRegion: '哈三中',
  importSrcExamType: '三模'
});
parsedQuestionsData.length = 0;
parsedQuestionsData[0] = { source: '2026届哈三中高三三模数学试卷', paper_question_no: '18' };
openParsedDuplicateReviewEntryMerge(
  { label: '拆解结果第 1 题', index: 0, result: {} },
  { id: 89 }, false, null
);
if (inputs.mergeSrcQuestionNo.value !== '18') {
  console.log('PREFILL_FAILED=' + JSON.stringify(inputs.mergeSrcQuestionNo.value));
  process.exit(1);
}
inputs.mergeSrcSubmit.onclick();
(async function () {
  await new Promise(resolve => setImmediate(resolve));
  await new Promise(resolve => setImmediate(resolve));
  const posted = global.__posted;
  console.log('POSTED=' + JSON.stringify(posted));
  if (!posted || !/\/api\/questions\/89\/sources$/.test(posted.url)) {
    console.log('SUBMIT_URL_WRONG'); process.exit(1);
  }
  if (posted.body.question_no !== '18') { console.log('QUESTION_NO_NOT_SENT'); process.exit(1); }
  if (posted.body.year !== '2026' || posted.body.region !== '哈三中' || posted.body.exam_type !== '三模') {
    console.log('SOURCE_FIELDS_LOST'); process.exit(1);
  }
  console.log('SUBMIT_OK');
})();
"""


def _assert(result, needle):
    assert result.returncode == 0, result.stdout + result.stderr
    assert needle in result.stdout, result.stdout + result.stderr


def test_merge_source_modal_prefills_from_import_panel_and_card():
    """面板有值 + 卡片有整卷标题 → 四个框全部带出。"""
    _assert(_run(_ASSERT_PREFILLED), "PREFILL_OK")


def test_merge_source_modal_stays_empty_when_nothing_was_filled():
    """面板全空且无卡片来源 → 四框为空且不报错（旧行为保留）。"""
    _assert(_run(_ASSERT_EMPTY_PANEL), "EMPTY_OK")


def test_merge_source_modal_prefills_structured_fields_without_card_source():
    """卡片来源为空但面板有值 → year/region/exam_type 预填、paper_title 留空。"""
    _assert(_run(_ASSERT_PANEL_ONLY), "PANEL_ONLY_OK")


def test_merge_source_modal_prefill_never_interpolates_into_html():
    """预填只能走 .value 赋值；用户可控内容不得变成 HTML 节点。"""
    _assert(_run(_ASSERT_NO_INJECTION), "NO_INJECTION")


def test_merge_source_submits_the_parsed_cards_paper_question_no():
    """并入已有题时，必须把当前拆解卡片的原卷题号随来源一起提交。"""
    _assert(_run(_ASSERT_SUBMIT), "SUBMIT_OK")
