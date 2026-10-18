# -*- coding: utf-8 -*-
r"""导出前 LaTeX 预检/自愈层（待办 #8 P0）回归测试。

题库里有一类「入库时就坏了」的公式：导入链路把 LaTeX 当转义串解过一遍，``\\`` 掉成
单个 ``\``、``\nRightarrow`` 掉成换行；另有 ``\left``/``\right`` 被中文打断、宏名紧贴
汉字被 TeX 吞掉。生产编译带 ``-halt-on-error``，于是**一道坏题炸掉整卷**（与 2026-10-18
缺图事故同一条路径，缺图那张网是 ``patch_missing_latex_images``，这里是公式侧同语义网）。

钉四件事：
1. 五条规则各自的行为与文案；
2. 幂等（路由层与编译入口各跑一次，第二次必须空操作）+ 健康源码零误伤；
3. 端到端：脏题进库后导出必须真出 PDF（并附「不修就一定炸」的对照组）；
4. 前端链路：``X-MathBank-Export-Notes`` 头名两端逐字一致，且四个导出入口都消费它。

正文一律用字面量，绝不读 ``*.db``——生产题库被 .gitignore，读它的测试在干净
worktree 里必挂（见 commit 9a57269 同一教训）。
"""

import json
import shutil
import subprocess
import tempfile
import urllib.parse
from pathlib import Path

import pytest

from main import LOCAL_TOKEN
from mathbank.latex_preflight import repair_latex_before_compile

HDRS = {"X-Local-Token": LOCAL_TOKEN}
PROJECT_ROOT = Path(__file__).resolve().parents[1]
PAPER_JS = PROJECT_ROOT / "static" / "js" / "paper.js"
ROUTES_PAPER = PROJECT_ROOT / "mathbank" / "routes_paper.py"

NOTES_HEADER = "X-MathBank-Export-Notes"
NOTE_COUNT_HEADER = "X-MathBank-Export-Note-Count"

# ---------------------------------------------------------------------------
# 五条规则的真实形态（逐字取自生产题库，题号见注释）
# ---------------------------------------------------------------------------

# id=30：\left 与 \right 被中文「或」劈成两个行内公式，TeX 立刻 Undefined control sequence
SPLIT_DELIMITER = (
    r"已知集合 $A=\left\{x\mid x<1$ 或 $x>2\right\}$ ，求 $\complement_R A$。"
)
# id=189 / id=224：xeCJK 把汉字当字母，\fillin 与汉字粘连后被读成不存在的宏
MACRO_EATEN_BY_CJK = (
    r"已知集合 $A=\left\{x\mid x<1\right\}$ ，则 $A\oplus B$ 中有\fillin个元素."
)
MACRO_EATEN_IN_ANSWER = r"则 $p$ 是 $q$ 的\fillin条件。（在充分不必要、必要不充分、充要中选填）"
# id=32 / id=285：cases 里的行分隔 \\ 入库时掉了一个反斜杠，\x 成了未定义宏
LOST_ROWBREAK_CASES = (
    r"【详解】（1）$a=0$ 时，$f(x)=x^{2}+(x-1)|x|+3=\begin{cases}"
    r"2x^{2}-x+3,x\geq0\x+3,x<0\end{cases}$"
)
LOST_ROWBREAK_PAREN = (
    r"故 \(M(x)=\begin{cases}(x+1)^2,&x\le -1\text{ 或 }x\ge 0,"
    r"\x+1,&-1<x<0.\end{cases}\)"
)
# id=105：库里存的是 CRLF，写盘时默认换行翻译再把 \n 变成 \r\n，行尾翻倍成空行，
# 行内公式跨段 → Missing $ inserted。这里用「数学段内出现 CR」这一指纹复现。
CRLF_DOUBLED = "若 $m \\leq -5\r\n\r\n\\Rightarrow m < 0$ 成立，则结论得证。"
# 行内公式跨段落（R4）
CROSS_PARAGRAPH = "因为 $a=1\n\n所以结论成立。"

ALL_DIRTY = (
    SPLIT_DELIMITER,
    MACRO_EATEN_BY_CJK,
    MACRO_EATEN_IN_ANSWER,
    LOST_ROWBREAK_CASES,
    LOST_ROWBREAK_PAREN,
    CRLF_DOUBLED,
    CROSS_PARAGRAPH,
)


def _notes_text(notes):
    return "；".join(notes)


# ---------------------------------------------------------------------------
# 1. 五条规则各自的行为
# ---------------------------------------------------------------------------

def test_r0_normalizes_crlf_and_reports_math_internal_cr():
    fixed, notes = repair_latex_before_compile(CRLF_DOUBLED)

    assert "\r" not in fixed, "换行必须归一成 LF，否则写盘时行尾会翻倍成空行"
    assert notes and "裸回车" in _notes_text(notes), notes


def test_r0_is_silent_for_plain_lf_source():
    fixed, notes = repair_latex_before_compile("已知 $a=1$，求 $b$。")
    assert fixed == "已知 $a=1$，求 $b$。"
    assert notes == [], "纯 LF 的健康源码不许产生 Toast，否则每次导出都吵一遍"


def test_r1_rejoins_delimiters_split_by_cjk():
    fixed, notes = repair_latex_before_compile(SPLIT_DELIMITER)

    assert "$A=\\left\\{x\\mid x<1\\text{或}x>2\\right\\}$" in fixed, fixed
    assert r"\text{或}" in fixed, "中文要包进 \\text{}，不能裸放在数学模式里"
    assert "定界符" in _notes_text(notes), notes


def test_r2_breaks_macro_names_off_cjk():
    for dirty in (MACRO_EATEN_BY_CJK, MACRO_EATEN_IN_ANSWER):
        fixed, notes = repair_latex_before_compile(dirty)

        assert r"\fillin{}" in fixed, fixed
        assert "宏名紧贴汉字" in _notes_text(notes), notes


def test_r3_restores_lost_row_breaks_inside_row_envs():
    for dirty in (LOST_ROWBREAK_CASES, LOST_ROWBREAK_PAREN):
        fixed, _notes = repair_latex_before_compile(dirty)

        assert r"\x" not in fixed, fixed
        assert "\\\\ x" in fixed, fixed


def test_r4_joins_cross_paragraph_inline_math():
    fixed, notes = repair_latex_before_compile(CROSS_PARAGRAPH)

    assert "因为" in fixed and "所以结论成立" in fixed
    assert "\n\n所以结论成立" not in fixed, fixed
    assert "跨段落" in _notes_text(notes), notes



def test_r5_degrades_unclosed_inline_dollar():
    r"""少一个收尾 $ 的题不许再炸整卷：降级成卷面可见的字面 $，并如实上报。"""

    fixed, notes = repair_latex_before_compile(r"因为 $a=1 所以结论成立。")

    assert fixed.count(r"\$") == 1, fixed
    assert "缺少收尾的 $" in _notes_text(notes), notes
    again, second_notes = repair_latex_before_compile(fixed)
    assert again == fixed and second_notes == [], "降级必须一次到位，否则同一道题被报两次"


def test_r5_degrades_every_dollar_in_an_unclosed_run():
    r"""入库链把未闭合公式翻倍成 ``$$a=1$``：只转开界符会留下新的孤立 $。

    那种半成品第二次调用还得再修一遍，既不幂等，也让同一问题重复出现在 Toast 里。
    """

    fixed, _notes = repair_latex_before_compile(r"因为 $$a=1$ 成立。")

    assert fixed.count("$") == fixed.count(r"\$"), fixed
    again, second_notes = repair_latex_before_compile(fixed)
    assert again == fixed and second_notes == [], second_notes


def test_r5_degrades_unclosed_display_math():
    fixed, notes = repair_latex_before_compile("$$x=1" + "\n\n" + "故得解。")

    assert fixed.startswith(r"\$\$x=1"), fixed
    assert "缺少收尾的 $" in _notes_text(notes), notes


def test_r5_keeps_balanced_math_across_paragraphs():
    r"""每段各自闭合的行内公式跨段出现时不许动：这是健康题面的常态。"""

    source = "第一段 $a=1$ 成立。" + "\n\n" + "第二段 $b=2$ 也成立。"
    fixed, notes = repair_latex_before_compile(source)

    assert fixed == source, fixed
    assert notes == [], notes


# ---------------------------------------------------------------------------
# 2. 幂等 + 零误伤
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dirty", ALL_DIRTY, ids=range(len(ALL_DIRTY)))
def test_repair_is_idempotent(dirty):
    """路由层与 compile_tex_to_pdf 各跑一次：改写必须恒定。

    契约说清楚：改写动作是幂等的（第二次零改写），但「仍有 N 处未闭合」这类
    提示是**状态诊断**不是改写记录，第二次照样会报——只要求它不得无中生有。
    """

    once, notes_once = repair_latex_before_compile(dirty)
    twice, notes_twice = repair_latex_before_compile(once)

    assert twice == once, "第二次调用改写了文本，说明规则不自洽"
    assert set(notes_twice) <= set(notes_once), "第二次报出了第一次没有的说明：%s" % (
        [note for note in notes_twice if note not in notes_once]
    )


HEALTHY = (
    "$$x=1$$\n\n故得解。",
    r"已知 $f(x)=x^2-2x+1$，则最小值为（） \paren",
    "\\begin{choices}\n\\item $2$ 条\n\\item $4$ 条\n\\end{choices}",
    r"求 $\xi$ 的期望，$\alpha+\beta=\gamma$。",
    r"列表 $\begin{array}{cc}1 & 2\\3 & 4\end{array}$ 的和。",
    r"已知 $A=\{x\mid x<1\}$，则 $\complement_R A$ 为（）",
    r"则 $\{a_n\}$ 满足 $a_1=1$，$a_{n+1}=2a_n$。",
    r"如图 \includegraphics{a.png} 所示，$x\geq0$。",
    # 换行后紧跟缩进 / 紧跟正文，都是同一句里的正常换行，数学段本该继续：
    # 把它当成段末就会把好端端的 $ 误降级，这是预检层最不该犯的错
    "已知 $a=1\n    +b=2$ 成立。",
    "已知 $a=1\n+b=2$ 成立。",
    # 合法的多行数学环境（内部没有空行）同样一个字都不许动
    "$$\\begin{aligned}\na &= 1 \\\\\nb &= 2\n\\end{aligned}$$",
)


@pytest.mark.parametrize("clean", HEALTHY, ids=range(len(HEALTHY)))
def test_healthy_source_is_untouched(clean):
    fixed, notes = repair_latex_before_compile(clean)

    assert fixed == clean, "健康源码必须逐字节不动"
    assert notes == [], notes


def test_backslash_x_outside_row_env_is_not_touched():
    r"""\x 只在 cases/aligned/matrix 这类行式环境里才当成丢了的行分隔。"""

    source = r"设 $x$ 满足 $\xi=1$，且 $2\alpha\beta$ 成立。"
    fixed, _notes = repair_latex_before_compile(source)
    assert fixed == source


# ---------------------------------------------------------------------------
# 3. 端到端：脏题进库后真导出 PDF
# ---------------------------------------------------------------------------

def _insert_raw_question(db, content, question_type="detailed_answer", answer=""):
    """绕开入库归一化，直接落「库里已有脏数据」的状态。

    预检层要救的正是这批存量：入库链本身会把一部分脏形态原地修好，走 API 反而复现不了。
    """

    from mathbank.database import Question

    row = Question(
        content=content,
        question_type=question_type,
        answer_markdown=answer,
        category_compulsory="必修一",
        category_chapter="第一章",
        category_knowledge="测试小节",
        difficulty="L2",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row.id


def _export_pdf(client, ids):
    return client.post(
        "/api/paper/export/pdf",
        headers=HDRS,
        json={
            "title": "预检层回归卷", "subtitle": "", "paper_type": "exam",
            "target": "paper", "include_answers": True,
            "show_secret": False, "show_notice": False,
            "questions": [{"id": qid, "score": 5} for qid in ids],
        },
    )


def _read_export_tex(client, db_session, ids):
    """把导出链路真正交给编译器的 TeX 源码取回来（不编译，只拦截）。"""

    import mathbank.routes_paper as routes

    captured = {}

    def fake_compile(tex, image_paths=None):
        captured["tex"] = tex
        return b"%PDF-fake", "ok"

    original = routes._main.compile_tex_to_pdf
    routes._main.compile_tex_to_pdf = fake_compile
    try:
        _export_pdf(client, ids)
    finally:
        routes._main.compile_tex_to_pdf = original
    return captured.get("tex", "")


@pytest.mark.skipif(not shutil.which("xelatex"), reason="xelatex not installed")
def test_dirty_bank_rows_still_export_a_pdf(client, db_session):
    """七道脏题拼一卷：必须真出 PDF，且自愈说明经响应头上报。

    实测响应头固定是 4 条：裸回车那条在整卷组装时已被上游清洗掉，所以不进断言。
    """

    ids = [_insert_raw_question(db_session, dirty) for dirty in ALL_DIRTY]
    response = _export_pdf(client, ids)

    assert response.status_code == 200, response.text[:1500]
    assert response.content[:5] == b"%PDF-", "XeLaTeX 崩了就等于把残缺源码丢给编译器"

    notes = urllib.parse.unquote(response.headers.get(NOTES_HEADER) or "")
    assert notes, "改了什么必须让教师知道，否则没人会回题库修原文"
    # 条数头单独给：说明正文里本来就带「；」（样例之间用它分隔），拆不得
    assert int(response.headers.get(NOTE_COUNT_HEADER) or 0) >= 4, response.headers
    for keyword in ("定界符", "宏名紧贴汉字", "行分隔", "缺少收尾的 $"):
        assert keyword in notes, "%s 未上报：%s" % (keyword, notes)

    # 编译入口还会再跑一次预检：到编译器手里时源码必须已经干净（零改写、零新提示），
    # 否则同一道题会被 Toast 报两遍，而且说明路由层与编译层的口径没对齐。
    from mathbank.latex_preflight import repair_latex_before_compile

    _again, second_notes = repair_latex_before_compile(_read_export_tex(client, db_session, ids))
    assert second_notes == [], second_notes


@pytest.mark.skipif(not shutil.which("xelatex"), reason="xelatex not installed")
def test_without_preflight_the_same_paper_still_crashes(client, db_session, monkeypatch):
    """对照组：把预检层摘掉，同一份卷子必须编译失败。

    这条测试钉的是「因果」——只证明导出成功，可能只是 XeLaTeX 今天心情好。
    """

    import mathbank.paper_helper as paper_helper
    import mathbank.routes_paper as routes

    # 两道网都要摘：路由层那份负责上报，compile_tex_to_pdf 里那份负责兜底。
    # 只摘一层，另一层仍会把卷子救活，对照组就失去意义。
    noop = lambda tex: (tex, [])
    monkeypatch.setattr(routes, "repair_latex_before_compile", noop)
    monkeypatch.setattr(paper_helper, "repair_latex_before_compile", noop)
    ids = [_insert_raw_question(db_session, dirty) for dirty in ALL_DIRTY]
    response = _export_pdf(client, ids)

    assert response.status_code == 400, "预检层被摘掉仍出 PDF，说明它其实没在链路上"
    body = response.json()
    assert body.get("status") == "error"
    assert body.get("export_notes") == [], "摘掉预检层就不该凭空产生上报文案"


# ---------------------------------------------------------------------------
# 4. 前端链路：响应头两端逐字一致，四个入口都消费
# ---------------------------------------------------------------------------

def _extract_function(source, signature):
    """按缩进抠出整个函数体（注释里有成对引号，花括号配平法会被带偏）。"""

    lines = source.splitlines()
    for idx, line in enumerate(lines):
        if signature in line:
            indent = len(line) - len(line.lstrip())
            end = idx + 1
            while end < len(lines):
                stripped = lines[end].strip()
                if stripped in ("}", "};") and len(lines[end]) - len(lines[end].lstrip()) == indent:
                    break
                end += 1
            assert end < len(lines), "未找到闭合行：%s" % signature
            return "\n".join(lines[idx:end + 1])
    raise AssertionError("未找到函数：%s" % signature)


def _run_node(script):
    node = shutil.which("node")
    assert node, "Node.js is required for the export-notes frontend regression"
    handle = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8")
    try:
        with handle:
            handle.write(script)
        result = subprocess.run(
            [node, handle.name], cwd=PROJECT_ROOT, text=True, encoding="utf-8",
            errors="replace", capture_output=True, check=False,
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


_HELPER_SRC = _extract_function(
    PAPER_JS.read_text(encoding="utf-8"), "function notifyPaperExportNotes(response)"
)

_HARNESS = r"""
%s

var toasts = [];
global.window = { showToast: function (msg, type) { toasts.push({ msg: msg, type: type }); } };
function resp(headers) {
    return { headers: { get: function (name) {
        return Object.prototype.hasOwnProperty.call(headers, name) ? headers[name] : null;
    } } };
}
function take(label, value) {
    var out = { label: label, returned: value, toasts: toasts.slice() };
    toasts.length = 0;
    return out;
}
var cases = [];
cases.push(take("无响应头", notifyPaperExportNotes(resp({}))));
cases.push(take("空值", notifyPaperExportNotes(resp({ "%s": "" }))));
cases.push(take("一条说明", notifyPaperExportNotes(resp({
    "%s": encodeURIComponent("宏名紧贴汉字会被 TeX 吞掉，已补 {}：2 处"),
    "%s": "1",
}))));
cases.push(take("四条说明带计数", notifyPaperExportNotes(resp({
    "%s": encodeURIComponent("定界符被打断：1 处（例：A；B）；宏名被吞：2 处；行分隔丢失：1 处；跨段：3 处"),
    "%s": "4",
}))));
cases.push(take("有正文无计数", notifyPaperExportNotes(resp({
    "%s": encodeURIComponent("裸回车 6 处"),
}))));
cases.push(take("超长正文", notifyPaperExportNotes(resp({
    "%s": encodeURIComponent("相" + "长".repeat(400)),
    "%s": "1",
}))));
cases.push(take("未编码的脏头", notifyPaperExportNotes(resp({
    "%s": "%%E4%%B8%%8D%%E6%%98%%AF%%E6%%96%%87",
    "%s": "1",
}))));
cases.push(take("没有headers", notifyPaperExportNotes({})));
cases.push(take("空响应", notifyPaperExportNotes(null)));
console.log(JSON.stringify(cases));
""" % (
    _HELPER_SRC,
    NOTES_HEADER,
    NOTES_HEADER, NOTE_COUNT_HEADER,
    NOTES_HEADER, NOTE_COUNT_HEADER,
    NOTES_HEADER,
    NOTES_HEADER, NOTE_COUNT_HEADER,
    NOTES_HEADER, NOTE_COUNT_HEADER,
)


def _cases():
    stdout = _run_node(_HARNESS)
    return {item["label"]: item for item in json.loads(stdout.strip().splitlines()[-1])}



def test_notes_header_never_grows_past_its_budget():
    r"""长卷的说明头必须封顶：响应头爆掉的后果是「导出失败」，比不报还糟。

    每条说明都带公式样例，百分号编码后中文按三倍膨胀；主/答案/答题卡三份文档各自
    成串，四十题的卷子能把它顶到近 10KB。所以正文要有预算，同时计数头必须仍写真实
    总条数——截断的是文案，不是事实。
    """

    from mathbank.routes_paper import MAX_NOTES_HEADER_CHARS, _export_notes_headers
    import urllib.parse

    many = ["宏名紧贴汉字会被 TeX 吞掉，已补 {}：%d 处（例：%s）" % (i, r"$A=\left\{x\mid x<1$" * 4)
            for i in range(1, 40)]
    headers = _export_notes_headers(many)

    encoded = headers[NOTES_HEADER]
    assert len(encoded) <= MAX_NOTES_HEADER_CHARS, len(encoded)
    assert headers[NOTE_COUNT_HEADER] == str(len(many)), "条数头必须是真实总数，不许跟着正文一起缩"
    assert 0 < int(headers["X-MathBank-Export-Notes-Shown"]) < len(many), headers
    kept = urllib.parse.unquote(encoded).split("\uff1b")
    assert kept[0] == many[0], "被截断时必须从头保留，不许跳着留"

    small = _export_notes_headers(["定界符被打断：1 处"])
    assert small[NOTE_COUNT_HEADER] == "1"
    assert small["X-MathBank-Export-Notes-Shown"] == "1"
    assert _export_notes_headers([]) == {}, "没问题时一个头都不许发，免得前端凭空弹警告"


def test_export_notes_become_warning_toast():
    cases = _cases()

    for label in ("无响应头", "空值", "没有headers", "空响应"):
        item = cases[label]
        assert item["returned"] == ""
        assert item["toasts"] == [], "%s 不该弹提示：%r" % (label, item["toasts"])

    one = cases["一条说明"]
    assert one["returned"] != ""
    toast = one["toasts"][0]
    assert toast["type"] == "warning", "自动改题是警告级，不能混进成功提示"
    assert "自动修复 1 处" in toast["msg"], toast["msg"]
    assert "回题库核对原文" in toast["msg"], toast["msg"]
    assert "宏名紧贴汉字" in toast["msg"], toast["msg"]

    four = cases["四条说明带计数"]["toasts"][0]["msg"]
    assert "自动修复 4 处" in four, four
    assert "%E" not in four, "百分号编码必须解回原文：%s" % four

    long = cases["超长正文"]["toasts"][0]["msg"]
    assert long.count("长") <= 90, "Toast 不许被超长正文撑爆：%d" % long.count("长")
    assert long.endswith("…"), long

    # 计数头缺失时退化成 1 条，提示仍要出（后端老版本 / 中间层吃掉自定义头）
    bare = cases["有正文无计数"]["toasts"][0]["msg"]
    assert "自动修复 1 处" in bare and "裸回车" in bare, bare


def test_every_paper_export_entry_reports_export_notes():
    """三个导出入口都要消费：漏一个就等于那个入口继续「静默改题」。"""

    source = PAPER_JS.read_text(encoding="utf-8")
    entries = {
        "组卷导出 PDF": "window.exportPaperPdf = async function",
        "全套归档导出": "window.exportPaperBundle = async function",
        "历史试卷快速导出": "window.quickExportPaperPdf = async function",
    }
    for label, signature in entries.items():
        body = _extract_function(source, signature)
        assert "notifyPaperExportNotes(" in body, "%s 没有消费导出说明响应头" % label


def test_export_notes_header_names_match_the_backend():
    """头名逐字对齐：后端改名而前端没跟上时，这条必须当场响。"""

    routes = ROUTES_PAPER.read_text(encoding="utf-8")
    for header in (NOTES_HEADER, NOTE_COUNT_HEADER):
        assert header in routes, "后端不再发 %s" % header
        assert header in _HELPER_SRC, "前端还在读 %s" % header
    assert 'quote("' in routes and 'safe=""' in routes, "中文说明必须百分号编码，否则非 ASCII 头值会被截断"