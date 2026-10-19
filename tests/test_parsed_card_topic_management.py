"""拆卷卡片必须能新增考向（③）与改名考向且保住 topic_id（②）。待办 #20 第二步。

两条契约各自要钉住的东西不一样：

③「新增」的关键不是弹窗，而是**写入形态**：push 的必须是 ``{id:'', name: v}`` 对象，
   裸字符串要靠后端 ``normalize_topic_list`` 自愈，两份实现（编辑器 / 拆卷）一旦分叉
   就变成「一边有 ID、一边没有」，而题目侧存的正是 topic_id。手动新增走大纲 CRUD，
   **不经过** mathbank/metadata.py 的 STRICT_AUTO_TOPICS 闸门 —— 闸门保持原样是设计，
   不是漏改。

②「改名」的生死线是 **topic_id 不变**。题目存的是 ``questions.topic_id`` /
   ``question_topics.topic_id``，名字每次由展示层从大纲翻译。所以改名必须按 id 定位、
   只动 name；按名字匹配或「先删后加」都会让旧题指向一个大纲里已不存在的 id，
   显示成空考向 —— 数据没报错，只是再也找不回来。

这里真跑 node（抽源码片段喂进去），不用纯文本断言糊过去：#18/#20① 两轮的教训都是
静态断言全绿而链路是死的。⚠️ 片段走临时文件：static/js 是 CRLF，CR 进单引号字符串
会直接 SyntaxError。
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMPORT_JS = PROJECT_ROOT / "static" / "js" / "import.js"
EDITOR_JS = PROJECT_ROOT / "static" / "js" / "editor.js"

TOPIC_BLOCK_START = "        const PARSED_CARD_EXTRA_TOPIC_LIMIT = 3;"
TOPIC_BLOCK_END = "        // ---- 插图来源标记 + 一键转 TikZ（拆题预览区）----"
HELPER_BLOCK_START = "        // ---- 考向（考点）写入大纲的唯一出口 ----"
HELPER_BLOCK_END = "        window.openAddTopicModal = function() {"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _slice_between(source: str, start: str, end: str) -> str:
    i = source.index(start)
    j = source.index(end, i)
    return source[i:j]


def _extract_function(source: str, signature: str) -> str:
    """按花括号配平切出整个函数体（拿下一个签名当锚点会吞掉块前注释）。"""
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


# ------------------------------------------------------------------ DOM 桩
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
    this.__html = '';
    this.className = '';
    this.onclick = null;
    this.onkeydown = null;
    this.__text = '';
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
  select() {}
  dispatchEvent(ev) {
    const l = this.__listeners && this.__listeners[ev.type];
    if (l) l(ev);
    if (ev && ev.bubbles && this.parent) this.parent.dispatchEvent(ev);
    return true;
  }
  appendChild(c) {
    if (c.parent && c.parent !== this) {
      const i = c.parent.children.indexOf(c);
      if (i !== -1) c.parent.children.splice(i, 1);
    }
    c.parent = this; this.children.push(c); return c;
  }
  insertBefore(node, ref) {
    if (ref && ref.parent === this) {
      const i = this.children.indexOf(ref);
      node.parent = this;
      this.children.splice(i, 0, node);
      return node;
    }
    return this.appendChild(node);
  }
  get parentNode() { return this.parent; }
  get parentElement() { return this.parent; }
  all() {
    const out = [];
    const walk = n => n.children.forEach(c => { out.push(c); walk(c); });
    walk(this);
    return out;
  }
  // 说明：all()/querySelectorAll 走的是**先序深度优先**，与浏览器文档顺序一致。
  // 曾经把它改成自底向上收集（后插入先返回），于是 innerHTML='' 重建下拉后残留的
  // 旧 option 会排到新 options 前面，selectedOptions[0] 取到旧值 —— 看起来像产品
  // 「新旧名同时在下拉里」，其实是桩自己造的假象。
  get textContent() {
    let s = this.__text || '';
    this.children.forEach(c => { s += c.textContent; });
    return s;
  }
  set textContent(v) { this.__text = String(v); this.children = []; }
  // 真实 DOM 里 `el.innerHTML = ''` 会**清空子节点**；被测代码全靠这一手重建下拉。
  // 桩原本把 innerHTML 当纯字符串记账，于是旧 option 永远留在树上 —— 曾据此误判
  // 产品「新旧名同时在下拉里」。赋值时同步清子节点才与浏览器同语义。
  set innerHTML(v) {
    this.__html = String(v);
    if (this.__html === '') {
      this.children.forEach(c => { c.parent = null; });
      this.children = [];
    }
  }
  get innerHTML() { return this.__html || ''; }
  // 真实 DOM：innerHTML 赋「含子节点的标记串」会重建整棵子树。桩只解析 option，
  // 因为被测代码里唯一这么写的是卡片模板的 `topicSelect.innerHTML =
  // '<option value="">-- 选择考向 --</option>'` —— 不解析就会留下一个幻影旧选项，
  // 让「改名后下拉只剩新名」这类断言恒假（本轮实测踩过两次，都误判成产品缺陷）。
  set innerHTML(v) {
    const html = String(v);
    this.__html = html;
    if (html === '') {
      this.children.forEach(c => { c.parent = null; });
      this.children = [];
      return;
    }
    if (this.tagName === 'SELECT') {
      this.children.forEach(c => { c.parent = null; });
      this.children = [];
      const re = /<option\s+value="([^"]*)"[^>]*>/g;
      let m;
      while ((m = re.exec(html)) !== null) {
        const opt = new El('option');
        opt.value = m[1];
        this.appendChild(opt);
      }
    }
  }
  querySelector(sel) { return this.all().find(n => n.matches(sel)) || null; }
  querySelectorAll(sel) { return this.all().filter(n => n.matches(sel)); }
  matches(sel) {
    if (sel.startsWith('.')) {
      const cls = sel.slice(1);
      // className 字符串与 classList.add() 两种写法都要认：真实 DOM 两者同步，
      // 桩若只认前者，被测代码用 row.classList.add(...) 打的标记就查不到。
      return (this.className || '').split(/\s+/).includes(cls) || this.classListValues.has(cls);
    }
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
Object.defineProperty(El.prototype, 'options', {
  get() { return this.querySelectorAll('option'); }
});
// 真实 <select>.value 就是「选中 option 的 value」；桩必须同语义，否则被测代码
// 里 `if (selected && v === selected) opt.selected = true` 这条路根本没被验证 ——
// 桩上直接赋 sel.value 也能过，测试就成了自证。
Object.defineProperty(El.prototype, 'value', {
  // 真实 <select>.value 就是「选中 option 的 value」：只认 option.selected，
  // 不维护独立字段。这样无论代码走 `sel.value = x` 还是 `opt.selected = true`，
  // 读回来都一致；否则两处状态会各说各话，测试结论取决于谁最后写。
  get() {
    if (this.tagName !== 'SELECT') return this.__value || '';
    const opts = this.querySelectorAll('option');
    const hit = opts.find(o => o.selected);
    return hit ? hit.value : '';
  },
  set(v) {
    if (this.tagName === 'SELECT') {
      const opts = this.querySelectorAll('option');
      let matched = false;
      opts.forEach(o => { o.selected = (o.value === v); if (o.selected) matched = true; });
      if (!matched && opts.length) opts[0].selected = true;   // 真实 DOM 落到占位项
      return;
    }
    this.__value = String(v);
  },
  configurable: true
});
""".strip()


CURRICULUM = {
    "必修一": {
        "1. 集合与常用逻辑用语": [
            {"name": "1.1 集合的概念", "topics": [
                {"id": "k_aaa1111111", "name": "集合的判断", "level": "L1"},
                {"id": "k_bbb2222222", "name": "元素与集合的关系", "level": "L1"},
            ]},
            {"name": "1.3 集合的基本运算", "topics": [
                {"id": "k_ccc3333333", "name": "交集运算", "level": "L1"},
            ]},
        ]
    }
}

_SCRIPT_HEAD = r"""
const fs = require('fs');
__DOM_STUB__
__DOM_FACTORY__

// ---- 假后端：GET 返回深拷贝，POST 校验字段后落回 store ----
// 真实接口是「扁平 metadata」：{applicable_grades, question_types, difficulties, curriculum}
// （见 mathbank/routes_settings.save_metadata_config 的必备字段校验）。
let store = {
  applicable_grades: [{ value: 'g1', label: '高一' }],
  question_types: [{ value: 'single_choice', label: '单选题' }],
  difficulties: [{ value: 'basic', label: '基础' }],
  curriculum: __CURRICULUM__
};
let strictGateCalls = 0;         // 用来证明手动新增根本没碰闸门那条路
const postedPayloads = [];
const fetchedUrls = [];
global.fetch = function (url, init) {
  fetchedUrls.push(url + '|' + ((init && init.method) || 'GET'));
  const method = (init && init.method) || 'GET';
  if (method === 'GET') {
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve(JSON.parse(JSON.stringify(store)))
    });
  }
  let payload;
  try { payload = JSON.parse(init.body); } catch (e) {
    return Promise.resolve({ ok: false, json: () => Promise.resolve({ detail: '请求 Payload 格式错误' }) });
  }
  // 复刻 routes_settings.save_metadata_config 的必备字段校验：缺任一即 400。
  for (const field of ['applicable_grades', 'question_types', 'difficulties', 'curriculum']) {
    if (!(field in payload)) {
      return Promise.resolve({
        ok: false,
        json: () => Promise.resolve({ detail: "元数据配置缺少核心字段: '" + field + "'" })
      });
    }
  }
  postedPayloads.push(payload);
  store = payload;
  return Promise.resolve({ ok: true, json: () => Promise.resolve({ status: 'success' }) });
};

const toasts = [];
function showToast(msg, type) { toasts.push([String(msg), type]); }
global.window = {
  categoryTree: {},
  __gFlag: __G_FLAG__,
  // 每次调用现读 __gFlag：绝不能用 defineProperty getter —— Object.assign({}, window)
  // 会把 getter 冻结成拷贝那一刻的布尔值，之后再改 __gFlag 也读不到（实测两个方向
  // 的假结果都出自这里）。
  isGCurriculum: function () { return String(global.window.__gFlag) === '1'; },
  // 真实级联里 populateCategoryDropdowns 只刷 #edit* 那几个编辑器下拉，**不碰**
  // 拆题卡片；卡片的四个 select 由 setupCardCategoryLinkage 自己填。桩必须照做，
  // 否则 refreshParsedCardTopics 里「重跑联动」那一步没被执行也能通过。
  populateCategoryDropdowns: function () {},
  // 真实级联里 populateCategoryDropdowns 只刷 #edit* 编辑器下拉，**不碰**拆题卡片；
  // 卡片的四个 select 由 setupCardCategoryLinkage 自己填。桩照做，否则
  // refreshParsedCardTopics 里「重跑联动」那一步没执行也能通过。
  // systemMetadata 每次现读 store：改名/新增后要按当前名反查 topic_id。
  loadCategories: function () { return Promise.resolve(); }
};

// window.topicName 由 api.js 定义（级联里先于 editor/import）；被测代码直接用它，
// 桩必须提供同款实现，否则整段在第一个考点对象上就抛错。
window.topicName = function (t) {
  if (t && typeof t === 'object') return (typeof t.name === 'string' ? t.name : String(t.name || ''));
  return (t == null ? '' : String(t));
};
// systemMetadata 由 window.loadCategories() 负责写入（见上面注释）
window.loadCategories();

// 桩里必须**自己**复刻真实级联收尾的那次重置：api.loadCategories 之后会跑
// populateCategoryDropdowns → compSelect.onchange()，那一路把卡片四个下拉的选中项
// 退回 q.category_*。原本只在用例里注册 __resetCardFromQ、却没人调用它，于是
// 「刷新前回写坐标」那步被摘掉也照样全绿（反向验证当场暴露了这条假通路）。
function resetCardsFromQuestions() {
  (global.__cards || []).forEach(function (entry) {
    const c = entry.card, qq = entry.q;
    const map = { '.card-compulsory': 'category_compulsory', '.card-chapter': 'category_chapter',
                  '.card-knowledge': 'category_knowledge', '.card-topic': 'topic' };
    Object.keys(map).forEach(function (selKey) {
      const s = c.querySelector(selKey);
      if (!s) return;
      const want = qq[map[selKey]] || '';
      Array.prototype.forEach.call(s.querySelectorAll('option'), function (o) {
        o.selected = (o.value === want);
      });
    });
    global.resetCalls = (global.resetCalls || 0) + 1;
  });
}
global.resetCalls = 0;
global.__cards = [];

const __helperSrc = fs.readFileSync(process.argv[2], 'utf8');
const __blockSrc = fs.readFileSync(process.argv[3], 'utf8');
const __apiRowSrc = fs.readFileSync(process.argv[4], 'utf8');
const __linkageSrc = fs.readFileSync(process.argv[5], 'utf8');
// 拆卷页的改名行复用 api.js 的 metadataTreeDraftRow。按级联的真实形态挂到 window 上，
// 让 buildInlineTopicEditorRow 走**主路径**（onclick 赋值），而不是本地兜底分支
// （addEventListener）—— 两条路的按钮事件读取方式不同，只测兜底路等于没测真链路。
new Function('window', 'document', 'metadataTreeRender',
  __apiRowSrc + '\nreturn metadataTreeDraftRow;'
)(window, document, function () {});
window.metadataTreeDraftRow = new Function('window', 'document', 'metadataTreeRender',
  __apiRowSrc + '\nreturn metadataTreeDraftRow;'
)(window, document, function () {});
// window.createCurriculumTopic = ... 是**赋值**而不是声明，拼接尾部拿不到裸标识符，
// 必须从 window 上取（少取一个就是 ReferenceError，而报错点在脚手架里，很容易被
// 误读成被测代码坏了）。
const helperApi = new Function('window', 'fetch', 'showToast', 'document',
  __helperSrc + '\nreturn { locateCurriculumSection: window.locateCurriculumSection,'
  + ' fetchEditableMetadata: window.fetchEditableMetadata,'
  + ' createCurriculumTopic: window.createCurriculumTopic,'
  + ' renameCurriculumTopic: window.renameCurriculumTopic };'
)(window, global.fetch, showToast, global.document);
global.helpers = helperApi;
// 被测代码里的裸 categoryTree 引用的是 api.js 的模块级变量，真实形态是「window.categoryTree
// 与那份变量指向同一个对象」。桩用**可变盒子 + with** 复刻：loadCategories 每次把新摊平
// 结果写回盒子，联动立刻能看到。直接传快照的话，改名后 refreshParsedCardTopics 重跑
// 联动会读到旧树、把用户刚选中的考向清空 —— 这条用例要抓的就是这种缺陷形态。
// 真实级联里 api.js 的模块变量 categoryTree 与 window.categoryTree **指向同一个对象**，
// loadCategories 是就地重填它。桩照同一语义：盒子里永远放同一个引用，loadCategories
// 清空后重新填充 —— 这样被测代码里的裸 categoryTree 引用才能看到刷新结果。
const sharedTree = {};
const treeBox = { categoryTree: sharedTree };
window.categoryTree = sharedTree;
window.systemMetadata = JSON.parse(JSON.stringify(store));
window.loadCategories = function () {
  // 与真实 api.loadCategories 同口径：先换 metadata 快照，再就地重填共享的 categoryTree。
  window.systemMetadata = JSON.parse(JSON.stringify(store));
  // 真实级联收尾还会跑 populateCategoryDropdowns → compSelect.onchange()，那一路会把
  // **当前卡片**的四个下拉重置成 q.category_* 预填值。桩必须照做，否则被测代码里
  // 「刷新前把卡片坐标回写进 q」这一步永远不会被验证（浏览器实测才发现的坑）。
  resetCardsFromQuestions();
  Object.keys(window.systemMetadata.curriculum).forEach(comp => {
    const chapTree = sharedTree[comp] || (sharedTree[comp] = {});
    Object.keys(window.systemMetadata.curriculum[comp]).forEach(chap => {
      const knowMap = chapTree[chap] || (chapTree[chap] = {});
      (window.systemMetadata.curriculum[comp][chap] || []).forEach(sec => {
        knowMap[sec.name] = sec.topics || [];
      });
    });
  });
  return Promise.resolve();
};
window.loadCategories();
const cardApi = new Function('document', 'window', 'showToast', 'fetch', 'treeBox',
  'with (treeBox) {' + __linkageSrc + '\n' + __blockSrc + '\nreturn {'
  + ' parsedCardTopicId, parsedCardTopicCoordinates, buildInlineTopicEditorRow,'
  + ' openParsedCardCreateTopicModal, openParsedCardRenameTopicFlow,'
  + ' setupParsedCardTopicManagement, refreshParsedCardTopics,'
  + ' renderParsedCardExtraTopics, readParsedCardExtraTopics,'
  + ' collectParsedCardExtraTopics, setupCardCategoryLinkage'
  + ' }; }'
)(document, window, showToast, global.fetch, treeBox);
global.cardApi = cardApi;
global.helpers = global.helpers;
global.toasts = toasts;
global.postedPayloads = postedPayloads;
global.fetchedUrls = fetchedUrls;
global.El = El;
global.getStore = () => store;
global.showToast = showToast;
global.document = document;
"""


def _make_dom():
    """桩需要一个真的 document：被测代码用 document.createElement / body.appendChild。"""
    return r"""
const byId = new Map();
const bodyEl = new El('body');
const document = {
  createElement: tag => new El(tag),
  getElementById: id => byId.get(id) || null,
  body: Object.assign(bodyEl, { appendChild(c) { c.parent = this; this.children.push(c); return c; } })
};
"""


def _run_import_block(body: str, g_curriculum=None, is_g=False) -> subprocess.CompletedProcess:
    """把 editor.js 助手 + import.js 考向段一起喂进 node 跑 body。"""
    node = shutil.which("node")
    assert node, "Node.js is required for the frontend regression"
    editor_src = _read(EDITOR_JS)
    import_src = _read(IMPORT_JS)
    helper_src = _slice_between(editor_src, HELPER_BLOCK_START, HELPER_BLOCK_END)
    block_src = _slice_between(import_src, TOPIC_BLOCK_START, TOPIC_BLOCK_END)
    curriculum = g_curriculum if g_curriculum is not None else CURRICULUM
    head = (_SCRIPT_HEAD
            .replace("__DOM_STUB__", _DOM_STUB_LINES)
            .replace("__DOM_FACTORY__", _make_dom())
            .replace("__CURRICULUM__", json.dumps(curriculum, ensure_ascii=False))
            .replace("__G_FLAG__", "'1'" if is_g else "'0'"))
    with tempfile.TemporaryDirectory(prefix="parsed-topic-mgmt-") as raw:
        tmp = Path(raw)
        helpers = tmp / "helpers.js"
        helpers.write_text(helper_src, encoding="utf-8", newline="")
        block = tmp / "block.js"
        block.write_text(block_src, encoding="utf-8", newline="")
        apirow = tmp / "apirow.js"
        api_src = _read(PROJECT_ROOT / "static" / "js" / "api.js")
        apirow.write_text(
            _extract_function(api_src, "function metadataTreeDraftRow(draft, onOk) {"),
            encoding="utf-8", newline="")
        # 卡片联动函数必须和被测段落在**同一个作用域**：refreshParsedCardTopics 会
        # 回头调 setupCardCategoryLinkage，而它内部引用的 categoryTree 正是这里
        # 用 with 暴露的那份可变盒子。分开注入就等于绕过真实刷新链。
        linkage = tmp / "linkage.js"
        linkage.write_text(
            _extract_function(import_src, "function setupCardCategoryLinkage(card, q) {"),
            encoding="utf-8", newline="")
        script = tmp / "case.js"
        script.write_text(head + _CARD_FACTORY + body, encoding="utf-8", newline="")
        return subprocess.run(
            [node, str(script), str(helpers), str(block), str(apirow), str(linkage)],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )


_CARD_FACTORY = r"""
function makeCard(coords) {
  const card = new El('div');
  card.className = 'parsed-card';
  const keyOf = { 'card-compulsory': 'compulsory', 'card-chapter': 'chapter',
                  'card-knowledge': 'knowledge', 'card-topic': 'topic' };
  ['card-compulsory','card-chapter','card-knowledge','card-topic'].forEach(cls => {
    const s = new El('select');
    s.className = cls;
    // 真实 <select> 的 value 由选中的 <option> 决定；桩里必须照样造出 option，
    // 否则读回来恒空 —— 那会让「坐标缺失」分支在所有用例里假阳性通过。
    const blank = new El('option');
    blank.value = '';
    blank.textContent = '-- 选择 --';
    s.appendChild(blank);
    const opt = new El('option');
    opt.value = coords[keyOf[cls]] || '';
    blank.selected = !opt.value;
    opt.selected = !!opt.value;
    s.appendChild(opt);
    card.appendChild(s);
  });
  ['card-topic-rename','card-topic-create','card-extra-topic-add'].forEach(cls => {
    const b = new El('button');
    b.className = cls;
    card.appendChild(b);
  });
  const list = new El('div'); list.className = 'card-extra-topic-list'; card.appendChild(list);
  const hint = new El('div'); hint.className = 'card-topic-manage-hint'; card.appendChild(hint);
  return card;
}
function registerCard(card, q) { global.__cards.push({ card: card, q: q }); }
const COORDS = {
  compulsory: '必修一', chapter: '1. 集合与常用逻辑用语',
  knowledge: '1.1 集合的概念', topic: '集合的判断'
};
"""


def _ok(proc: subprocess.CompletedProcess) -> str:
    assert proc.returncode == 0, f"node failed:\n{proc.stderr}\n{proc.stdout}"
    return proc.stdout


def test_shared_helpers_exist_and_are_exported_once():
    """两个入口必须共用同一份实现，不许拆卷侧再抄一套。"""
    editor_src = _read(EDITOR_JS)
    import_src = _read(IMPORT_JS)
    for name in ("createCurriculumTopic", "renameCurriculumTopic"):
        assert ("window." + name + " = function") in editor_src, name
        assert ("window." + name) in import_src, name + " 未被拆卷页复用"
    # 定位助手是 create/rename 的内部依赖，只由 editor.js 使用；单独断言它在用
    assert editor_src.count("window.locateCurriculumSection(meta, comp, chap, know") >= 2
    assert editor_src.count("window.fetchEditableMetadata()") >= 2
    # editor.js 自己的新增入口也必须改成走助手，不能再留一份就地写大纲的代码
    modal_body = _extract_function(editor_src, "window.openAddTopicModal = function() {")
    assert "window.createCurriculumTopic(comp, chap, know, newTopic)" in modal_body
    assert "section.topics.push(newTopic)" not in modal_body, "旧的第二份实现还留着"


def test_create_topic_pushes_object_not_bare_string():
    """③核心：写进大纲的必须是 {id:'', name: v}，裸字符串会让两份实现分叉。"""
    body = r"""
const q = {};
const card = makeCard(COORDS);
cardApi.setupParsedCardTopicManagement(card, q);
const createBtn = card.querySelector('.card-topic-create');
if (!createBtn) throw new Error('新增按钮不存在');
const btnListener = createBtn.__listeners && createBtn.__listeners.click;
if (typeof btnListener !== 'function') throw new Error('新增按钮没绑 click');
btnListener();
const dialog = Array.from(document.body.children).reverse()
  .find(n => n.getAttribute && n.getAttribute('role') === 'dialog');
if (!dialog) throw new Error('点新增后弹窗没出现');
const input = dialog.querySelectorAll('input')[0];
input.value = '新定义运算';
const okBtn = dialog.querySelectorAll('button').find(b => b.textContent.trim() === '确认新增');
okBtn.click();
setTimeout(() => {}, 0);
Promise.resolve().then(() => {}).then(() => {}).then(() => {}).then(() => {}).then(() => {
  const posts = global.postedPayloads;
  if (posts.length !== 1) throw new Error('POST 次数=' + posts.length);
  const secs = posts[0].curriculum['必修一']['1. 集合与常用逻辑用语'];
  const sec = secs.find(s => s.name === '1.1 集合的概念');
  const added = sec.topics[sec.topics.length - 1];
  if (typeof added === 'string') throw new Error('push 的是裸字符串: ' + added);
  if (added.name !== '新定义运算') throw new Error('名字不对: ' + JSON.stringify(added));
  if (!('id' in added)) throw new Error('对象里没有 id 键: ' + JSON.stringify(added));
  console.log('OK');
}).catch(e => { console.error(e); process.exitCode = 1; });
""";
    assert "OK" in _ok(_run_import_block(body))


def test_create_topic_requires_all_three_coordinates():
    """坐标不全就拒绝，不能把考向种到解析不到的小节上。"""
    body = r"""
const card = makeCard({ compulsory: '必修一', chapter: '', knowledge: '', topic: '' });
cardApi.openParsedCardCreateTopicModal(card, {});
if (global.postedPayloads.length !== 0) throw new Error('坐标缺失仍写了大纲');
if (!global.toasts.some(t => /请先选择/.test(t[0]))) throw new Error('未提示先选坐标: ' + JSON.stringify(global.toasts));
console.log('OK');
"""
    assert "OK" in _ok(_run_import_block(body))


def test_comma_in_topic_name_is_rejected_before_post():
    """逗号是考点列表的分隔语义，必须在写库前挡下（与编辑器同口径）。"""
    body = r"""
global.helpers.createCurriculumTopic('必修一', '1. 集合与常用逻辑用语', '1.1 集合的概念', '含参,讨论')
  .then(() => { throw new Error('带逗号竟被放行'); })
  .catch(err => {
    if (!/逗号/.test(err.message)) throw new Error('拒绝理由不对: ' + err.message);
    if (global.postedPayloads.length !== 0) throw new Error('被拒仍发了 POST');
    console.log('OK');
  });
"""
    assert "OK" in _ok(_run_import_block(body))


def test_manual_create_does_not_touch_the_auto_topic_gate():
    """手动新增走大纲 CRUD，压根不调 _resolve_topic_id 那条自动建档路径。

    闸门（STRICT_AUTO_TOPIC_CREATION）保持开着是本次的设计前提；这条用例钉的是
    「前端没有为了绕闸去猜名字或改后端」。
    """
    body = r"""
// 闸门在后端；这里能验证的是：新增只发一次 config/metadata 的 GET+POST，
// 没有任何「按名字试探」的请求。
global.helpers.createCurriculumTopic('必修一', '1. 集合与常用逻辑用语', '1.1 集合的概念', '新定义运算')
  .then(() => {
    const urls = global.fetchedUrls.map(u => u.split('|')[0]);
    const uniq = Array.from(new Set(urls));
    if (uniq.length !== 1 || uniq[0] !== '/api/config/metadata') {
      throw new Error('出现了预期外接口: ' + JSON.stringify(uniq));
    }
    const methods = global.fetchedUrls.filter(u => u.indexOf('/api/config/metadata|') === 0).map(u => u.split('|')[1]);
    if (methods.join(',') !== 'GET,POST') throw new Error('请求序列不对: ' + methods.join(','));
    console.log('OK');
  });
"""
    assert "OK" in _ok(_run_import_block(body))


def test_g_curriculum_blocks_create_and_disables_buttons():
    """G 大纲「小节即考向」：不报错、不开弹窗、按钮置灰并给说明。"""
    g_tree = {
        "集合·复数·逻辑": {
            "集合": [{"name": "集合的基本运算", "topics": []}]
        }
    }
    body = r"""
const card = makeCard({ compulsory: '集合·复数·逻辑', chapter: '集合', knowledge: '集合的基本运算', topic: '' });
cardApi.setupParsedCardTopicManagement(card, {});
const createBtn = card.querySelector('.card-topic-create');
const renameBtn = card.querySelector('.card-topic-rename');
if (!createBtn.disabled) throw new Error('G 大纲下新增按钮未置灰');
if (!renameBtn.disabled) throw new Error('G 大纲下改名按钮未置灰');
createBtn.__listeners.click({});
const dialog = Array.from(document.body.children).reverse()
  .find(n => n.getAttribute && n.getAttribute('role') === 'dialog');
if (dialog) throw new Error('G 大纲下仍弹出了新增窗');
if (global.postedPayloads.length !== 0) throw new Error('G 大纲下仍写了大纲');
if (!global.toasts.some(t => /小节即考向/.test(t[0]))) {
  throw new Error('未给出 G 特例提示: ' + JSON.stringify(global.toasts));
}
const hint = card.querySelector('.card-topic-manage-hint');
if (!/小节即考向/.test(hint.textContent)) throw new Error('提示行文案不对: ' + hint.textContent);
console.log('OK');
"""
    assert "OK" in _ok(_run_import_block(body, g_curriculum=g_tree, is_g=True))


def test_rename_keeps_topic_id_and_locates_by_id():
    """②生死线：改名后 id 一字不动，且定位是按 id 而不是按名字。"""
    body = r"""
const before = global.getStore().curriculum['必修一']['1. 集合与常用逻辑用语']
  .find(s => s.name === '1.1 集合的概念').topics.map(t => ({ id: t.id, name: t.name }));
global.helpers.renameCurriculumTopic(
  '必修一', '1. 集合与常用逻辑用语', '1.1 集合的概念', 'k_aaa1111111', '集合的含义与确定性')
  .then(() => {
    const after = global.getStore().curriculum['必修一']['1. 集合与常用逻辑用语']
      .find(s => s.name === '1.1 集合的概念').topics.map(t => ({ id: t.id, name: t.name }));
    if (after[0].id !== before[0].id) throw new Error('topic_id 变了！' + before[0].id + ' → ' + after[0].id);
    if (after[0].name !== '集合的含义与确定性') throw new Error('名字没改: ' + after[0].name);
    if (after.length !== before.length) throw new Error('条数变了（疑似先删后加）: ' + before.length + '→' + after.length);
    // 其余考向的 id 必须原样保留
    if (after[1].id !== before[1].id) throw new Error('误伤了别的考向 id');
    console.log('OK');
  });
"""
    assert "OK" in _ok(_run_import_block(body))


def test_rename_never_matches_by_name():
    """按 id 找不到就直接拒，绝不退化成按名字改（那会造孤儿 topic_id）。"""
    body = r"""
global.helpers.renameCurriculumTopic(
  '必修一', '1. 集合与常用逻辑用语', '1.1 集合的概念', 'k_不存在的不确定ID', '随便一个新名')
  .then(() => { throw new Error('查不到 id 竟然还改了'); })
  .catch(err => {
    if (!/找不到 ID|没有稳定 ID/.test(err.message)) throw new Error('拒绝理由不对: ' + err.message);
    if (global.postedPayloads.length !== 0) throw new Error('被拒仍发了 POST');
    const sec = global.getStore().curriculum['必修一']['1. 集合与常用逻辑用语']
      .find(s => s.name === '1.1 集合的概念');
    if (sec.topics[0].name !== '集合的判断') throw new Error('大纲已被改写');
    console.log('OK');
  });
"""
    assert "OK" in _ok(_run_import_block(body))


def test_rename_rejects_duplicate_name_within_section():
    """同小节重名要挡：否则下拉里两个同名项，反查 id 会取错。"""
    body = r"""
global.helpers.renameCurriculumTopic(
  '必修一', '1. 集合与常用逻辑用语', '1.1 集合的概念', 'k_aaa1111111', '元素与集合的关系')
  .then(() => { throw new Error('同名覆盖被放行'); })
  .catch(err => {
    if (!/同名/.test(err.message)) throw new Error('拒绝理由不对: ' + err.message);
    if (global.postedPayloads.length !== 0) throw new Error('被拒仍发了 POST');
    console.log('OK');
  });
"""
    assert "OK" in _ok(_run_import_block(body))


def test_card_topic_id_lookup_uses_current_names():
    """卡片只有名字，必须先反查出 id 才能安全改名；查不到就不许改。"""
    body = r"""
const hit = cardApi.parsedCardTopicId('必修一', '1. 集合与常用逻辑用语', '1.1 集合的概念', '元素与集合的关系');
if (hit !== 'k_bbb2222222') throw new Error('反查 id 失败: ' + hit);
const miss = cardApi.parsedCardTopicId('必修一', '1. 集合与常用逻辑用语', '1.1 集合的概念', '不存在的名');
if (miss !== '') throw new Error('查不到应返回空串: ' + miss);

// 改名流程：无 id 的考向必须拒绝并引导去设置页处理，不能静默按名字改
const card = makeCard({ compulsory: '必修一', chapter: '1. 集合与常用逻辑用语', knowledge: '1.1 集合的概念', topic: '不存在的名' });
cardApi.openParsedCardRenameTopicFlow(card, {});
if (global.postedPayloads.length !== 0) throw new Error('无 id 仍写了大纲');
if (!global.toasts.some(t => /稳定 ID/.test(t[0]))) {
  throw new Error('未提示无 ID: ' + JSON.stringify(global.toasts));
}
console.log('OK');
"""
    assert "OK" in _ok(_run_import_block(body))


def test_inline_editor_submits_cancels_and_escapes():
    """内联编辑三态：✓提交、✗取消、Esc 取消；空值不得提交。"""
    body = r"""
const calls = [];
const row = cardApi.buildInlineTopicEditorRow('旧名', function (v) { calls.push(['submit', v]); },
                                              function () { calls.push(['cancel', null]); });
const inp = row.querySelectorAll('input')[0];
if (inp.value !== '旧名') throw new Error('初值没带上: ' + inp.value);
const btns = row.querySelectorAll('button');
if (btns.length !== 2) throw new Error('应有 ✓/✗ 两个按钮，实得 ' + btns.length);
// api.js 的 metadataTreeDraftRow 用 ok.onclick = commit 赋值；桩就照这个语义触发。
if (!btns[0].onclick) throw new Error('确认按钮没走 onclick 赋值（不是 api.js 那个助手）');
function fire(el) { el.onclick({ preventDefault(){} }); }
inp.value = '  新名字  ';
fire(btns[0]);
if (calls.length !== 1 || calls[0][0] !== 'submit' || calls[0][1] !== '新名字') {
  throw new Error('提交通道不对: ' + JSON.stringify(calls));
}
const row2 = cardApi.buildInlineTopicEditorRow('旧名', function (v) { calls.push(['submit2', v]); },
                                               function () { calls.push(['cancel2', null]); });
// ✗ 必须回调 onCancel：api.js 助手原本只做「清草稿 + 重绘大纲树」，在拆卷卡片里
// 那等于点了没反应、编辑行一直挂着。包装层把它转成对调用方的显式取消通知。
const cancelBtn = row2.querySelectorAll('button')[1];
if (!cancelBtn.onclick) throw new Error('取消按钮没走 onclick 赋值');
cancelBtn.onclick({ preventDefault(){} });
const last = calls[calls.length - 1];
if (!last || last[0] !== 'cancel2') throw new Error('✗ 没有回调 onCancel: ' + JSON.stringify(calls));
if (calls.some(c => String(c[0]).indexOf('submit') === 0 && c !== calls[0])) {
  throw new Error('✗ 竟触发了提交: ' + JSON.stringify(calls));
}
const row3 = cardApi.buildInlineTopicEditorRow('旧名', function (v) { calls.push(['submit3', v]); },
                                               function () { calls.push(['cancel3', null]); });
row3.querySelectorAll('input')[0].value = '';
const kd = row3.querySelectorAll('input')[0].onkeydown;
if (!kd) throw new Error('输入框没有 onkeydown（回车/Esc 通道断了）');
kd({ key: 'Enter', preventDefault(){} });
if (calls.some(c => String(c[0]).indexOf('submit3') === 0)) throw new Error('空值被提交了');
console.log('OK');
"""
    assert "OK" in _ok(_run_import_block(body))


def test_rename_flow_opens_inline_row_then_refreshes_dropdown():
    """完整用户路径：点 ✎ → 就地出现编辑行 → 提交 → 下拉换成新名并保持选中。"""
    body = r"""
// 关键设定：q 上的 AI 预填坐标与小节 **不同于** 用户后来在卡片上改成的目标小节。
// 真实级联收尾会把下拉退回 q.category_*，所以被测代码必须先把卡片当前坐标回写进 q；
// 两边一致的话这条路径永远测不到（本轮浏览器实测 + 反向验证都栽在这里）。
// 卡片当前坐标：小节与 q 的预填值**不同**，考向必须选一个大纲里带 id 的真实考向
// （桩 CURRICULUM 里 1.1 只有「集合的判断」「元素与集合的关系」有 id）。
const CARD_COORDS = { compulsory: '必修一', chapter: '1. 集合与常用逻辑用语',
                      knowledge: '1.1 集合的概念', topic: '元素与集合的关系' };
const card = makeCard(CARD_COORDS);
const qForCard = { category_compulsory: '必修一', category_chapter: '1. 集合与常用逻辑用语',
                   category_knowledge: '1.3 集合的基本运算', topic: '交集运算' };
cardApi.setupParsedCardTopicManagement(card, qForCard);
// 把这张卡片登记给桩的级联重置副作用（见 harness 里的 resetCardsFromQuestions）：
// 真实 api.loadCategories 收尾会把四个下拉退回 q.category_*，所以被测代码必须在
// 刷新前把卡片当前坐标回写进 q，否则用户刚选的考向会被抹掉。
registerCard(card, qForCard);
const renameBtn = card.querySelector('.card-topic-rename');
// 这条断言本身就是回归：曾经 setupParsedCardTopicManagement 没接进联动链，
// 按钮永远不置灰、也永远点不动（disabled 恒为初始 false）。
if (renameBtn.disabled) throw new Error('A 大纲下改名按钮不该置灰');
renameBtn.__listeners.click({});
const row = card.querySelector('.card-topic-rename-host');
if (!row) throw new Error('点 ✎ 后没有出现内联编辑行');
const hint = card.querySelector('.card-topic-manage-hint');
if (!/写入教材大纲|影响全部题目/.test(hint.textContent)) {
  throw new Error('缺少全站影响提示: ' + JSON.stringify(hint.textContent));
}
// 退路提示也必须在这行里 —— 用户手输错字要能找到「设置 → 大纲维度」删回来
if (!/删除考点/.test(hint.textContent)) {
  throw new Error('缺少写错后的退路指引: ' + JSON.stringify(hint.textContent));
}
const inp = row.querySelectorAll('input')[0];
inp.value = '集合的含义与确定性';
const okBtn = row.querySelectorAll('button').find(b => (b.title || '') === '确认');
if (!okBtn) throw new Error('内联行里没有确认按钮');
if (!okBtn.onclick) throw new Error('确认按钮没走 onclick 赋值');
okBtn.onclick({ preventDefault(){} });
let ticks = 0;
function drain(done) {
  const step = () => {
    ticks += 1;
    if (ticks > 60) { done(new Error('微任务队列 60 轮仍未收敛')); return; }
    Promise.resolve().then(step);
  };
  Promise.resolve().then(() => {
    const check = () => {
      if (global.postedPayloads.length >= 1) { done(null); return; }
      step();
    };
    check();
  });
}
drain(() => { try { verify(); } catch (e) { console.error(e); process.exitCode = 1; } });
function verify() {
  const posts = global.postedPayloads;
  if (posts.length !== 1) throw new Error('POST 次数=' + posts.length);
  const topics = posts[0].curriculum['必修一']['1. 集合与常用逻辑用语']
    .find(s => s.name === '1.1 集合的概念').topics;
  const renamed = topics.find(x => x.id === 'k_bbb2222222');
  if (!renamed || renamed.name !== '集合的含义与确定性') {
    throw new Error('改名未落到目标考向: ' + JSON.stringify(topics));
  }
  // 别的考向 id 不许被动过
  if (topics[0].id !== 'k_aaa1111111' || topics[0].name !== '集合的判断') {
    throw new Error('误伤了同小节其他考向: ' + JSON.stringify(topics[0]));
  }
  const sel = card.querySelector('.card-topic');
  const values = sel.options.map(o => o.value).filter(Boolean);
  // 真实级联收尾会把卡片下拉重置成 q 上的值；若 refresh 没先回写坐标，这里会看到
  // 改名前的旧列表（本轮浏览器实测发现的坑，必须能被测试抓住）。
  if (global.resetCalls < 1) throw new Error('桩的级联重置副作用没跑到，这条用例等于没测');
  if (qForCard.topic !== '集合的含义与确定性') {
    throw new Error('刷新前未把新考向回写到题目对象，级联重置会把它抹掉: ' + JSON.stringify(qForCard));
  }
  if (qForCard.category_knowledge !== '1.1 集合的概念') {
    throw new Error('刷新前未回写小节坐标，级联重置会把下拉退回 1.3 集合的基本运算: '
      + qForCard.category_knowledge);
  }
  // 下拉里必须**恰好**是改名后的两条：幻影旧名（桩没解析 innerHTML 里的 <option>）
  // 与重复项（refresh 自己建一遍、联动又建一遍）都要在这里被抓住。
  if (values.length !== 2) {
    throw new Error('下拉选项数应为 2，实得 ' + values.length + ': ' + JSON.stringify(values));
  }
  if (values.join(',') !== '集合的判断,集合的含义与确定性') {
    throw new Error('下拉内容与大纲不一致: ' + JSON.stringify(values));
  }
  if (sel.value !== '集合的含义与确定性') throw new Error('没有保持选中: ' + sel.value);
  console.log('OK');
}
"""
    assert "OK" in _ok(_run_import_block(body))
    # POST 之后到下拉刷新还隔着 loadCategories 那一串 Promise；drain() 已经轮询到
    # payload 落库才 verify，所以这里不再靠猜微任务层数。


def test_wiring_is_present_in_template_and_linkage():
    """模板锚点 + 联动接线都得在：摘任一条，用户看到的都是「入口根本不出现」。"""
    source = _read(IMPORT_JS)
    template = _slice_between(source, "card.innerHTML = `", "`;")
    for cls in ("card-topic-rename", "card-topic-create", "card-topic-manage-hint"):
        assert cls in template, cls + " 不在卡片模板里"
    linkage = _extract_function(source, "function setupCardCategoryLinkage(card, q) {")
    assert "setupParsedCardTopicManagement(card, q);" in linkage, "联动末尾没接管理入口"


def test_api_exports_the_inline_row_helper():
    """改名行复用 api.js 的 metadataTreeDraftRow，它必须真的导出。"""
    api_src = _read(PROJECT_ROOT / "static" / "js" / "api.js")
    assert "window.metadataTreeDraftRow = metadataTreeDraftRow;" in api_src
    # 复用到非树上下文后，无条件重绘大树会变成 ReferenceError，必须包一层守卫
    row_fn = _extract_function(api_src, "function metadataTreeDraftRow(draft, onOk) {")
    assert "typeof window.metadataTreeRender === 'function'" in row_fn
    assert row_fn.count("metadataTreeRender();") <= 3


def test_backend_gate_left_untouched():
    """闸门本身不许被这次改动顺手放宽。"""
    from mathbank.metadata import STRICT_AUTO_TOPIC_CREATION
    assert STRICT_AUTO_TOPIC_CREATION is True
    src = _read(PROJECT_ROOT / "mathbank" / "metadata.py")
    assert 'MATHBANK_STRICT_AUTO_TOPICS", "1"' in src


def test_linkage_lights_up_cascade_from_dirty_ai_category_names():
    r"""卡片联动的连锁锁死：章名一旦对不上大纲键，小节与考向会跟着一起空掉。

    后端 7.6 已把 AI 分类字段归一到大纲规范名，这条是第二道防线（Word/TeX/图片录入
    不经过那道归一），钉三个形态：整行「章 > 小节」抄写、章写飞但小节抄对、全空不报错。
    """
    body = r"""
function blankCard() {
  const card = new El('div');
  card.className = 'parsed-card';
  ['card-compulsory','card-chapter','card-knowledge','card-topic','card-difficulty']
    .forEach(function (cls) {
      const s = new El('select');
      s.className = cls;
      const blank = new El('option');
      blank.value = '';
      blank.selected = true;
      s.appendChild(blank);
      card.appendChild(s);
    });
  ['card-topic-rename','card-topic-create','card-extra-topic-add'].forEach(function (cls) {
    const b = new El('button');
    b.className = cls;
    card.appendChild(b);
  });
  ['card-extra-topic-list','card-topic-manage-hint'].forEach(function (cls) {
    const d = new El('div');
    d.className = cls;
    card.appendChild(d);
  });
  return card;
}

function linkValues(card) {
  return ['.card-compulsory','.card-chapter','.card-knowledge','.card-topic']
    .map(function (sel) { return card.querySelector(sel).value; }).join('|');
}

const copied = {
  category_compulsory: '必修一',
  category_chapter: '1. 集合与常用逻辑用语 > 1.3 集合的基本运算',
  category_knowledge: '1.3 集合的基本运算',
  topic: '交集运算',
  difficulty: 'L1'
};
const card = blankCard();
cardApi.setupCardCategoryLinkage(card, copied);
const got = linkValues(card);
if (got !== '必修一|1. 集合与常用逻辑用语|1.3 集合的基本运算|交集运算') {
  throw new Error('整行抄写的章名没能点亮三排下拉: ' + got);
}

const wrong = {
  category_compulsory: '必修一',
  category_chapter: '9. 模型瞎写的章',
  category_knowledge: '1.1 集合的概念',
  topic: '集合的判断'
};
const card2 = blankCard();
cardApi.setupCardCategoryLinkage(card2, wrong);
const got2 = linkValues(card2);
if (got2 !== '必修一|1. 集合与常用逻辑用语|1.1 集合的概念|集合的判断') {
  throw new Error('小节反查章失效: ' + got2);
}

const card3 = blankCard();
cardApi.setupCardCategoryLinkage(card3, {});
if (card3.querySelector('.card-chapter').value !== '') {
  throw new Error('空分类被凭空选中了章');
}
console.log('OK');
"""
    assert "OK" in _ok(_run_import_block(body))
