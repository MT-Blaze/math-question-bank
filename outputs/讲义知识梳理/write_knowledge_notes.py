# -*- coding: utf-8 -*-
"""写入「必修一 第三章 函数的概念与性质」各小节的知识梳理（note_type=knowledge）。

通过本机 27182 端口（本项目服务）的 PUT /api/lecture/notes 写入，
内容为轻量 HTML：<p> 段落、<span class="key"> 关键词、<table class="def"> 定义表；
行内公式用 \(...\)，块公式用 $$...$$（与讲义预览 KaTeX auto-render 分隔符一致）。
"""
import json
import pathlib
import urllib.request

BASE = "http://127.0.0.1:27182"
TOKEN = (pathlib.Path(r"E:\math-question-bank-main\.system_generated\local_token")
         .read_text(encoding="utf-8").strip())

COMPULSORY = "必修一"
CHAPTER = "3. 函数的概念与性质"

NOTES = {
"3.1.1 函数的表示与函数值": """<p><span class="key">函数的概念</span>：设 \\(A\\)、\\(B\\) 是非空实数集，如果按照某种确定的对应关系 \\(f\\)，使对于集合 \\(A\\) 中的任意一个数 \\(x\\)，在集合 \\(B\\) 中都有唯一确定的数 \\(f(x)\\) 与它对应，那么就称 \\(f\\colon A\\to B\\) 为从集合 \\(A\\) 到集合 \\(B\\) 的一个函数，记作 \\(y=f(x),\\ x\\in A\\)。</p><p><span class="key">函数的三要素</span>：<b>定义域</b>（自变量 \\(x\\) 的取值范围 \\(A\\)）、<b>对应关系</b>（\\(f\\)，常表现为解析式）、<b>值域</b>（\\(\\{f(x)\\mid x\\in A\\}\\)，是 \\(B\\) 的子集）。判断两个函数是否为<b>同一函数</b>，只需看定义域与对应关系是否都相同。</p><p><span class="key">区间表示</span>：闭区间 \\([a,b]\\)、开区间 \\((a,b)\\)、半开半闭区间 \\([a,b)\\)、\\((a,b]\\)；无穷区间记作 \\([a,+\\infty)\\)、\\((a,+\\infty)\\)、\\((-\\infty,b]\\)、\\((-\\infty,b)\\) 等。注意：端点按定义域的取舍确定，多个不连续区间用“∪”或“和”连接。</p><table class="def"><tr><th>表示法</th><th>形式</th><th>优点</th></tr><tr><td>解析法</td><td>\\(y=f(x)\\)</td><td>简明，便于运算与推理</td></tr><tr><td>列表法</td><td>表格</td><td>直接查得函数值</td></tr><tr><td>图象法</td><td>函数图象</td><td>直观，便于观察性质</td></tr></table><p><span class="key">函数值的求法</span>：求 \\(f(a)\\) 把 \\(x=a\\) 代入解析式；求 \\(f(f(a))\\) 由内向外逐层代入；由 \\(f(x)=m\\) 求参数，先解方程再代入定义域检验。</p>""",

"3.1.2 解析式": """<p><span class="key">求解析式的常用方法</span>：</p><table class="def"><tr><th>方法</th><th>适用情形</th><th>关键步骤</th></tr><tr><td>换元法</td><td>已知 \\(f(g(x))\\)</td><td>令 \\(t=g(x)\\)，反解 \\(x\\) 代入，注意新元 \\(t\\) 的范围即新函数的定义域</td></tr><tr><td>配凑法</td><td>已知 \\(f(g(x))\\)</td><td>把表达式凑成 \\(g(x)\\) 的整体形式再整体替换</td></tr><tr><td>待定系数法</td><td>已知函数类型</td><td>设出含参解析式，代入条件列方程（组）求参数</td></tr><tr><td>方程组法</td><td>\\(f(x)\\) 与 \\(f(\\frac{1}{x})\\)、\\(f(-x)\\) 共存</td><td>用 \\(\\frac{1}{x}\\) 或 \\(-x\\) 替换 \\(x\\) 构造方程组消元</td></tr><tr><td>赋值法</td><td>抽象函数关系式</td><td>令 \\(x\\)、\\(y\\) 取 \\(0\\)、\\(1\\)、\\(-1\\) 等特殊值求解</td></tr></table><p><span class="key">分段函数</span>：定义域的不同子集对应不同解析式。求值——先判断自变量所在区间再代入对应段；解不等式——分段求解后取并集；含参问题——按分界点分类讨论。</p><p><span class="key">易错提醒</span>：换元后必须注明新元的取值范围（即所求函数的定义域），否则解析式不完整。</p>""",

"3.1.3 定义域": """<p><span class="key">具体函数定义域</span>：使解析式有意义的自变量的集合，各类限制取交集。</p><table class="def"><tr><th>解析式特征</th><th>限制条件</th></tr><tr><td>分式</td><td>分母 \\(\\ne 0\\)</td></tr><tr><td>偶次根式</td><td>被开方数 \\(\\ge 0\\)</td></tr><tr><td>\\(0\\) 次幂（\\(x^0\\)）</td><td>底数 \\(\\ne 0\\)</td></tr><tr><td>对数式</td><td>真数 \\(>0\\)，底数 \\(>0\\) 且 \\(\\ne 1\\)</td></tr><tr><td>实际问题</td><td>还需满足实际意义</td></tr></table><p><span class="key">抽象函数定义域</span>：若 \\(f(x)\\) 的定义域为 \\([a,b]\\)，则 \\(f(g(x))\\) 的定义域由 \\(a\\le g(x)\\le b\\) 解得；反过来，已知 \\(f(g(x))\\) 的定义域求 \\(f(x)\\) 的定义域，就是求 \\(g(x)\\) 的值域。</p><p><span class="key">含参问题</span>：“定义域为 \\(R\\)”转化为不等式恒成立（注意讨论二次项系数是否为 \\(0\\)）；“在某区间上有意义”转化为不等式在给定区间上有解。</p>""",

"3.1.4 值域1（方法）": """<p><span class="key">求值域的常用方法</span>（按方法分类，实际解题常多种方法联用）：</p><table class="def"><tr><th>方法</th><th>适用题型</th><th>要点</th></tr><tr><td>观察法</td><td>简单函数</td><td>直接由解析式与定义域看出值域</td></tr><tr><td>配方法</td><td>二次函数型</td><td>配方后结合定义域（顶点、端点）确定</td></tr><tr><td>换元法</td><td>根式型、复合型</td><td>令新元 \\(t\\) 化为二次函数，新元范围即新定义域</td></tr><tr><td>分离常数法</td><td>一次比一次分式</td><td>分离出常数，得值域 \\(y\\ne \\frac{a}{c}\\)</td></tr><tr><td>基本不等式法</td><td>对勾型</td><td>一正、二定、三相等</td></tr><tr><td>判别式法</td><td>二次比二次分式</td><td>化为关于 \\(x\\) 的方程，\\(\\Delta\\ge 0\\)；注意二次项系数为 \\(0\\) 的情形</td></tr><tr><td>数形结合法</td><td>图象、几何意义</td><td>借助图象、斜率、距离等几何意义</td></tr></table>""",

"3.1.5 值域2（形式）": """<p><span class="key">按函数形式求值域</span>：</p><table class="def"><tr><th>函数形式</th><th>常用方法</th><th>值域结论 / 注意</th></tr><tr><td>一次函数型 \\(y=kx+b\\)</td><td>观察法</td><td>定义域为 \\(R\\) 时值域为 \\(R\\)，区间上取端点</td></tr><tr><td>二次函数型</td><td>配方法</td><td>看对称轴与区间的位置关系</td></tr><tr><td>对勾函数型 \\(y=x+\\frac{a}{x}\\ (a>0)\\)</td><td>基本不等式 / 单调性</td><td>\\((-\\infty,-2\\sqrt{a}]\\cup[2\\sqrt{a},+\\infty)\\)</td></tr><tr><td>飘带函数型 \\(y=x-\\frac{a}{x}\\ (a>0)\\)</td><td>单调性 / 数形结合</td><td>值域为 \\(R\\)（两段均递增）</td></tr><tr><td>分式型（一次比一次）</td><td>分离常数法</td><td>\\(y\\ne \\frac{a}{c}\\)</td></tr><tr><td>分式型（二次比一次）</td><td>换元法 / 数形结合</td><td>令 \\(t=\\) 分母，转化为二次型</td></tr><tr><td>分式型（一次比二次）</td><td>判别式法 / 倒数换元</td><td>注意定义域排除的点</td></tr><tr><td>分式型（二次比二次）</td><td>判别式法</td><td>先观察是否可约分、可配方</td></tr><tr><td>根式型</td><td>换元法 / 平方法</td><td>新元非负；平方法注意等价变形</td></tr><tr><td>分段函数型</td><td>分段讨论</td><td>各段值域取并集</td></tr><tr><td>绝对值型</td><td>分类讨论 / 数形结合</td><td>去绝对值，图象为折线</td></tr></table><p><span class="key">由值域求参数</span>：把“值域包含某数 / 值域为 \\(R\\) / 值域为某区间”转化为方程或不等式有解、恒成立问题。</p>""",

"3.2.1 单调性": """<p><span class="key">定义</span>：设函数 \\(f(x)\\) 的定义域为 \\(I\\)，区间 \\(D\\subseteq I\\)。对任意 \\(x_1,x_2\\in D\\)，当 \\(x_1<x_2\\) 时，若 \\(f(x_1)<f(x_2)\\)，则 \\(f(x)\\) 在 \\(D\\) 上<b>单调递增</b>；若 \\(f(x_1)>f(x_2)\\)，则<b>单调递减</b>。</p><p><span class="key">证明（定义法）五步</span>：任取 \\(x_1<x_2\\) → 作差 \\(f(x_1)-f(x_2)\\) → 变形（因式分解、通分、有理化）→ 定号 → 下结论。作差变形要能判断符号，且 \\(x_1\\)、\\(x_2\\) 须在同一单调区间内。</p><p><span class="key">复合函数单调性</span>：\\(y=f(g(x))\\) 的单调性由内外层<b>同增异减</b>决定；先求定义域，再按内层函数值域分段判断。</p><p><span class="key">运算性质</span>（同一区间上）：增函数 \\(+\\) 增函数 \\(=\\) 增函数，减函数 \\(+\\) 减函数 \\(=\\) 减函数；增函数 \\(-\\) 减函数 \\(=\\) 增函数等。</p><p><span class="key">单调区间</span>：多个单调区间用“和”或“，”连接，<b>不要用“∪”</b>；端点可开可闭（不改变单调性时均可）。</p><p><span class="key">应用</span>：比大小（同区间直接脱 \\(f\\)）、解不等式 \\(f(a)<f(b)\\Rightarrow a<b\\)（同时受定义域限制）、求最值、含参讨论单调性（二次函数看对称轴，分段函数看分界点）。</p>""",

"3.2.2 最值": """<p><span class="key">定义</span>：设函数 \\(y=f(x)\\) 的定义域为 \\(I\\)。若存在实数 \\(M\\) 满足：① 对任意 \\(x\\in I\\)，都有 \\(f(x)\\le M\\)；② 存在 \\(x_0\\in I\\)，使得 \\(f(x_0)=M\\)，则称 \\(M\\) 是函数 \\(f(x)\\) 的<b>最大值</b>。把“\\(\\le\\)”换成“\\(\\ge\\)”即为<b>最小值</b>。</p><p><span class="key">注意</span>：最大（小）值必须在定义域内取到，是值域中的最大（小）元素；只有上（下）界而无取到点，则不能说有最大（小）值。</p><p><span class="key">求法</span>：</p><p>① <b>单调性法</b>：先判断单调性，最值在区间端点取得；</p><p>② <b>二次函数含参最值</b>：讨论对称轴与给定区间的位置关系（轴动区间定 / 轴定区间动），分对称轴在区间左、内、右三类；</p><p>③ <b>分段函数</b>：各段求最值再比较取最大（小）值；</p><p>④ <b>能（恒）成立转化</b>：\\(f(x)\\le M\\) 恒成立 \\(\\Leftrightarrow f(x)_{\\max}\\le M\\)；存在 \\(x\\) 使 \\(f(x)\\ge m\\) \\(\\Leftrightarrow f(x)_{\\max}\\ge m\\)；\\(f(x)\\ge m\\) 恒成立 \\(\\Leftrightarrow f(x)_{\\min}\\ge m\\)。</p><p><span class="key">实际应用</span>：建立模型 → 确定定义域 → 用单调性、基本不等式或二次函数求最值，注意结果须符合实际意义。</p>""",

"3.2.3 奇偶性": """<p><span class="key">定义</span>：函数 \\(f(x)\\) 的定义域关于原点对称。若对任意 \\(x\\in D\\)，都有 \\(f(-x)=f(x)\\)，则 \\(f(x)\\) 为<b>偶函数</b>；若 \\(f(-x)=-f(x)\\)，则为<b>奇函数</b>。</p><p><span class="key">前提条件</span>：定义域必须关于原点对称，否则是非奇非偶函数。判断步骤：① 看定义域是否关于原点对称；② 计算 \\(f(-x)\\) 并与 \\(f(x)\\)、\\(-f(x)\\) 比较；③ 下结论。</p><p><span class="key">图象特征</span>：偶函数图象关于 \\(y\\) 轴对称；奇函数图象关于原点对称。既奇又偶的函数只有 \\(f(x)=0\\)（定义域关于原点对称）。</p><p><span class="key">常用结论</span>：奇函数在对称区间上单调性相同，偶函数在对称区间上单调性相反；奇函数若在 \\(x=0\\) 处有定义，则 \\(f(0)=0\\)（常用来求参）；奇 \\(+\\) 奇 \\(=\\) 奇，偶 \\(+\\) 偶 \\(=\\) 偶，奇 \\(\\times\\) 偶 \\(=\\) 奇，奇 \\(\\times\\) 奇 \\(=\\) 偶。</p><p><span class="key">奇函数 \\(+\\) 常数模型</span>：\\(f(x)=g(x)+c\\)，其中 \\(g(x)\\) 为奇函数，则 \\(f(x)+f(-x)=2c\\)，图象关于点 \\((0,c)\\) 对称。</p><p><span class="key">应用</span>：利用奇偶性求值（转化到已知区间）、求解析式（\\(x<0\\) 时用 \\(-x>0\\) 代入）、求参数、比较大小、结合单调性解不等式。</p>""",

"3.2.4 单调性与奇偶性综合": """<p><span class="key">两条转化主线</span>：</p><p>① <b>奇函数</b>：对称区间单调性一致，把 \\(x<0\\) 的问题化归到 \\(x>0\\) 处理（\\(f(-x)=-f(x)\\)）；</p><p>② <b>偶函数</b>：对称区间单调性相反，常用 \\(f(x)=f(|x|)\\) 把不同自变量统一到非负区间比较。</p><p><span class="key">四大题型</span>：</p><table class="def"><tr><th>题型</th><th>策略</th></tr><tr><td>单调奇偶求最值</td><td>结合对称性确定最值所在位置（奇函数最值对称取到，偶函数对称点函数值相等）</td></tr><tr><td>单调奇偶比较大小</td><td>先用奇偶性把自变量转化到同一单调区间，再用单调性比较</td></tr><tr><td>单调奇偶解不等式</td><td>脱去 \\(f\\)：偶函数转化为 \\(f(|u|)<f(|v|)\\)，奇函数注意不等号方向</td></tr><tr><td>单调奇偶求参数</td><td>由已知性质列方程（组）或不等式，注意定义域约束与分段点</td></tr></table><p><span class="key">抽象函数</span>：无解析式时，直接用单调性、奇偶性的代数定义（任取作差、比较 \\(f(-x)\\)）推理，必要时赋值构造条件。</p>""",

"3.3 幂函数": """<p><span class="key">定义</span>：形如 \\(y=x^{\\alpha}\\)（\\(\\alpha\\) 为常数）的函数叫幂函数。注意：系数必须为 \\(1\\)，指数是常数，底数是自变量；\\(y=2x^3\\)、\\(y=3^x\\) 等不是幂函数。</p><p><span class="key">五个常见幂函数</span>：</p><table class="def"><tr><th>函数</th><th>定义域</th><th>值域</th><th>奇偶性</th><th>单调性</th></tr><tr><td>\\(y=x\\)</td><td>\\(R\\)</td><td>\\(R\\)</td><td>奇</td><td>\\(R\\) 上递增</td></tr><tr><td>\\(y=x^2\\)</td><td>\\(R\\)</td><td>\\([0,+\\infty)\\)</td><td>偶</td><td>\\((-\\infty,0)\\) 减，\\((0,+\\infty)\\) 增</td></tr><tr><td>\\(y=x^3\\)</td><td>\\(R\\)</td><td>\\(R\\)</td><td>奇</td><td>\\(R\\) 上递增</td></tr><tr><td>\\(y=x^{\\frac{1}{2}}\\)</td><td>\\([0,+\\infty)\\)</td><td>\\([0,+\\infty)\\)</td><td>非奇非偶</td><td>\\([0,+\\infty)\\) 上递增</td></tr><tr><td>\\(y=x^{-1}\\)</td><td>\\(\\{x\\mid x\\ne 0\\}\\)</td><td>\\(\\{y\\mid y\\ne 0\\}\\)</td><td>奇</td><td>\\((-\\infty,0)\\)、\\((0,+\\infty)\\) 上递减</td></tr></table><p><span class="key">共同性质</span>：图象都过点 \\((1,1)\\)；都不过第四象限。\\(\\alpha>0\\) 时在第一象限递增，\\(\\alpha<0\\) 时在第一象限递减；\\(\\alpha>1\\) 时图象下凸，\\(0<\\alpha<1\\) 时上凸。</p><p><span class="key">应用</span>：比较大小——化为同指数的幂比底数（用幂函数单调性）；解不等式——利用单调性并注意定义域（如 \\(x^{\\frac{1}{2}}\\) 要求 \\(x\\ge 0\\)，\\(x^{-1}\\) 要求 \\(x\\ne 0\\)）；含参问题——结合图象与单调性分类讨论。</p>""",

"3.4 函数的应用（一）": """<p><span class="key">建模流程</span>：<b>审题</b>（明确已知量与所求量、自变量与因变量）→ <b>建模</b>（建立函数关系式并注明定义域）→ <b>求解</b>（用函数性质求值、求最值）→ <b>检验作答</b>（结果符合实际意义）。</p><p><span class="key">常见函数模型</span>：</p><table class="def"><tr><th>模型</th><th>解析式特征</th><th>典型情境</th></tr><tr><td>一次函数</td><td>\\(y=kx+b\\)</td><td>匀速运动、线性计费</td></tr><tr><td>二次函数</td><td>\\(y=ax^2+bx+c\\)</td><td>利润最大、面积最值</td></tr><tr><td>幂函数</td><td>\\(y=x^{\\alpha}\\)</td><td>面积体积、增长规律</td></tr><tr><td>分段函数</td><td>不同区间不同式</td><td>阶梯计费、税收、分段优惠</td></tr><tr><td>对勾函数</td><td>\\(y=x+\\frac{a}{x}\\ (a>0)\\)</td><td>均值不等式型最值（如每件成本最低）</td></tr><tr><td>分式函数</td><td>分子分母均为一次</td><td>平均成本、效率问题</td></tr></table><p><span class="key">模型选择</span>：均匀变化选一次；面积、利润等二次关系选二次；增长率、翻倍类问题选指数型（后续章节）；分摊、效率类选反比例 / 分式型。</p><p><span class="key">实际最值</span>：先定解析式与定义域，再用配方法、基本不等式或单调性求最值；注意结果的取整、取舍与单位。</p>""",
}


def put_note(knowledge: str, content: str) -> dict:
    payload = json.dumps({
        "note_type": "knowledge",
        "compulsory": COMPULSORY,
        "chapter": CHAPTER,
        "knowledge": knowledge,
        "content": content,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE + "/api/lecture/notes",
        data=payload,
        method="PUT",
        headers={"Content-Type": "application/json", "X-Local-Token": TOKEN},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    print("target:", BASE, "chapter:", CHAPTER, "sections:", len(NOTES))
    ok = 0
    for knowledge, content in NOTES.items():
        row = put_note(knowledge, content)
        print("OK", row.get("note_key"), "len=", len(content))
        ok += 1
    print("written:", ok)


if __name__ == "__main__":
    main()
