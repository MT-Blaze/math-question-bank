"""「已转好的图仍显示转 TikZ」的回归锁。

用户反馈：用「新增 TikZ 绘图」工作台画完图、![TikZ 几何图](/static/uploads/tikz_xxx.png)
已经进到题干里，预览里那张图下面却还挂着一个「转 TikZ」按钮。

查证结论：decoratePreviewIllustrationButtons 的 skipPaths **只认内存资产表**
（TikzState.contentAssets / answerAssets 的 image_path + reference_image_path）。
TikzState 是纯内存态：老数据刷新后 hydrate 到空的 content_tikz_assets、或资产记录因为
迁移/导入丢了，图本身还是那张转好的 TikZ，但资产表里查不到 → 按钮又回来了。

修法：加一条不依赖内存态的物理判据——文件名以 tikz_ 开头即为 TikZ 渲染结果
（后端 paper_helper 的 img_filename.startswith("tikz_") 与前端
describeQuestionImageSource 的 /^tikz_/i 是同一条口径），并让插图列表沿用。

这里用最小 DOM 桩真跑一遍抽取出来的前端函数，不用文本断言糊过去。

⚠️ 抽取出来的片段一律走临时文件喂给 node，不拼进 JS 字符串字面量：
static/js 里 api.js/ocr.js 是 CRLF、editor.js 是 LF，CR 进单引号字符串会直接
SyntaxError，而且片段源码本身也带引号和反斜杠。
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_JS_DIR = PROJECT_ROOT / "static" / "js"

TIKZ_IMAGE = "/static/uploads/tikz_abc123.png"
BITMAP_IMAGE = "/static/uploads/pdf_crop_abc_003_01.png"


def _read(name: str) -> str:
    return (STATIC_JS_DIR / name).read_text(encoding="utf-8")


def _extract(source: str, start_marker: str, end_marker: str) -> str:
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def _run_frontend_case(snippets, body: str) -> subprocess.CompletedProcess:
    """把抽取片段与用例体各写一份临时 .js，交给 node 真跑。

    snippets: {变量名: 源码}，变量名即 node 侧的同名常量。
    """

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend regression"
    workdir = tempfile.mkdtemp(prefix="tikz-button-")
    try:
        read_lines = []
        for var_name, code in snippets.items():
            path = Path(workdir) / (var_name + ".snippet.js")
            path.write_text(code, encoding="utf-8")
            read_lines.append(
                "const %s = fs.readFileSync(%r, 'utf8');" % (var_name, str(path))
            )
        script_path = Path(workdir) / "run.js"
        # newline='' 关掉 Windows 的 \n -> \r\n 翻译：脚本与用例体必须逐字节一致，
        # 否则 node 侧看到的换行和 Python 里写的不是一个东西（也避免调试时被误导）。
        with open(script_path, "w", encoding="utf-8", newline="") as handle:
            handle.write(
            "\n".join(
                [
                    "const fs = require('fs');",
                    *_DOM_STUB_LINES,
                    "const win = {",
                    "  location: { href: 'http://127.0.0.1:27182/',",
                    "    origin: 'http://127.0.0.1:27182' },",
                    "  MathBankSafe: { safeImageUrl },",
                    "  autoTikzFromIllustration: function () {},",
                    "  renderContentTikzAssets: function () {},",
                    "  TikzState: {",
                    "    contentAssets: [], answerAssets: [],",
                    "    referencePaths() { return []; }",
                    "  }",
                    "};",
                    "global.window = win;",
                    "global.document = { createElement: tag => new El(tag) };",
                    # renderIllustrationBadges 直接闭包引用 uploadedImages；
                    # 用例体只往这个数组里填，不再重复声明同名标识符。
                    "var uploadedImages = [];",
                    *read_lines,
                    "",
                    "new Function('window', __isTikzRenderImage",
                    "  + '\\nwindow.isTikzRenderImage = isTikzRenderImage;')(win);",
                    "const decorate = new Function('window', 'document', __decorator",
                    "  + '\\nreturn decoratePreviewIllustrationButtons;')",
                    "  (win, global.document);",
                    "const renderIllustrationBadges = new Function(",
                    "  'window', 'document', 'renderContentTikzAssets', 'TikzState',",
                    "  'uploadedImages',",
                    "  __badge + '\\nreturn renderIllustrationBadges;')",
                    "  (win, global.document, function () {}, win.TikzState,",
                    "   uploadedImages);",
                    "",
                    # 用例体必须**真正拼进脚本**：只写成 "__body" 会被当成表达式
                    # 语句求值一个字符串，整段用例静默不执行、只会拿到空 stdout。
                    body,
                ]
            )
        )
        return subprocess.run(
            [node, str(script_path)],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    finally:
        try:
            shutil.rmtree(workdir, ignore_errors=True)
        except OSError:
            pass


# --------------------------------------------------------------------------- DOM 桩

_DOM_STUB_LINES = [
    r"""
class El {
  constructor(tag) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.children = [];
    this.attrs = {};
    this.parent = null;
    this.innerHTML = '';
  }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  setAttribute(k, v) { this.attrs[k] = v; }
  // 装饰器的去重守卫读的是 hasAttribute；桩此前只有 attrs，没这个接口。
  hasAttribute(k) { return k in this.attrs; }
  addEventListener() {}
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  insertBefore(c, ref) {
    c.parent = this;
    const i = this.children.indexOf(ref);
    if (i >= 0) this.children.splice(i, 0, c); else this.children.push(c);
    return c;
  }
  get parentNode() { return this.parent; }
  // 真实 DOM 的 img.parentElement 是父节点本身；桩只有 parent，
  // 少了这个别名，装饰器新写的「按父节点去重」守卫在 node 侧永远读不到。
  get parentElement() { return this.parent; }
  closest(sel) {
    let n = this;
    while (n) {
      if (sel.indexOf('[') === 0 && sel.slice(1, -1) in n.attrs) return n;
      n = n.parent;
    }
    return null;
  }
  all() {
    const out = [];
    const walk = n => n.children.forEach(c => { out.push(c); walk(c); });
    walk(this);
    return out;
  }
  querySelectorAll(sel) {
    return sel === 'img' ? this.all().filter(n => n.tagName === 'IMG') : [];
  }
  // 角标渲染点有一半是 innerHTML 拼出来的字符串（不是真实子元素），
  // 所以自检必须把节点自身的 innerHTML 也纳入，否则会「什么都没渲染」 yet 判定通过。
  hasText(text) {
    const pool = [String(this.innerHTML || '')].concat(
      this.all().map(n => String(n.innerHTML || ''))
    );
    return pool.some(chunk => chunk.indexOf(text) >= 0);
  }
}

// api.js 的 safeImageUrl（/static/uploads/ 白名单 + 扩展名 + 同站校验）
function safeImageUrl(value) {
  const asString = v => (v === null || v === undefined ? '' : String(v));
  const raw = asString(value).trim();
  if (!raw || raw.indexOf('\\') >= 0) return '';
  try {
    const url = new URL(raw, 'http://127.0.0.1:27182/');
    if (url.origin !== 'http://127.0.0.1:27182') return '';
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return '';
    const decodedPath = decodeURIComponent(url.pathname);
    const allowedPrefix = decodedPath.startsWith('/static/uploads/')
      || decodedPath.startsWith('/static/test_uploads/');
    if (!allowedPrefix || decodedPath.split('/').includes('..')) return '';
    if (!/\.(?:png|jpe?g|gif|webp)$/i.test(decodedPath)) return '';
    return url.pathname;
  } catch (e) { return ''; }
}
""".strip()
]


def _snippets() -> dict:
    api_source = _read("api.js")
    editor_source = _read("editor.js")
    ocr_source = _read("ocr.js")
    return {
        "__isTikzRenderImage": _extract(
            api_source,
            "function isTikzRenderImage(",
            "window.isTikzRenderImage = isTikzRenderImage;",
        ),
        "__decorator": _extract(
            editor_source,
            "function decoratePreviewIllustrationButtons(",
            "window.decoratePreviewIllustrationButtons = decoratePreviewIllustrationButtons;",
        ),
        "__badge": _extract(
            ocr_source,
            "function renderIllustrationBadges() {",
            "window.autoTikzFromIllustration = function(referencePath) {",
        ),
    }


# --------------------------------------------------------------------------- 用例体

_DECORATOR_CASES = """
const cases = [
  // [名称, 正文里的图, TikzState.contentAssets, 是否允许出现「转 TikZ」按钮]
  ['工作台刚插入（资产齐全）', ['TIKZ'], [
    { id: 'tikz_1', image_path: 'TIKZ', tikz_code: '\\\\begin{tikzpicture}...' }], false],
  ['重开题目（资产齐全）', ['TIKZ'], [
    { id: 'tikz_1', image_path: 'TIKZ', tikz_code: '\\\\begin{tikzpicture}...' }], false],
  ['老数据：assets 为空且 tikz_code 为空', ['TIKZ'], [], false],
  ['资产表里完全没有这条记录', ['TIKZ'], [
    { id: 'tikz_9', image_path: '/static/uploads/tikz_other.png', tikz_code: 'x' }], false],
  ['同一张图带查询串', ['TIKZ?t=123'], [], false],
  ['未转的原题位图仍要入口', ['BITMAP'], [], true],
  ['位图已登记为参考图时不给入口', ['BITMAP'], [
    { id: 'tikz_2', image_path: '/static/uploads/tikz_z.png', tikz_code: 'x',
      reference_image_path: 'BITMAP' }], false]
];
let failures = [];
cases.forEach(function (row) {
  const name = row[0];
  win.TikzState.contentAssets = row[2] || [];
  win.TikzState.answerAssets = [];
  const container = new El('div');
  row[1].forEach(function (path) {
    const img = new El('img');
    img.setAttribute('src', safeImageUrl(path));
    container.appendChild(img);
  });
  decorate(container);
  const shown = container.hasText('转 TikZ');
  if (shown !== row[3]) {
    failures.push(name + '（按钮=' + shown + '，期望=' + row[3] + '）');
  }
  console.log((shown === row[3] ? '  ok  ' : '  FAIL') + ' ' + name);
});
if (failures.length) {
  console.log('FAILED:\\n - ' + failures.join('\\n - '));
  process.exit(1);
}
console.log('ALL_DECORATOR_CASES_PASSED');
""".replace("TIKZ", TIKZ_IMAGE).replace("BITMAP", BITMAP_IMAGE)


def test_already_tikZ_image_must_not_offer_conversion_entry():
    """已转好的 TikZ 渲染结果（tikz_*.png）不得再挂「转 TikZ」按钮。

    资产记录丢失时这条最要命：图确实已经是 TikZ，按钮却还在，用户会以为没转成。
    """

    result = _run_frontend_case(_snippets(), _DECORATOR_CASES)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ALL_DECORATOR_CASES_PASSED" in result.stdout


def test_unconverted_bitmap_keeps_its_conversion_entry():
    """反向锁：普通位图必须仍然给出「转 TikZ」入口。

    否则「不看内存资产表」这条新判据会被写歪成「所有图都不给按钮」。
    """

    body = (
        "const container = new El('div');\n"
        "const img = new El('img');\n"
        "img.setAttribute('src', safeImageUrl('" + BITMAP_IMAGE + "'));\n"
        "container.appendChild(img);\n"
        "decorate(container);\n"
        "if (!container.hasText('转 TikZ')) {\n"
        "  console.log('MISSING_ENTRY'); process.exit(1);\n"
        "}\n"
        "console.log('BITMAP_ENTRY_OK');\n"
    )
    result = _run_frontend_case(_snippets(), body)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "BITMAP_ENTRY_OK" in result.stdout


def test_illustration_list_does_not_offer_tikZ_for_a_tikZ_render():
    """插图列表里，TikZ 渲染结果不再单独占一条可「转 TikZ」的行。

    applyTikzWorkbench 会把渲染结果 push 进 uploadedImages 并刷新插图列表；
    原先它会被当成待转位图再列一条，点进去等于拿转好的图重转一次。
    """

    body = (
        # 复用脚本作用域里的 uploadedImages（模板声明的是 var，这里只填充不重复声明）
        "uploadedImages.length = 0;\n"
        "uploadedImages.push('" + TIKZ_IMAGE + "');\n"
        "uploadedImages.push('" + BITMAP_IMAGE + "');\n"
        "const list = new El('div');\n"
        "list.setAttribute('id', 'illustrationsList');\n"
        "global.document.getElementById = function (id) {\n"
        "  return id === 'illustrationsList' ? list : null;\n"
        "};\n"
        "renderIllustrationBadges();\n"
        "if (list.hasText('" + TIKZ_IMAGE.split('/')[-1] + "')) {\n"
        "  console.log('TIKZ_STILL_LISTED'); process.exit(1);\n"
        "}\n"
        "if (!list.hasText('" + BITMAP_IMAGE.split('/')[-1] + "')) {\n"
        "  console.log('BITMAP_LOST'); process.exit(1);\n"
        "}\n"
        "console.log('BADGE_OK');\n"
    )
    result = _run_frontend_case(_snippets(), body)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "BADGE_OK" in result.stdout


# --------------------------------------------------------------------------- 增量重渲染
#
# 上面三条钉的是「该不该出按钮」；这一条钉「同一容器重复渲染会不会叠按钮」。
# decoratePreviewIllustrationButtons 里那道去重守卫原先写的是
# `img.closest('[data-preview-illustration]')`，但 wrapper 是 img 的**父节点**，
# closest() 从 img 自身往上找祖先，永远命中不了自己刚包上的那层 —— 守卫恒不生效。
# 现在页面每次预览都整块重写 innerHTML（editor.js renderQuestionPreviewContent），
# 所以线上看不见；一旦改成增量渲染就会每张图叠一个按钮。

_DOUBLE_DECORATE_BODY = """
const container = new El('div');
// 桩的 insertBefore 需要 ref 已在 children 里才插得进去；appendChild 会设 parent，
// 但真实 DOM 里 img 一进容器就有父节点。这里显式建父子关系，模拟 innerHTML 注入。
const mkImg = function (p) {
  const img = new El('img');
  img.setAttribute('src', safeImageUrl(p));
  container.appendChild(img);
  img.parent = container;
  return img;
};
const target = mkImg('BITMAP_PATH');
decorate(container);
const afterFirst = container.querySelectorAll('button').length;
// 第二次调用模拟「状态刷新后对同一容器重新装饰」：innerHTML 未清空。
decorate(container);
const afterSecond = container.querySelectorAll('button').length;
console.log('FIRST=' + afterFirst + ' SECOND=' + afterSecond
  + ' PARENT_TAG=' + (target.parentElement ? target.parentElement.tagName : 'null'));
if (afterFirst !== 1) { console.log('EXPECTED_ONE_AFTER_FIRST'); process.exit(1); }
if (afterSecond !== 1) { console.log('BUTTON_STACKED'); process.exit(1); }
console.log('NO_DOUBLE_BUTTON');
""".replace("BITMAP_PATH", BITMAP_IMAGE)


def test_redecorating_the_same_container_does_not_stack_buttons():
    """同一容器二次装饰不得叠加「转 TikZ」按钮（钉住去重守卫真的在挡）。"""

    snippets = _snippets()
    # 桩里的 El 只有 closest()，没有 hasAttribute / parentElement.querySelector，
    # 补上真实 DOM 才有的能力，否则测的是桩的行为而不是浏览器的。
    decorated = snippets["__decorator"]
    assert "hasAttribute('data-preview-illustration')" in decorated, (
        "去重守卫必须按父节点判定（closest 找不到自己包上的 wrapper）"
    )

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend regression"
    workdir = tempfile.mkdtemp(prefix="tikz-double-")
    try:
        paths = {}
        for var_name, code in snippets.items():
            path = Path(workdir) / (var_name + ".snippet.js")
            path.write_text(code, encoding="utf-8")
            paths[var_name] = str(path)
        with open(Path(workdir) / "run.js", "w", encoding="utf-8", newline="") as handle:
            handle.write("\n".join([
                "const fs = require('fs');",
                *_DOM_STUB_LINES,
                # querySelectorAll('button') 需要能按标签名筛，桩默认只认 'img'。
                "El.prototype.querySelectorAll = function (sel) {",
                "  if (sel === 'img') return this.all().filter(n => n.tagName === 'IMG');",
                "  if (sel === 'button') return this.all().filter(n => n.tagName === 'BUTTON');",
                "  return [];",
                "};",
                "const win = {",
                "  location: { href: 'http://127.0.0.1:27182/',",
                "    origin: 'http://127.0.0.1:27182' },",
                "  MathBankSafe: { safeImageUrl },",
                "  autoTikzFromIllustration: function () {},",
                "  renderContentTikzAssets: function () {},",
                "  TikzState: { contentAssets: [], answerAssets: [],",
                "    referencePaths() { return []; } }",
                "};",
                "global.window = win;",
                "global.document = { createElement: tag => new El(tag) };",
                "const __isTikzRenderImage = fs.readFileSync(%r, 'utf8');" % paths["__isTikzRenderImage"],
                "new Function('window', __isTikzRenderImage"
                "  + '\\nwindow.isTikzRenderImage = isTikzRenderImage;')(win);",
                "const __decorator = fs.readFileSync(%r, 'utf8');" % paths["__decorator"],
                "const decorate = new Function('window', 'document', __decorator"
                "  + '\\nreturn decoratePreviewIllustrationButtons;')",
                "  (win, global.document);",
                _DOUBLE_DECORATE_BODY,
            ]))
        result = subprocess.run(
            [node, str(Path(workdir) / "run.js")],
            cwd=PROJECT_ROOT, text=True, encoding="utf-8",
            errors="replace", capture_output=True, check=False,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "NO_DOUBLE_BUTTON" in result.stdout, result.stdout + result.stderr

