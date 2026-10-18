"""拆卷卡片必须能挂第二个考点（待办 #20 第一步）。

用户报的三个子问题里，①「无法增加第二个考点」是**纯前端缺失**：
`question_topics` 中间表、`extra_topics` Form 字段（routes_questions.py 的
create/update）、编辑器路径的附加考向 UI 全都就绪，唯独拆题卡片只有一个
`.card-topic` 单选下拉，保存载荷也只 append `topic`。

这里用最小 DOM 桩真跑一遍新代码（抽取源码片段喂给 node），不用文本断言糊过去 ——
#18/#23 两轮的教训都是静态断言全绿而链路是死的。

⚠️ 片段一律走临时文件喂给 node：static/js/import.js 是 CRLF，CR 进单引号字符串
会直接 SyntaxError。
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMPORT_JS = PROJECT_ROOT / "static" / "js" / "import.js"

_BLOCK_START = "        const PARSED_CARD_EXTRA_TOPIC_LIMIT = 3;"
_BLOCK_END = "        // ---- 插图来源标记 + 一键转 TikZ（拆题预览区）----"


def _extract_block(source: str) -> str:
    """切出整段附加考向实现（7 个函数 + 上限常量）。

    这些函数互相调用且引用模块作用域的 categoryTree/showToast，只抽单个函数
    一定拼不出可运行的片段。
    """
    start = source.index(_BLOCK_START)
    end = source.index(_BLOCK_END, start)
    return source[start:end]


def _extract_function(source: str, signature: str) -> str:
    """按花括号配平切出**整个函数体**。

    拿下一个函数签名当结束锚点会把中间的块前注释一起吞进来；注释以 // 结尾时
    会连拼接出的 return 一起注释掉 —— new Function 返回 undefined 而测试照样绿
    （#23 那轮实测踩过）。
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
    this.disabled = false;
    this.innerHTML = '';
    this.textContent = '';
    this.className = '';
  }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === 'id') this.id = this.attrs[k]; }
  hasAttribute(k) { return k in this.attrs; }
  addEventListener(type, fn) { (this.__listeners || (this.__listeners = {}))[type] = fn; }
  click() { const l = this.__listeners && this.__listeners.click; if (l) l({}); }
  remove() {
    if (!this.parent) return;
    const kids = this.parent.children;
    const i = kids.indexOf(this);
    if (i !== -1) kids.splice(i, 1);
    this.parent = null;
  }
  focus() {}
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  get parentNode() { return this.parent; }
  get parentElement() { return this.parent; }
  all() {
    const out = [];
    const walk = n => n.children.forEach(c => { out.push(c); walk(c); });
    walk(this);
    return out;
  }
  // 真实 DOM 的 textContent 会拼接整棵子树；桩必须同款，否则「文本只走
  // textContent」这条安全断言会因为父节点自身没有文本而误判失败。
  get textContent() {
    let s = this.__text || '';
    this.children.forEach(c => { s += c.textContent; });
    return s;
  }
  set textContent(v) {
    this.__text = String(v);
    this.children = [];   // 真实 DOM：赋 textContent 会清掉子节点
  }
  querySelector(sel) { return this.all().find(n => n.matches(sel)) || null; }
  querySelectorAll(sel) { return this.all().filter(n => n.matches(sel)); }
  matches(sel) {
    if (sel.startsWith('.')) return (this.className || '').split(/\s+/).includes(sel.slice(1));
    if (sel.startsWith('#')) return this.id === sel.slice(1);
    return this.tagName === sel.toUpperCase();
  }
}
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
// option 的 selected 在真实 DOM 里决定 select.value；桩按同一口径实现读取。
Object.defineProperty(El.prototype, 'selectedOptions', {
  get() {
    if (this.tagName !== 'SELECT') return [];
    const opts = this.querySelectorAll('option');
    const hit = opts.find(o => o.selected);
    return hit ? [hit] : (opts.length ? [opts[0]] : []);
  }
});
""".strip()


_SCRIPT_HEAD = r"""
const fs = require('fs');
__DOM_STUB__
const byId = new Map();
global.document = {
  createElement: tag => new El(tag),
  getElementById: id => byId.get(id) || null,
  body: Object.assign(new El('body'), {
    appendChild(child) { child.parent = this; this.children.push(child); return child; }
  })
};
const toasts = [];
function showToast(msg, type) { toasts.push(String(msg)); }
// 被测代码引用模块作用域的 categoryTree 与 showToast；new Function 的参数表就是
// 它们的注入点 —— 少注入一个都会 ReferenceError，而那正是「接线是死的」的形态。
const TREE = __TREE_JSON__;
const __blockSrc = fs.readFileSync(process.argv[2], 'utf8');
const api = new Function(
  'document', 'window', 'categoryTree', 'showToast',
  __blockSrc + '\nreturn {'
  + ' normalizeParsedCardExtraTopic, readParsedCardExtraTopics,'
  + ' collectParsedCardExtraTopics, renderParsedCardExtraTopics,'
  + ' setupCardExtraTopics, openParsedCardExtraTopicPicker,'
  + ' buildParsedCardExtraTopicPicker, PARSED_CARD_EXTRA_TOPIC_LIMIT'
  + ' };'
)(global.document, global.window || {}, TREE, showToast);
global.api = api;
global.showToast = showToast;
global.toasts = toasts;
global.El = El;
global.byId = byId;
"""


def _run(tree: dict, body: str) -> subprocess.CompletedProcess:
    node = shutil.which("node")
    assert node, "Node.js is required for the frontend regression"
    source = IMPORT_JS.read_text(encoding="utf-8")
    block_src = _extract_block(source)
    head = _SCRIPT_HEAD.replace("__DOM_STUB__", _DOM_STUB_LINES).replace(
        "__TREE_JSON__", json.dumps(tree, ensure_ascii=False)
    )
    with tempfile.TemporaryDirectory(prefix="parsed-extra-topic-") as raw:
        tmp = Path(raw)
        block = tmp / "block.js"
        block.write_text(block_src, encoding="utf-8", newline="")
        script = tmp / "case.js"
        script.write_text(head + _MAKE_CARD_JS + body, encoding="utf-8", newline="")
        return subprocess.run(
            [node, str(script), str(block)],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )


TREE = {
    "必修一": {
        "1. 集合与常用逻辑用语": {
            "1.1 集合的概念": [{"name": "集合的判断", "level": "L1"}],
            "1.3 集合的基本运算": [
                {"name": "交集运算", "level": "L1"},
                {"name": "补集运算", "level": "L2"},
            ],
        }
    },
    "必修二": {"6. 平面向量及其应用": {"6.1 向量的概念": [{"name": "向量线性运算"}]}},
}


def _result(proc: subprocess.CompletedProcess) -> str:
    assert proc.returncode == 0, f"node failed: {proc.stderr}\n{proc.stdout}"
    return proc.stdout


def _make_card():
    """造一张带 .card-extra-topic-add / .card-extra-topic-list 的假卡片。"""
    return r"""
function makeCard() {
  const card = new El('div');
  card.className = 'parsed-card';
  const addBtn = new El('button');
  addBtn.className = 'card-extra-topic-add';
  card.appendChild(addBtn);
  const list = new El('div');
  list.className = 'card-extra-topic-list';
  card.appendChild(list);
  return card;
}
""".strip()


_MAKE_CARD_JS = "\n" + _make_card() + "\n"


def test_extracted_block_is_the_full_implementation():
    """钉住切片范围：漏掉任何一个函数，下面的用例都会在拼接期炸掉。"""
    source = IMPORT_JS.read_text(encoding="utf-8")
    block = _extract_block(source)
    for name in (
        "function normalizeParsedCardExtraTopic",
        "function readParsedCardExtraTopics",
        "function collectParsedCardExtraTopics",
        "function renderParsedCardExtraTopics",
        "function buildParsedCardExtraTopicPicker",
        "function setupCardExtraTopics",
        "function openParsedCardExtraTopicPicker",
    ):
        assert name in block, name
    assert "PARSED_CARD_EXTRA_TOPIC_LIMIT = 3" in block


def test_add_second_topic_and_payload_carries_extra_topics():
    """①的核心契约：入口点击 → 选到第二考点 → chips 出 1 枚 → 载荷含 extra_topics。"""
    body = r"""
const card = makeCard();
const q = {};
api.setupCardExtraTopics(card, q);
if (!card.querySelector('.card-extra-topic-add')) throw new Error('入口按钮不存在');
// 真正写入走 picker 的 onPick 回调：把弹窗造出来，逐级选中同册另一小节的考向。
const picked = api.buildParsedCardExtraTopicPicker(card, q, function (slot) {
  api.readParsedCardExtraTopics(q).push(slot);
  api.renderParsedCardExtraTopics(card, q);
});
// 从弹窗里那四个 select 取值：册次/章节/小节/考点逐级点亮
const selects = picked.querySelectorAll('select');
if (selects.length !== 4) throw new Error('期望 4 级下拉，实得 ' + selects.length);
function pick(sel, value) {
  sel.querySelectorAll('option').forEach(o => { o.selected = (o.value === value); });
  sel.value = value;
  const l = sel.__listeners && sel.__listeners.change;
  if (l) l({});
}
pick(selects[0], '必修一');
pick(selects[1], '1. 集合与常用逻辑用语');
pick(selects[2], '1.3 集合的基本运算');
pick(selects[3], '补集运算');
const okBtn = picked.querySelectorAll('button').find(b => b.textContent === '确认添加');
if (!okBtn) throw new Error('确认按钮缺失');
okBtn.click();
if (api.collectParsedCardExtraTopics(q).length !== 1) {
  throw new Error('附加考向没写进 q.extraTopics: ' + JSON.stringify(q.extraTopics));
}
const chips = card.querySelector('.card-extra-topic-list').children;
if (chips.length !== 1) throw new Error('chips 数量=' + chips.length);
if (!String(chips[0].textContent).includes('补集运算')) {
  throw new Error('chip 文案不含考向名: ' + chips[0].textContent);
}
console.log(JSON.stringify(api.collectParsedCardExtraTopics(q)));
"""
    out = _result(_run(TREE, body))
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload == [{
        "compulsory": "必修一",
        "chapter": "1. 集合与常用逻辑用语",
        "knowledge": "1.3 集合的基本运算",
        "topic": "补集运算",
    }], payload


def test_entry_button_is_actually_wired():
    """入口按钮必须真的绑上 click —— 只查 DOM 存在会漏掉「按钮在、点了没反应」。

    实测口径：桩里 addEventListener 会把 handler 记到 __listeners.click，
    未绑定就是 undefined；这一条专门钉住 dataset.bound 守卫被改坏、或
    setupCardExtraTopics 压根没被调用的静默失效。
    """
    body = r"""
const card = makeCard();
const q = {};
api.setupCardExtraTopics(card, q);
const btn = card.querySelector('.card-extra-topic-add');
if (!btn) throw new Error('入口按钮不存在');
const l = btn.__listeners && btn.__listeners.click;
if (typeof l !== 'function') throw new Error('入口按钮没有绑 click（点了没反应）');
// 点一次应当真的弹出四级选择器（往 body 挂 dialog）
const before = global.document.body.children.length;
l({});
if (global.document.body.children.length <= before) throw new Error('点击后弹窗没有出现');
console.log('OK');
"""
    assert "OK" in _result(_run(TREE, body))


def test_real_page_wires_the_entry_button():
    """真页面口径：卡片模板 + setupCardCategoryLinkage 末尾那行接线都得钉住。

    直接调 api.setupCardExtraTopics 只能证明函数本身没坏；实测「摘掉联动末尾的
    setupCardExtraTopics(card, q);」时其余用例全绿（桩绕过了真实入口），而这正是
    用户看到的「按钮压根不出现」形态 —— #18/#23 两轮都栽在同类静默失效上。
    """
    source = IMPORT_JS.read_text(encoding="utf-8")
    template = _extract_card_template(source)
    linkage = _extract_function(source, "function setupCardCategoryLinkage(card, q) {")

    assert "card-extra-topic-add" in template, "卡片模板里没有附加考向按钮"
    assert "card-extra-topic-list" in template, "卡片模板里没有 chips 容器"
    assert "setupCardExtraTopics(card, q);" in linkage, (
        "setupCardCategoryLinkage 没有接上 setupCardExtraTopics —— 入口永远不出现"
    )


def _extract_card_template(source: str) -> str:
    """切出拆题卡片的 innerHTML 模板字符串（含新加的附加考向那一行）。"""
    marker = "card.innerHTML = `"
    start = source.index(marker)
    end = source.index("`;", start)
    return source[start:end]


def test_limit_three_is_enforced():
    """上限 3：第 4 次尝试既不入库也不弹弹窗，只 toast。"""
    body = r"""
const card = makeCard();
const q = { extraTopics: [
  { compulsory: '必修一', chapter: '1. 集合与常用逻辑用语', knowledge: '1.1 集合的概念', topic: 'A' },
  { compulsory: '必修一', chapter: '1. 集合与常用逻辑用语', knowledge: '1.1 集合的概念', topic: 'B' },
  { compulsory: '必修一', chapter: '1. 集合与常用逻辑用语', knowledge: '1.1 集合的概念', topic: 'C' },
] };
const before = global.toasts.length;
const opened = api.openParsedCardExtraTopicPicker(card, q);
if (opened !== false) throw new Error('已满 3 条仍打开了选择器');
if (q.extraTopics.length !== 3) throw new Error('超额写入: ' + q.extraTopics.length);
if (global.toasts.length <= before) throw new Error('未提示上限');
// collect 侧同样裁剪到 3（防外部把 4 条塞进 q.extraTopics）
q.extraTopics.push({ compulsory: 'X', chapter: 'Y', knowledge: 'Z', topic: 'W' });
if (api.collectParsedCardExtraTopics(q).length !== 3) throw new Error('collect 未裁剪');
console.log('OK');
"""
    assert "OK" in _result(_run(TREE, body))


def test_backfill_from_backend_topics_array():
    """详情回传 topics（含 sort_order>0）时，卡片初始加载要回填成附加考向。"""
    body = r"""
const card = makeCard();
const q = { topics: [
  { sort_order: 0, compulsory: '必修一', chapter: '1. 集合与常用逻辑用语', knowledge: '1.1 集合的概念', topic: '主考向' },
  { sort_order: 1, compulsory: '必修一', chapter: '1. 集合与常用逻辑用语', knowledge: '1.3 集合的基本运算', topic: '交集运算' },
  { sort_order: 2, compulsory: '必修二', chapter: '6. 平面向量及其应用', knowledge: '6.1 向量的概念', topic: '向量线性运算' },
] };
api.setupCardExtraTopics(card, q);
const got = api.collectParsedCardExtraTopics(q);
if (got.length !== 2) throw new Error('回填条数=' + got.length + ' :: ' + JSON.stringify(got));
if (got.map(g => g.topic).join(',') !== '交集运算,向量线性运算') {
  throw new Error('回填内容不对: ' + JSON.stringify(got));
}
const chips = card.querySelector('.card-extra-topic-list').children;
if (chips.length !== 2) throw new Error('chips 未同步: ' + chips.length);
console.log('OK');
"""
    assert "OK" in _result(_run(TREE, body))


def test_slot_texts_never_interpolate_into_innerHTML():
    """考向名是用户/AI 可控文本：只能 textContent，不得拼进 innerHTML。"""
    body = r"""
const card = makeCard();
const evil = '<img src=x onerror=alert(1)>';
const q = { extraTopics: [{ compulsory: evil, chapter: evil, knowledge: evil, topic: evil }] };
api.renderParsedCardExtraTopics(card, q);
const chip = card.querySelector('.card-extra-topic-list').children[0];
if (chip.innerHTML.indexOf('onerror') !== -1) {
  throw new Error('chip.innerHTML 里出现了注入面: ' + chip.innerHTML);
}
if (chip.textContent.indexOf(evil) === -1) throw new Error('文本没走 textContent');
console.log('OK');
"""
    assert "OK" in _result(_run(TREE, body))


def test_save_payload_appends_extra_topics_alongside_topic():
    """写库边界：拆卷保存必须同时带 topic 与 extra_topics（与编辑器路径同格式）。"""
    source = IMPORT_JS.read_text(encoding="utf-8")
    idx = source.index("if (topic) formData.append('topic', topic);")
    window = source[idx:idx + 700]
    assert "formData.append('extra_topics'" in window, window[:400]
    assert "collectParsedCardExtraTopics(q)" in window, window[:400]
    # 主考向仍然先发，附加考向紧随其后；顺序变了说明改动落错了分支
    assert window.index("'topic'") < window.index("'extra_topics'")
