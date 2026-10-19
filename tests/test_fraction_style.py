# -*- coding: utf-8 -*-
"""「规范分式」（移植自上游 V2.3.1）回归：纯函数契约、无状态端点、录入页接线。

这个功能的底线是「绝不把对的公式改错」：定界符不配对、未知宏、样式控制宏、代码块、
TikZ、``\\text{}``、``%`` 注释里的内容全部原样放过。所以下面既测「该改的改了」，
也测「不该动的一个字都没动」。行为以实测为准逐条钉住，改语义必须连带改测试。
"""
from main import LOCAL_TOKEN
from mathbank.database import Question
from mathbank.fraction_style import normalize_fraction_style


def _unchanged(source):
    """这段数学必须原样返回：判不清就不许动。"""

    assert normalize_fraction_style(source) == source, source


def _normalized(source, expected):
    assert normalize_fraction_style(source) == expected, source


def test_main_level_fractions_become_dfrac_and_inner_stay_frac():
    _normalized(r"计算 $\frac{1}{2}+\frac{a}{b}$ 的值", r"计算 $\dfrac{1}{2}+\dfrac{a}{b}$ 的值")
    _normalized(r"$$\frac{1}{2}$$", r"$$\dfrac{1}{2}$$")
    _normalized(r"\(\frac{1}{2}\)", r"\(\dfrac{1}{2}\)")
    # 嵌套分式与上标内部降回 \frac：\dfrac 会把行高撑坏
    _normalized(r"$\frac{\frac{1}{2}}{3}$", r"$\dfrac{\frac{1}{2}}{3}$")
    _normalized(r"$a^{\frac{1}{2}}$", r"$a^{\frac{1}{2}}$")
    _normalized(r"$\frac{x^{\frac{1}{3}}}{2}$", r"$\dfrac{x^{\frac{1}{3}}}{2}$")


def test_style_is_idempotent_and_never_touches_delimiters():
    sources = [
        r"已知 $A=\{x|x^2-1>0\}$，求 $\frac{1}{2}$ 的值。",
        r"$\frac{1}{2}$ 与 $\frac{2}{3}$ 之和",
        r"半截公式 $\frac{1}{2} 没有闭合",
        r"混用 \(\frac{1}{2}\] 也不许补定界符",
    ]
    for source in sources:
        once = normalize_fraction_style(source)
        assert normalize_fraction_style(once) == once, (
            "规范分式必须幂等：重复点按钮不该把公式越改越歪"
        )
        assert once.count("$") == source.count("$"), source
        assert once.count(r"\(") == source.count(r"\("), source
        assert once.count(r"\)") == source.count(r"\)"), source


def test_explicit_fraction_commands_are_respected():
    """老师显式写了 \\tfrac / \\cfrac 就是意图，不能被「规范」掉。"""

    _unchanged(r"设 $\tfrac{1}{2}$")
    _unchanged(r"级数 $\cfrac{1}{1+\cfrac{1}{2}}$")


def test_ambiguous_math_gives_up_per_span_not_per_document():
    """定界符不配对、未知宏、样式控制宏：这一整段放弃，同题其它公式照常规范。"""

    _unchanged(r"半截公式 $\frac{1}{2} 没有闭合")
    _unchanged(r"化简 $\displaystyle \frac{1}{2}+\frac{2}{3}$")
    _unchanged(r"$\genfrac{}{}{0pt}{}{1}{2}$")
    # 未知宏所在的那一段不动，但同一行的另一段该规范还是规范
    _normalized(
        r"约定 $\mycommand{\frac{1}{2}}$ 之后求 $\frac{1}{2}$",
        r"约定 $\mycommand{\frac{1}{2}}$ 之后求 $\dfrac{1}{2}$",
    )


def test_code_tikz_text_and_comments_are_locked():
    """代码块、TikZ 源码、``\\text{}``、``%`` 注释都是「原文」，一个字符都不许改。"""

    fenced = "示例：\n```\n\\frac{1}{2}\n```\n请计算 $\\frac{1}{2}$"
    assert normalize_fraction_style(fenced) == (
        "示例：\n```\n\\frac{1}{2}\n```\n请计算 $\\dfrac{1}{2}$"
    )

    tikz = (
        r"如图 \begin{tikzpicture} \node at (0,0) {$\frac{1}{2}$}; "
        r"\end{tikzpicture} 求 $\frac{1}{2}$"
    )
    result = normalize_fraction_style(tikz)
    assert r"\node at (0,0) {$\frac{1}{2}$}" in result, "TikZ 里的数学属于绘图源码"
    assert result.endswith(r"求 $\dfrac{1}{2}$")

    _normalized(r"求 $\frac{\text{甲}{1}}{2}$", r"求 $\dfrac{\text{甲}{1}}{2}$")
    assert normalize_fraction_style(r"$\frac{x}{2}$ 与 % \frac{y}{2} 注释") == (
        r"$\dfrac{x}{2}$ 与 % \frac{y}{2} 注释"
    )
    assert normalize_fraction_style("") == ""
    assert normalize_fraction_style(None) == ""


def test_format_endpoint_is_stateless_and_reports_changes(client, db_session):
    """端点只洗文本：绝不读题、绝不写题，changed 必须等价于「真的改了」。"""

    headers = {"X-Local-Token": LOCAL_TOKEN}
    question = Question(
        content=r"题干 $\frac{1}{2}$",
        question_type="single_choice",
        category_compulsory="必修一",
    )
    db_session.add(question)
    db_session.commit()

    response = client.post(
        "/api/format/fractions", headers=headers, data={"text": r"求 $\frac{1}{2}$ 的值"}
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["text"] == r"求 $\dfrac{1}{2}$ 的值"
    assert payload["changed"] is True

    same = client.post(
        "/api/format/fractions", headers=headers, data={"text": r"求 $\dfrac{1}{2}$ 的值"}
    )
    assert same.json()["changed"] is False

    empty = client.post("/api/format/fractions", headers=headers, data={"text": ""})
    assert empty.status_code == 200
    assert empty.json() == {"status": "success", "text": "", "changed": False}

    db_session.expire_all()
    stored = db_session.query(Question).filter(Question.id == question.id).first()
    assert stored.content == r"题干 $\frac{1}{2}$", "预览端点绝不能顺手改库存数据"
    assert db_session.query(Question).count() == 1


def test_fraction_style_route_lives_in_the_question_router():
    """架构约束：处理器在 routes_questions，main.py 只做装配。"""

    import mathbank.routes_questions as routes_questions

    source = open(routes_questions.__file__, encoding="utf-8").read()
    assert '@router.post("/api/format/fractions")' in source
    assert "def format_fraction_style(" in source
    assert "@app.post(\"/api/format/fractions\")" not in open(
        "main.py", encoding="utf-8"
    ).read()
    endpoint = next(
        route.endpoint
        for route in routes_questions.router.routes
        if getattr(route, "path", None) == "/api/format/fractions"
    )
    assert endpoint.__module__ == "mathbank.routes_questions"


def test_fraction_style_is_module_level_importable_without_flask():
    """纯函数模块不许依赖 app：导入它不该把整条路由链一起拖起来。"""

    source = open("mathbank/fraction_style.py", encoding="utf-8").read()
    assert "from fastapi" not in source
    assert "Depends" not in source
    assert "import json" not in source or "json." not in source
    # 移植必须对齐我们自己的公开辅助名：_balanced_group 在这个仓库里不存在
    assert "_balanced_group" not in source
    assert "from mathbank.tex_helper import balanced_group" in source
