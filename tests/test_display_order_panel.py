# -*- coding: utf-8 -*-
"""批1排序面板回归：真实执行 import.js 里的面板代码段（vm + 最小 DOM 桩）。

静态断言只能证明代码存在；这里把面板 IIFE 连同其闭包依赖喂给 node 跑通
「渲染 → 📌 置顶重排 → POST 置顶表」与「✎ 改名 → PUT 载荷」两条链，
并钉住「拖动已彻底移除」（行首无手柄、无 pointerdown 监听）。
"""
import shutil
import subprocess
import tempfile
from pathlib import Path
from modal_transition_stub import as_js_literal  # 弹窗助手在 Node 桩里必须同步揭示

PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMPORT_JS = PROJECT_ROOT / "static" / "js" / "import.js"


def _extract_panel_source() -> str:
    text = IMPORT_JS.read_text(encoding="utf-8")
    start = text.index("// ===== 省/市/校展示顺序面板（批1改版")
    end = text.index("window.openDisplayOrderPanel = openDisplayOrderPanel;", start)
    end = text.index("})();", end) + len("})();")
    return text[start:end]


HARNESS = r"""
const vm = require('vm');
const panelSource = %PANEL%;

class El {
    constructor(tag) {
        this.tagName = (tag || 'div').toUpperCase();
        this.children = [];
        this.dataset = {};
        this._classes = new Set();
        this.style = {};
        this.attrs = {};
        this.listeners = {};
        this.textContent = '';
        this._innerHTML = '';
        this.value = '';
        this.disabled = false;
        this.hidden = false;
    }
    get classList() {
        const self = this;
        return {
            add: (...cs) => { cs.forEach(c => self._classes.add(c)); self.__clsDirty = true; },
            remove: (...cs) => { cs.forEach(c => self._classes.delete(c)); self.__clsDirty = true; },
            contains: (c) => self._classes.has(c),
            toggle: (c, force) => {
                const want = force === undefined ? !self._classes.has(c) : !!force;
                if (want) self._classes.add(c); else self._classes.delete(c);
                self.__clsDirty = true;
                return want;
            },
        };
    }
    get className() {
        if (this.__clsDirty) { this._classes = new Set(String(this._className || '').split(/\s+/).filter(Boolean)); this.__clsDirty = false; }
        return Array.from(this._classes).join(' ');
    }
    set className(v) { this._className = v; this._classes = new Set(String(v).split(/\s+/).filter(Boolean)); this.__clsDirty = false; }
    get innerHTML() { return this._innerHTML; }
    set innerHTML(v) { this._innerHTML = String(v); if (v === '') this.children = []; }
    // 浏览器语义：给 textContent 赋空串会**清空全部子节点**。桩早先只记字符串不删子节点，
    // 于是面板里 `bar.textContent = ''` 重建 tab 栏后旧按钮仍挂在 children 上——
    // find() 会命中上一次留下的陈旧节点，任何「重建后状态」断言都不可信。
    set textContent(v) {
        this._textContent = String(v);
        if (this._textContent === '') this.children = [];
    }
    get textContent() {
        if (this.children.length) return this.children.map(c => c.textContent).join('');
        return this._textContent || '';
    }
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
    removeEventListener(type, fn) {
        const arr = this.listeners[type] || [];
        const i = arr.indexOf(fn);
        if (i >= 0) arr.splice(i, 1);
    }
    dispatchEvent(ev) { (this.listeners[ev.type] || []).forEach(fn => fn(ev)); return true; }
    appendChild(node) {
        if (node.tagName === '#FRAGMENT') { node.children.slice().forEach(c => this.appendChild(c)); return node; }
        if (node.parentNode) node.parentNode.removeChild(node);
        this.children.push(node); node.parentNode = this; return node;
    }
    replaceChild(next, prev) {
        const i = this.children.indexOf(prev);
        if (i < 0) throw new Error('replaceChild: not a child');
        this.children[i] = next; next.parentNode = this; return prev;
    }
    replaceWith(next) {
        if (!this.parentNode) throw new Error('replaceWith without parent');
        this.parentNode.replaceChild(next, this);
    }
    removeChild(node) { const i = this.children.indexOf(node); if (i >= 0) this.children.splice(i, 1); return node; }
    setAttribute(k, v) { this.attrs[k] = String(v); if (k === 'role') this.role = String(v); }
    // 浏览器语义：属性不存在时 getAttribute 返回 **null**。桩若用 `in` 判断，
    // removeAttribute 后仍会读到旧值，任何「属性已移除」断言都在假数据上跑。
    getAttribute(k) { return k in this.attrs ? String(this.attrs[k]) : null; }
    removeAttribute(k) { delete this.attrs[k]; }
    closest(sel) {
        let n = this;
        while (n) { if (matches(n, sel)) return n; n = n.parentNode; }
        return null;
    }
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
    querySelectorAll(sel) {
        // 面板源码用 '[data-modal-close]' / '#addSchoolError' / 'input,button,select' 三类
        // 选择器；桩的 matches() 只认 tag/.class，属性与 id 选择器必须单独支持才能断言接线。
        const s = sel.trim();
        const attr = /^\[([a-zA-Z-]+)\]$/.exec(s);
        const byId = /^#([\w-]+)$/.exec(s);
        const out = [];
        const walk = (node) => node.children.forEach(c => {
            if (attr ? (attr[1] in c.attrs)
                : byId ? String(c.id) === byId[1]
                : matches(c, s)) out.push(c);
            walk(c);
        });
        walk(this);
        return out;
    }
    focus() {
        let r = this; while (r.parentNode) r = r.parentNode;
        if (r.__doc) { r.__doc.activeElement = this; return; }   // 挂到根 → 写 doc.activeElement
        if (!this.parentNode) throw new Error('focus on detached element');
        r.__active = this;
    }
    select() {}
    getBoundingClientRect() {
        const self = this;
        return { top: Number(self.attrs['data-top'] || 0), bottom: Number(self.attrs['data-top'] || 0) + 40, left: 0, right: 300, height: 40 };
    }
    setAttribute(k, v) { this.attrs[k] = String(v); if (k === 'role') this.role = String(v); }
    // 浏览器语义：属性不存在时 getAttribute 返回 **null**。桩若用 `in` 判断，
    // removeAttribute 后仍会读到旧值，任何「属性已移除」断言都在假数据上跑。
    getAttribute(k) { return k in this.attrs ? String(this.attrs[k]) : null; }
    removeAttribute(k) { delete this.attrs[k]; }
}
function matches(el, sel) {
    // 支持 'input,button,select'、'.cls'、'span.min-w-0' 这几种本测试用到的形式；
    // className 走 getter（含 __clsDirty 重解析），与面板读法同一口径
    return sel.split(',').some(one => {
        one = one.trim();
        const parts = one.split('.').filter(Boolean);
        const tag = parts[0];
        if (tag && el.tagName !== tag.toUpperCase()) return false;
        const cls = String(el.className || '').split(/\s+/).filter(Boolean);
        return parts.slice(1).every(c => cls.includes(c));
    });
}

/**
 * DOM 桩的类名读写必须与浏览器语义一致：className 赋值要**整体替换**类集合。
 * 旧桩只记字符串、getter 里缓存不失效，于是「先 hidden 后 remove('hidden')」
 * 仍会报 hidden ——任何可见性断言在这种桩上都是恒真的。
 */
function makeDoc() {
    const byId = {};
    ['displayOrderModal','displayOrderTitle','displayOrderTabs','displayOrderSelectors','displayOrderList',
     'displayOrderStatus','displayOrderSaveBtn','displayOrderCancelBtn',
     'importSrcProvince','importSrcCity','importSrcRegion'].forEach(id => { byId[id] = new El('div'); });
    byId.importSortOrderBtn = new El('button');
    // 桩的遍历根：运行时创建的节点都挂在这里（body/documentElement 之外另登记一份）
    const rootEl = new El('#stub-root');
    rootEl.appendChild(byId.displayOrderModal);
    return {
        __root: rootEl,
        body: (() => { const b = new El('body'); rootEl.appendChild(b); return b; })(),
        createElement: (t) => new El(t),
        createTextNode: (t) => { const n = new El('#text'); n.textContent = t; return n; },
        createDocumentFragment: () => new El('#fragment'),
        // 浏览器语义：运行时 createElement + appendChild(body) 出来的带 id 节点也必须能查到。
        // 桩原先只查预注册表，弹窗里 `getElementById('addSchoolSubmitBtn')` 恒为 null，
        // 于是所有依赖现查的接线（错误位、按钮态）在桩上静默失效——断言全绿而链路是死的。
        getElementById: (id) => {
            if (byId[id]) return byId[id];
            let found = null;
            const walk = (node) => {
                for (const c of node.children) {
                    if (found) return;
                    if (String(c.id) === id) { found = c; return; }
                    walk(c);
                }
            };
            walk(rootEl);
            return found;
        },
        addEventListener: () => {},
        documentElement: new El('html'),
        // body 已在上面建好并挂进遍历根；这里**不得**再写第二个 `body:` 键——
        // 对象字面量后者覆盖前者，会把一棵游离的新 body 交出去（appendChild 还会把
        // 旧 body 从根上摘走），于是运行时弹窗挂到游离树上，getElementById 恒为 null。
        activeElement: null,
        __byId: byId,
    };
}

const calls = [];
const doc = makeDoc();
// 弹窗外壳由面板自己切换 hidden/flex（MathBankModal 只管焦点/Escape/隔离），
// 桩必须预置 index.html 的初始类，否则「可见性切换」断言恒真。
doc.__byId.displayOrderModal.className = 'fixed inset-0 z-[95] hidden items-center justify-center opacity-0';
doc.__byId.displayOrderModal.attrs['aria-hidden'] = 'true';
{
    const surface = new El('div');
    surface.className = 'glass-modal scale-95 flex-col';
    surface.attrs['data-modal-surface'] = '';
    const closeBtn = new El('button');
    closeBtn.attrs['data-modal-close'] = '';
    surface.appendChild(closeBtn);
    ['displayOrderTitle','displayOrderTabs','displayOrderSelectors','displayOrderList','displayOrderStatus','displayOrderSaveBtn','displayOrderCancelBtn'].forEach(id => {
        surface.appendChild(doc.__byId[id]);
    });
    doc.__byId.displayOrderModal.appendChild(surface);
}

// 真实页面里 MathBankModal 会 focus() 弹窗内元素；桩必须把焦点记到文档上，
// 才能断言「焦点进弹窗」这条卡死修复的核心契约（El.focus 沿 parentNode 上溯写入）。
const _rootEl = new El('#document-root');
_rootEl.__doc = doc;   // focus() 上溯到根后，经此引用写回 doc.activeElement
_rootEl.appendChild(doc.__byId.displayOrderModal);
Object.defineProperty(doc, 'activeElement', {
    get() { return this.__active || null; },
    set(v) { this.__active = v; },
});
// 桩里 byId 注册的节点是「游离」的（面板代码用 getElementById 直接取），
// focus() 沿 parentNode 上溯会到不了根。登记父链：这些节点的父 = surface/modal，
// 已在上面 appendChild；importSrc* 三个表单元素不在弹窗内，单独挂到 root。
['importSrcProvince','importSrcCity','importSrcRegion'].forEach(id => _rootEl.appendChild(doc.__byId[id]));
const pendingDomReady = [];
doc.addEventListener = (type, fn) => { if (type === 'DOMContentLoaded') pendingDomReady.push(fn); };
const winListeners = {};
function fireWin(type, ev) { (winListeners[type] || []).slice().forEach(fn => fn(ev)); }
const sandbox = {
    document: doc,
    window: {
        LOCAL_TOKEN: 'test-token',
        MathBankModal: { open: (m, o) => { calls.push(['open', o]); m.attrs['aria-hidden'] = 'false';
                         // 模拟 api.js rAF 里的焦点转移：优先 options.initialFocus
                         if (o && o.initialFocus && typeof o.initialFocus.focus === 'function') o.initialFocus.focus(); },
                         close: (m) => { calls.push(['close']); m.attrs['aria-hidden'] = 'true'; } },
        addEventListener: (type, fn) => { (winListeners[type] = winListeners[type] || []).push(fn); },
        removeEventListener: (type, fn) => {
            const arr = winListeners[type] || [];
            const i = arr.indexOf(fn);
            if (i >= 0) arr.splice(i, 1);
        },
        _schoolsDict: [],
        _regionsTree: [],
        fetch: null,
    },
    navigator: {},
    setTimeout: (fn, ms) => { sandbox.__lastTimer = fn; if (ms >= 300) (sandbox.__longTimers = sandbox.__longTimers || []).push(fn); return 1; },
    clearTimeout: () => { sandbox.__lastTimer = null; },
    Promise, Event: class Event { constructor(t) { this.type = t; } },
    console, JSON, Array, Object, String, Number, Math, Error, Boolean, RegExp, isNaN, parseInt, parseFloat,
};
sandbox.window.fetch = async (url, opts) => {
    const method = (opts && opts.method) || 'GET';
    calls.push([method, url, opts && opts.body]);
    if (String(url).startsWith('/api/display-order') && method === 'GET') {
        // 新契约：provinces 是置顶表 {_:[...]}，counts 三层齐备
        return { ok: true, status: 200, json: async () => ({ status: 'success',
            provinces: { _: ['黑龙江省'] }, cities: {'黑龙江省': ['大庆市','哈尔滨市']},
            schools: { '黑龙江省|哈尔滨市': ['哈三中','哈师大附中','师大附中','师附','哈六中','哈九中','德强','省实验','兆麟中学','道外一中','尚志中学'] },
            counts: { provinces: {}, cities: {'黑龙江省|大庆市': 4}, schools: { '哈三中': 9 } } }) };
    }
    return { ok: true, status: 200, json: async () => ({ status: 'success' }) };
};
// 真实页面里裸 fetch 就是 window.fetch；vm 沙箱必须显式给出，否则保存路径静默 ReferenceError
sandbox.fetch = sandbox.window.fetch;
sandbox.globalThis = sandbox;

const script = %MODAL_STUB% + panelSource + '\n;globalThis.__panelOpen = window.openDisplayOrderPanel;';
vm.createContext(sandbox);
vm.runInContext(script, sandbox);
sandbox.window.openDisplayOrderPanel = sandbox.globalThis.__panelOpen;
// 真实页面里 DOMContentLoaded 早已触发；桩在面板加载后立即触发绑定，
// 让后续断言能直接检查 ✕/保存/取消的监听器。只跑一次：重复注册 window 级
// pointermove 会让拖动断言翻倍执行。
pendingDomReady.forEach(fn => fn());

async function main() {
    doc.__byId.importSrcProvince.value = '黑龙江省';
    sandbox.window._regionsTree = [
        { province: '黑龙江省', cities: ['哈尔滨市','大庆市','齐齐哈尔市'] },
        { province: '吉林省', cities: ['长春市'] },
    ];
    sandbox.window._schoolsDict = [
        { id: 'sch_hsf_0001', name: '哈师大附中', full_name: '哈尔滨师范大学附属中学', short_name: '哈师大附中', province: '黑龙江省', city: '哈尔滨市' },
        { id: 'sch_h3z_0002', name: '哈三中', full_name: '哈尔滨市第三中学校', short_name: '哈三中', province: '黑龙江省', city: '哈尔滨市' },
        { id: 'sch_dqsy03', name: '大庆实验', full_name: '大庆实验中学', short_name: '大庆实验', province: '黑龙江省', city: '大庆市' },
    ];
    await sandbox.window.openDisplayOrderPanel();
    // —— 焦点与可见性（卡死修复的两半，改版不得回退）
    const openCall = calls.find(c => c[0] === 'open');
    if (!openCall || !openCall[1]) throw new Error('MathBankModal.open 未被调用或未传 options');
    if (!openCall[1].initialFocus) throw new Error('open 缺 initialFocus：静态 data-modal-initial-focus 会被首次打开抹掉');
    if (openCall[1].initialFocus !== doc.__byId.displayOrderTitle) throw new Error('initialFocus 应指向弹窗标题');
    const modalEl = doc.__byId.displayOrderModal;
    if (modalEl.classList.contains('hidden') || !modalEl.classList.contains('flex')) {
        throw new Error('open 后弹窗仍 hidden/未 flex，不可见');
    }
    if (modalEl.classList.contains('opacity-0')) throw new Error('打开后 opacity-0 未移除，弹窗透明');
    if (doc.activeElement !== doc.__byId.displayOrderTitle) throw new Error('焦点未进入弹窗标题');
    const closeBtnEl = modalEl.querySelector('[data-modal-close]');
    const foundClose = closeBtnEl || (function find(n) {
        for (const c of n.children) { if ('data-modal-close' in c.attrs) return c; const deep = find(c); if (deep) return deep; }
        return null;
    })(modalEl);
    if (!(foundClose.listeners['click'] || []).length) throw new Error('✕ 关闭按钮没有绑定 click');
    const boundSave = (doc.__byId.displayOrderSaveBtn.listeners['click'] || []).slice();
    const listEl = doc.__byId.displayOrderList;

    // —— 省档：置顶在前，其余按引用次数降序 → 字母序
    const names = listEl.children.map(r => r.dataset.name);
    if (JSON.stringify(names) !== JSON.stringify(['黑龙江省','吉林省'])) throw new Error('省档顺序错误: ' + names);
    // 拖动彻底移除：行首必须是置顶按钮，且不再有拖拽手柄/pointerdown
    const provRow = listEl.children[0];
    if (provRow.children[0].tagName !== 'BUTTON') throw new Error('行首应为置顶按钮，实际 ' + provRow.children[0].tagName);
    if ((provRow.children[0].listeners['pointerdown'] || []).length) throw new Error('仍存在拖拽 pointerdown 监听');
    if (listEl.textContent.indexOf('⠿') >= 0) throw new Error('拖拽手柄 ⠿ 仍在界面上');

    // —— 📌 置顶吉林省：立即重排到首位，aria-pressed 跟随
    const secondRow = listEl.children[1];
    const pinBtn = secondRow.children[0];
    if (pinBtn.getAttribute('aria-pressed') !== 'false') throw new Error('未置顶的行 aria-pressed 应为 false');
    (pinBtn.listeners['click'] || []).forEach(fn => fn({ stopPropagation(){} }));

    const after = doc.__byId.displayOrderList.children.map(r => r.dataset.name);
    if (after[0] !== '吉林省') throw new Error('点置顶后未排到最前: ' + after);
    if (doc.__byId.displayOrderList.children[0].children[0].getAttribute('aria-pressed') !== 'true') {
        throw new Error('置顶态未反映到 aria-pressed');
    }

    // —— 保存：POST 载荷是置顶表（provinces._ 为数组），不是整层全序
    boundSave.forEach(fn => fn({}));

    for (let i = 0; i < 6; i++) await new Promise(r => process.nextTick(r));
    const posts = calls.filter(c => c[0] === 'POST' && String(c[1]).includes('display-order'));
    const post = posts.pop();
    if (!post) throw new Error('保存没有发 POST /api/display-order');
    const body = JSON.parse(post[2]);
    if (!body.provinces || !Array.isArray(body.provinces._)) throw new Error('POST provinces 必须是 {_:[...]} 置顶表: ' + post[2]);
    if (body.provinces._[0] !== '吉林省') throw new Error('POST 置顶顺序错误: ' + JSON.stringify(body.provinces));
    // 后端 POST 是整文件替换：载荷只带自己那层的改动会清空其他层（见下方跨层断言）

    // —— 未触碰的层必须原样回传：省档保存时市/校两层的已存置顶不得消失
    const provSaveBody = JSON.parse(calls.filter(c => c[0] === 'POST' && String(c[1]).includes('display-order')).pop()[2]);
    if (!provSaveBody.cities['黑龙江省'] || provSaveBody.cities['黑龙江省'].join(',') !== '大庆市,哈尔滨市') {
        throw new Error('保存省档把市层置顶抹掉了: ' + JSON.stringify(provSaveBody.cities));
    }
    // 校层的已存桶此时还没被点开（载入时只建空会话副本），原样回传即可；
    // 真正要钉的是：GET 里给了 schools 桶，载荷就必须带上它。
    if (JSON.stringify(Object.keys(provSaveBody.schools)) !== JSON.stringify(['黑龙江省|哈尔滨市'])) {
        throw new Error('保存省档把校层置顶抹掉了: ' + JSON.stringify(provSaveBody.schools));
    }

    // —— 校档：引用次数徽标 + 📌 置顶 + ✎ 改名
    const tabs = doc.__byId.displayOrderTabs.children;
    (tabs[2].listeners['click'] || []).forEach(fn => fn({}));
    let schoolRows = doc.__byId.displayOrderList.children.map(r => r.dataset.name);
    // 未置顶时纯按引用次数降序 → 字母序（桩里 counts.schools 只给了哈三中=9）
    if (schoolRows.join(',') !== '哈三中,大庆实验,哈师大附中') throw new Error('校档次数序错误: ' + schoolRows);
    const h3Row = doc.__byId.displayOrderList.children.find(r => r.dataset.name === '哈三中');
    const badge = h3Row.children.map(c => String(c.textContent)).filter(t => /^\d+题$/.test(t));
    if (badge[0] !== '9题') throw new Error('引用次数徽标缺失或错误: ' + JSON.stringify(h3Row.children.map(c => c.textContent)));
    // 📌 置顶「哈师大附中」（0 引用）必须压过 9 引用的哈三中
    const lowRow = doc.__byId.displayOrderList.children.find(r => r.dataset.name === '哈师大附中');
    (lowRow.children[0].listeners['click'] || []).forEach(fn => fn({ stopPropagation(){} }));
    schoolRows = doc.__byId.displayOrderList.children.map(r => r.dataset.name);
    if (schoolRows[0] !== '哈师大附中') throw new Error('校档置顶未生效: ' + schoolRows);
    const firstRow = doc.__byId.displayOrderList.children.find(r => r.dataset.name === '哈师大附中');
    const pen = firstRow.children[firstRow.children.length - 1];
    (pen.listeners['click'] || []).forEach(fn => fn({ stopPropagation(){} }));
    const input = firstRow.querySelector('input');
    if (!input) throw new Error('✎ 未切换为输入框');
    input.value = '师大附';
    (input.listeners['keydown'] || []).forEach(fn => fn({ key: 'Enter', preventDefault(){} }));
    for (let i = 0; i < 6; i++) await new Promise(r => process.nextTick(r));
    const put = calls.filter(c => c[0] === 'PUT');
    if (!put.length) throw new Error('改名没有发 PUT /api/school-short-name');
    const putBody = JSON.parse(put[put.length - 1][2]);
    if (putBody.short_name !== '师大附' || putBody.id !== 'sch_hsf_0001') throw new Error('PUT 载荷错误: ' + put[put.length - 1][2]);
    if ('full_name' in putBody || 'name' in putBody) throw new Error('PUT 仍按名称定位: ' + put[put.length - 1][2]);
    if (sandbox.window._schoolsDict.find(s => s.name === '哈师大附中')) throw new Error('本地词库仍有旧名残留');
    console.log('BATCH1 PANEL OK');
}
main().catch(e => { console.error('FAIL:', e.stack || e.message); process.exit(1); });
"""


def test_display_order_panel_pin_rename_in_real_js():
    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    panel = _extract_panel_source()
    harness = HARNESS.replace("%PANEL%", repr(panel)).replace("%MODAL_STUB%", as_js_literal())
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(harness)
        path = f.name
    result = subprocess.run([node, path], capture_output=True, text=True, encoding="utf-8", timeout=60)
    Path(path).unlink(missing_ok=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "BATCH1 PANEL OK" in result.stdout


# ---------------------------------------------------------------------------
# 「+ 新增学校」：真跑 node，钉住按钮接线 -> 表单构建 -> POST 载荷 -> 失败不关窗
# ---------------------------------------------------------------------------

ADD_SCHOOL_HARNESS = r"""
const vm = require('vm');
const panelSource = %PANEL%;
__DOM_STUB__

const calls = [];
const doc = makeDoc();
doc.__byId.displayOrderModal.className = 'fixed inset-0 z-[95] hidden items-center justify-center opacity-0';
doc.__byId.displayOrderModal.attrs['aria-hidden'] = 'true';
{
    const surface = new El('div');
    surface.className = 'glass-modal scale-95 flex-col';
    surface.attrs['data-modal-surface'] = '';
    const closeBtn = new El('button');
    closeBtn.attrs['data-modal-close'] = '';
    surface.appendChild(closeBtn);
    ['displayOrderTitle','displayOrderTabs','displayOrderSelectors','displayOrderList','displayOrderStatus','displayOrderSaveBtn','displayOrderCancelBtn'].forEach(id => {
        surface.appendChild(doc.__byId[id]);
    });
    doc.__byId.displayOrderModal.appendChild(surface);
}
const _rootEl = new El('#document-root');
_rootEl.__doc = doc;
_rootEl.appendChild(doc.__byId.displayOrderModal);
_rootEl.appendChild(doc.__root);   // 桩的遍历根：body 必须留在 doc 内，不得被上树时摘走
Object.defineProperty(doc, 'activeElement', { get() { return this.__active || null; }, set(v) { this.__active = v; } });
['importSrcProvince','importSrcCity','importSrcRegion'].forEach(id => _rootEl.appendChild(doc.__byId[id]));
const pendingDomReady = [];
doc.addEventListener = (type, fn) => { if (type === 'DOMContentLoaded') pendingDomReady.push(fn); };
const winListeners = {};

// 后端契约桩：POST /api/schools 默认成功，第 6 步翻成 409 做反向验证
let postSchoolsCalls = [];
// 词库/区划的服务端真相：面板 POST 成功后必须重新拉到新校（第 5 步前才追加增量）
let dictRows = [
    { id: 'sch_h3z_0002', name: '哈三中', full_name: '哈尔滨市第三中学校', short_name: '哈三中', province: '黑龙江省', city: '哈尔滨市' },
];
const extraDictRow = { id: 'sch_new_0001', name: '新桥中学', full_name: '呼和浩特市新桥中学',
    short_name: '新桥中学', province: '内蒙古自治区', city: '呼和浩特市', tier: '市重', nature: '公办' };
let regionRows = [
    { province: '黑龙江省', cities: ['哈尔滨市','大庆市'] },
    { province: '内蒙古自治区', cities: ['呼和浩特市','包头市'] },
];
let nextPostResult = { ok: true, status: 200, body: { ok: true, id: 'sch_new_0001',
    school: { id: 'sch_new_0001', name: '新桥中学', full_name: '呼和浩特市新桥中学',
              short_name: '新桥中学', province: '内蒙古自治区', city: '呼和浩特市',
              tier: '市重', nature: '公办', source: '手动录入' } } };
const sandbox = {
    document: doc,
    window: {
        LOCAL_TOKEN: 'test-token',
        MathBankModal: { open: (m, o) => { calls.push(['open', !!m, !!(o && o.initialFocus)]); m.attrs['aria-hidden'] = 'false';
                         if (o && o.initialFocus && typeof o.initialFocus.focus === 'function') o.initialFocus.focus(); },
                         close: (m) => { calls.push(['close']); m.attrs['aria-hidden'] = 'true'; } },
        addEventListener: (type, fn) => { (winListeners[type] = winListeners[type] || []).push(fn); },
        removeEventListener: () => {},
        showToast: (t) => { calls.push(['toast', t]); },
        loadSchoolDict: () => { calls.push(['reload']); return Promise.resolve(); },
        fillProvinceSelect: () => { calls.push(['fillProvince']); },
        _schoolsDict: [], _regionsTree: [], _displayConfig: null,
    },
    navigator: {},
    setTimeout: (fn) => { sandbox.__lastTimer = fn; return 1; },
    clearTimeout: () => {},
    Promise, Event: class Event { constructor(t) { this.type = t; } },
    console, JSON, Array, Object, String, Number, Math, Error, Boolean, RegExp, isNaN, parseInt, parseFloat,
};
sandbox.window.fetch = async (url, opts) => {
    const method = (opts && opts.method) || 'GET';
    calls.push([method, String(url), opts && opts.body]);
    if (String(url).startsWith('/api/display-order') && method === 'GET') {
        return { ok: true, status: 200, json: async () => ({ status: 'success',
            provinces: { _: ['黑龙江省'] }, cities: {'黑龙江省': ['哈尔滨市']}, schools: {},
            counts: { provinces: {}, cities: {}, schools: {} } }) };
    }
    if (String(url) === '/api/schools' && method === 'POST') {
        postSchoolsCalls.push(JSON.parse(opts.body));
        const r = nextPostResult;
        if (r.ok) dictRows = dictRows.concat([extraDictRow]);   // 入库成功 → 词库真相多一行
        return { ok: r.ok, status: r.status, json: async () => r.body };
    }
    // 词库/区划的兜底重取：宿主 loadSchoolDict 不重新发请求时，面板必须靠这两个接口拿到新校
    if (String(url).startsWith('/api/school-suggestions')) {
        return { ok: true, status: 200, json: async () => ({ suggestions: dictRows.slice() }) };
    }
    if (String(url).startsWith('/api/school-regions')) {
        return { ok: true, status: 200, json: async () => ({ regions: regionRows.slice() }) };
    }
    return { ok: true, status: 200, json: async () => ({ status: 'success', provinces: [] }) };
};
sandbox.fetch = sandbox.window.fetch;
// 浏览器里 window === globalThis，`window._schoolsDict = x` 与裸 `_schoolsDict` 是同一块存储。
// 沙箱必须给同一个对象，否则面板写 window、测试读另一份，断言永远对不上。
sandbox.globalThis = sandbox.window;

const script = %MODAL_STUB% + panelSource + '\n;globalThis.__panelOpen = window.openDisplayOrderPanel;';
vm.createContext(sandbox);
vm.runInContext(script, sandbox);
sandbox.window.openDisplayOrderPanel = sandbox.window.__panelOpen;
pendingDomReady.forEach(fn => fn());

function deepFind(node, pred) {
    for (const c of node.children) { if (pred(c)) return c; const d = deepFind(c, pred); if (d) return d; }
    return null;
}
function field(id) { return deepFind(doc.body, c => c.id === id); }
async function flush(n) { for (let i = 0; i < (n || 8); i++) await new Promise(r => process.nextTick(r)); }

async function main() {
    doc.__byId.importSrcProvince.value = '黑龙江省';
    sandbox.window._regionsTree = [
        { province: '黑龙江省', cities: ['哈尔滨市','大庆市'] },
        { province: '内蒙古自治区', cities: ['呼和浩特市','包头市'] },
    ];
    sandbox.window._schoolsDict = [
        { id: 'sch_h3z_0002', name: '哈三中', full_name: '哈尔滨市第三中学校', short_name: '哈三中', province: '黑龙江省', city: '哈尔滨市' },
    ];
    await sandbox.window.openDisplayOrderPanel();

    // —— 1. 按钮存在；省档下不可用（省市是派生维度，不该手建）
    const addBtn = doc.__byId.displayOrderTabs.children.find(c => String(c.textContent) === '+ 新增学校');
    if (!addBtn) throw new Error('「+ 新增学校」按钮未出现在 tab 栏');
    if (addBtn.getAttribute('aria-disabled') !== 'true') throw new Error('省档下按钮应 aria-disabled');
    if (!(addBtn.listeners['click'] || []).length) throw new Error('按钮没有绑定 click');
    (addBtn.listeners['click'] || []).forEach(fn => fn({}));
    if (deepFind(doc.body, c => c.id === 'addSchoolModal')) throw new Error('省档点击却开了新增弹窗');

    const tabs = doc.__byId.displayOrderTabs.children;
    (tabs[2].listeners['click'] || []).forEach(fn => fn({}));   // 切到「校」
    const addBtn2 = doc.__byId.displayOrderTabs.children.find(c => String(c.textContent) === '+ 新增学校');
    if (addBtn2.getAttribute('aria-disabled') !== null) throw new Error('校档按钮仍带 aria-disabled');

    // —— 2. 表单 createElement 构建：六控件齐备 + 默认值 + 选项口径
    (addBtn2.listeners['click'] || []).forEach(fn => fn({}));
    const modal = deepFind(doc.body, c => c.id === 'addSchoolModal');
    if (!modal) throw new Error('点击后未创建新增学校弹窗');
    ['addSchoolProvince','addSchoolCity','addSchoolFullName','addSchoolShortName','addSchoolTier','addSchoolNature']
        .forEach(id => { if (!field(id)) throw new Error('表单缺控件 ' + id); });
    if (field('addSchoolProvince').tagName !== 'SELECT') throw new Error('省份必须是下拉');
    if (field('addSchoolCity').tagName !== 'SELECT') throw new Error('城市必须是下拉');
    if (field('addSchoolFullName').tagName !== 'INPUT') throw new Error('全称必须是输入框');
    if (field('addSchoolProvince').value !== '黑龙江省') throw new Error('省份默认未跟随当前省: ' + field('addSchoolProvince').value);
    if (field('addSchoolNature').value !== '公办') throw new Error('性质默认应为公办');
    if (field('addSchoolTier').value !== '') throw new Error('档次默认应留空');
    const tierVals = field('addSchoolTier').children.map(o => o.value);
    if (tierVals.join(',') !== ',国重,省重,市重,普高') throw new Error('档次选项不对: ' + tierVals);
    const natureVals = field('addSchoolNature').children.map(o => o.value);
    if (natureVals.join(',') !== ',公办,民办') throw new Error('性质选项不对: ' + natureVals);
    const submitBtn = field('addSchoolSubmitBtn');
    if (!submitBtn || !(submitBtn.listeners['click'] || []).length) throw new Error('保存按钮未 addEventListener');
    if (submitBtn.getAttribute('onclick')) throw new Error('保存按钮用了内联 onclick');
    if (modal._innerHTML) throw new Error('弹窗外壳用了 innerHTML 构建');

    // —— 3. 必填在前端就拦下，不发请求
    const before = postSchoolsCalls.length;
    (submitBtn.listeners['click'] || []).forEach(fn => fn({}));
    await flush();
    if (postSchoolsCalls.length !== before) throw new Error('空表单仍然发出了 POST');
    const errEl = field('addSchoolError');
    if (!errEl || errEl.classList.contains('hidden')) throw new Error('必填缺失却没有显示错误');
    if (errEl.textContent.indexOf('请填写') < 0) throw new Error('错误文案不含必填提示: ' + errEl.textContent);

    // —— 4. 填全后提交：六字段载荷，tier/nature 按用户所选
    const setVal = (id, v) => { const el = field(id); el.value = v; el.dispatchEvent({ type: 'input' }); el.dispatchEvent({ type: 'change' }); };
    setVal('addSchoolProvince', '内蒙古自治区');
    setVal('addSchoolCity', '呼和浩特市');
    setVal('addSchoolFullName', '呼和浩特市新桥中学');
    setVal('addSchoolShortName', '新桥中学');
    setVal('addSchoolTier', '市重');
    setVal('addSchoolNature', '公办');
    (field('addSchoolSubmitBtn').listeners['click'] || []).forEach(fn => fn({}));
    await flush(12);
    if (postSchoolsCalls.length !== before + 1) throw new Error('未提交 POST /api/schools');
    const body = postSchoolsCalls[postSchoolsCalls.length - 1];
    if (body.full_name !== '呼和浩特市新桥中学' || body.short_name !== '新桥中学') throw new Error('名称载荷错误: ' + JSON.stringify(body));
    if (body.province !== '内蒙古自治区' || body.city !== '呼和浩特市') throw new Error('省市载荷错误');
    if (body.tier !== '市重' || body.nature !== '公办') throw new Error('tier/nature 未随载荷: ' + JSON.stringify(body));

    // —— 5. 成功后关弹窗 + 刷新词库/下拉/列表，新校出现在面板
    if (!calls.filter(c => c[0] === 'close').length) throw new Error('成功后未关闭弹窗');
    if (!calls.some(c => c[0] === 'reload')) throw new Error('成功后未重新拉取词库');
    if (!calls.some(c => c[0] === 'fillProvince')) throw new Error('成功后未刷新录题表单下拉');
    const names = doc.__byId.displayOrderList.children.map(r => r.dataset.name);
    if (names.indexOf('新桥中学') < 0) throw new Error('面板列表未出现新学校: ' + names);
    if (doc.__byId.displayOrderStatus.textContent.indexOf('已新增') < 0) throw new Error('状态栏未反馈新增结果');

    // —— 6. 反向验证：后端拒绝时不关窗、错误可见、输入保留
    const closeCount = calls.filter(c => c[0] === 'close').length;
    nextPostResult = { ok: false, status: 409, body: { ok: false, error: '「某某中学」已使用简称「新桥中学」，请换一个' } };
    (addBtn2.listeners['click'] || []).forEach(fn => fn({}));
    if (!deepFind(doc.body, c => c.id === 'addSchoolModal')) throw new Error('第二次未能打开弹窗');
    // 重开是空白表单（真实行为），必须重新填一遍才能走到后端拒绝那条分支
    setVal('addSchoolProvince', '内蒙古自治区');
    setVal('addSchoolCity', '呼和浩特市');
    setVal('addSchoolFullName', '呼和浩特市新桥中学');
    setVal('addSchoolShortName', '新桥中学');
    (field('addSchoolSubmitBtn').listeners['click'] || []).forEach(fn => fn({}));
    await flush(12);
    if (calls.filter(c => c[0] === 'close').length !== closeCount) throw new Error('新增失败却关闭了弹窗');
    const err2 = field('addSchoolError');
    if (err2.classList.contains('hidden')) throw new Error('后端 409 未显示给用户');
    if (err2.textContent.indexOf('已使用简称') < 0) throw new Error('错误文案丢失后端原因: ' + err2.textContent);
    if (field('addSchoolFullName').value !== '呼和浩特市新桥中学') throw new Error('失败后用户输入被清空');
    console.log('ADD SCHOOL OK');
}
main().catch(e => { console.error('FAIL:', e.stack || e.message); process.exit(1); });
"""


def _run_node(harness: str, marker: str) -> None:
    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(harness)
        path = f.name
    result = subprocess.run([node, path], capture_output=True, text=True, encoding="utf-8", timeout=60)
    Path(path).unlink(missing_ok=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker in result.stdout


def test_add_school_modal_builds_posts_and_keeps_errors_in_real_js():
    """新增学校入口的可执行回归（含「失败不关窗」反向验证）。"""
    stub_start = HARNESS.index("class El {")
    stub_end = HARNESS.index("const calls = [];")
    dom_stub = HARNESS[stub_start:stub_end]
    harness = ADD_SCHOOL_HARNESS.replace("%PANEL%", repr(_extract_panel_source())).replace("__DOM_STUB__", dom_stub).replace("%MODAL_STUB%", as_js_literal())
    _run_node(harness, "ADD SCHOOL OK")
