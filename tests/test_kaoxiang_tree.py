# -*- coding: utf-8 -*-
"""考向树解析/渲染工具与「JSON 单一数据源」的回归测试。

TXT ↔ Web 大纲双向同步链路已下线（考向树的唯一事实来源是 JSON），本文件守两件事：

* 解析与渲染纯函数：三种历史版式、章节键匹配、id 稳定性、render→parse 不动点；
* 同步链路确实死了：启动不再改写任何 JSON，接口与前端入口都不存在。

历史 TXT 已归档到 `.qoder-scratch/kaoxiang_txt_archive_20261001/`，项目目录内不
再有可读的 ``考向树/`` 源文件，因此依赖真实 TXT 的用例改为合成文本或 tmp_path。
"""

import copy
import json
import re
from pathlib import Path

import pytest

from mathbank import kaoxiang_tree as kt
from mathbank.metadata import normalize_metadata_curriculum
from mathbank.topics_helper import topic_id_for

PROJECT_ROOT = Path(__file__).resolve().parents[1]

LEVELS = set(kt.LEVELS)


SAMPLE_TXT = """第一章 测试章
│
├─ 1.1 第一节
│  ├─ L1 概念理解
│  │  ├─ 考向1：甲考向
│  │  │  └─ 例：示例甲。
│  │  └─ 考向2：乙考向
│  │     └─ 例：示例乙。
│  │
│  └─ L3 性质应用
│     └─ 考向3：丙考向
│
└─ 1.2 第二节
   ├─ L1 概念理解
   │  └─ 考向1：丁考向
   └─ L5 创新拓展
"""



def test_normalize_chapter_key_bridges_both_styles():
    assert kt.normalize_chapter_key("第一章 集合与常用逻辑用语") == kt.normalize_chapter_key(
        "1. 集合与常用逻辑用语"
    )
    assert kt.normalize_chapter_key("第三章 函数的概念与性质") == kt.normalize_chapter_key(
        "3. 函数的概念与性质"
    )


def test_match_chapter_key_handles_missing_book():
    curriculum = {"必修一": {"1. 集合与常用逻辑用语": []}}
    assert kt.match_chapter_key(curriculum, "必修一", "第一章 集合与常用逻辑用语") == (
        "1. 集合与常用逻辑用语"
    )
    assert kt.match_chapter_key(curriculum, "必修二", "第一章 集合与常用逻辑用语") == ""


    def test_three_level_sections_keep_group(self):
        """3.1.1 这类三级小节要记住它所属的 3.1 分组。

        v3 起真实考向树改用 3.2.1/3.2.2 平铺（无 3.2 父节），不再有三级分组样本，
        故此断言改用合成文本验证解析器的分组能力，避免绑定某份具体数据。
        """

        tree = kt.parse_tree_text(
            "第三章 函数的概念与性质\n"
            "3.1 函数的概念与表示\n"
            "  3.1.1 区间的表示\n"
            "    L1 概念理解\n"
            "      考向1：区间写法\n"
        )
        grouped = [s for s in tree["sections"] if s.get("group")]
        assert grouped, tree["sections"]
        assert any(s["name"].count(".") == 2 for s in grouped)


def test_empty_level_markers_are_ignored():
    """用户手工写的「（留空）」占位不能被拼到上一个考向的 example 里。"""

    tree = kt.parse_tree_text(
        "第五章 三角函数\n"
        "5.1.1 任意角\n"
        "  L1 概念理解\n"
        "    考向1：任意角的概念判断\n"
        "  L2 基础运算\n"
        "    （留空）\n"
        "  L3 性质应用\n"
        "    （空）\n"
    )
    topics = tree["sections"][0]["topics"]
    assert [t["name"] for t in topics] == ["任意角的概念判断"]
    assert topics[0]["example"] == ""
    assert topics[0]["level"] == "L1"


def test_example_continuation_does_not_leak_to_next_level():
    """进入新层级后，example 续行不能再挂到上层最后一个考向上。"""

    tree = kt.parse_tree_text(
        "第五章 三角函数\n"
        "5.1.1 任意角\n"
        "  L1 概念理解\n"
        "    考向1：任意角的概念判断\n"
        "    例：终边相同的角相差 360° 的整数倍。\n"
        "  L2 基础运算\n"
        "    考向2：直线角\n"
    )
    topics = tree["sections"][0]["topics"]
    assert [(t["level"], t["name"]) for t in topics] == [
        ("L1", "任意角的概念判断"),
        ("L2", "直线角"),
    ]
    assert "终边相同的角" in topics[0]["example"]
    assert topics[1]["example"] == ""



def test_normalize_keeps_kaoxiang_fields():
    curriculum = {
        "必修一": {
            "1. 测试章": [
                {
                    "name": "1.1 第一节",
                    "group": "1 分组",
                    "level_titles": {"L1": "概念理解"},
                    "topics": [
                        {
                            "id": "k_1",
                            "name": "甲考向",
                            "level": "L2",
                            "no": 3,
                            "example": "例：示例。",
                        }
                    ],
                }
            ]
        }
    }
    normalized, modified = normalize_metadata_curriculum(curriculum)
    assert modified is False
    section = normalized["必修一"]["1. 测试章"][0]
    assert section["group"] == "1 分组"
    assert section["level_titles"] == {"L1": "概念理解"}
    topic = section["topics"][0]
    assert topic["level"] == "L2"
    assert topic["no"] == 3
    assert topic["example"] == "例：示例。"


# ---------------------------------------------------------------------------
# #20 第三步：新增考向不得被 TXT 往返吞掉（方案 C = 写侧兜底 + 读侧放宽）
#
# 全部用合成大纲或 tmp_path，绝不触碰真实考向树 TXT、A.json 与活跃大纲。
# ---------------------------------------------------------------------------

SYNTH_BASE = [
    {
        "name": "1.1 第一节",
        "topics": [
            {"id": "k_a1", "name": "甲考向", "level": "L1", "no": 1, "example": "例：示例甲。"},
            {"id": "k_a2", "name": "乙考向", "level": "L1", "no": 2, "example": "例：示例乙。"},
            {"id": "k_b1", "name": "丙考向", "level": "L2", "no": 3, "example": "例：示例丙。"},
            {"id": "k_c1", "name": "丁考向", "level": "L3", "no": 4, "example": ""},
        ],
    }
]


def _synth_base():
    return copy.deepcopy(SYNTH_BASE)


def _with_new_topic(sections, name="新增考向", topic_id="k_new"):
    """模拟手动「新增考向」/ AI 自动建档往大纲里只写 id + name。"""

    sections[0]["topics"].append({"id": topic_id, "name": name})
    return sections


def test_new_topic_without_no_or_level_survives_roundtrip():
    """只有 id/name 的新考向必须活过一次 render→parse，且不得污染邻居。"""

    sections = _with_new_topic(_synth_base())
    rendered = kt.render_tree_text({"title": "第一章 测试章", "sections": sections})

    line = [l.strip() for l in rendered.splitlines() if "新增考向" in l and "例：" not in l]
    assert line and re.match(r"^.*└─ 考向\d+：新增考向$", line[0]), line
    # 必须是「带序号 + 冒号」的可解析形态，绝不能是裸行 `考向新增考向`
    assert kt._KAOXIANG_RE.match(line[0].lstrip("│├└─ ")), line

    pulled, stats = kt.build_sections("必修一", "1. 测试章", kt.parse_tree_text(rendered), sections)
    names = [t["name"] for s in pulled for t in s["topics"]]
    assert "新增考向" in names, names
    # 反向断言：绝不能被当续行拼进上一个考向的 example
    for sec in pulled:
        for topic in sec["topics"]:
            assert "新增考向" not in (topic.get("example") or ""), (topic["name"], topic.get("example"))
    # id 按名字复用，不造幽灵考点
    assert stats["added"] == 0, stats


def test_render_fallback_does_not_mutate_the_outline():
    """兜底只作用于渲染副本；大纲里的真实条目必须保持只有 id/name 两个键。"""

    sections = _with_new_topic(_synth_base())
    kt.render_tree_text({"title": "第一章 测试章", "sections": sections})
    fresh = [t for t in sections[0]["topics"] if t["name"] == "新增考向"][0]
    assert set(fresh.keys()) == {"id", "name"}, fresh


def test_fallback_assigns_section_scoped_number():
    """补出来的 no 是节内序号，跨节互不串号。"""

    sections = _synth_base()
    sections.append(
        {
            "name": "1.2 第二节",
            "topics": [
                {"id": "k_x1", "name": "戊考向", "level": "L1", "no": 1, "example": ""},
                {"id": "k_x2", "name": "己考向"},
            ],
        }
    )
    first = kt._topics_with_render_defaults(sections[0])
    second = kt._topics_with_render_defaults(sections[1])
    assert [t["no"] for t in first] == [1, 2, 3, 4], first
    assert [t["no"] for t in second] == [1, 2], second
    assert second[1]["level"] == "L1", second[1]


def test_new_topic_between_levels_renumbers_following_topics():
    """新考向归到 L1 桶后，其后条目的 no 连续后移 —— 既有的统一重编号语义。"""

    sections = _with_new_topic(_synth_base())
    stable = kt.pull_stable_sections(sections)
    topics = stable[0]["topics"]
    assert [(t["no"], t["level"], t["name"]) for t in topics] == [
        (1, "L1", "甲考向"),
        (2, "L1", "乙考向"),
        (3, "L1", "新增考向"),
        (4, "L2", "丙考向"),
        (5, "L3", "丁考向"),
    ], topics


def test_pull_stable_sections_reaches_text_fixed_point():
    """校正一次后 TXT 文本必须停在不动点，否则每次保存都多写一遍文件。"""

    sections = _with_new_topic(_synth_base())
    stable = kt.pull_stable_sections(sections)
    first = kt.render_tree_text({"title": "第一章 测试章", "sections": stable})
    repulled, _ = kt.build_sections("必修一", "1. 测试章", kt.parse_tree_text(first), stable)
    second = kt.render_tree_text({"title": "第一章 测试章", "sections": repulled})
    assert first == second, "TXT 文本未收敛到不动点"
    # sync_to_txt 的真实判据：大纲与刚写下的 TXT 解析结果等价
    assert kt._canonical_signature(stable) == kt._canonical_signature(
        kt.parse_tree_text(first)["sections"]
    )


def test_pull_stable_sections_keeps_ids_and_content():
    """校正只动编号口径，内容签名与 id 集合都必须原样保留。"""

    sections = _with_new_topic(_synth_base())
    stable = kt.pull_stable_sections(sections)
    assert {t["id"] for s in stable for t in s["topics"]} == {
        t["id"] for s in sections for t in s["topics"]
    }
    before = {(t["name"], t.get("example", "")) for s in sections for t in s["topics"]}
    after = {(t["name"], t.get("example", "")) for s in stable for t in s["topics"]}
    assert before == after, before ^ after


def test_hand_written_bare_topic_line_is_recognized():
    """用户在 TXT 里手打无冒号裸名行必须成为一条考向，而不是续行。"""

    tree = kt.parse_tree_text(
        "第一章 测试章\n"
        "├─ 1.1 第一节\n"
        "│  ├─ L1 概念理解\n"
        "│  │  ├─ 考向1：甲考向\n"
        "│  │  │  └─ 例：示例甲。\n"
        "│  │  └─ 考向乙考向\n"
    )
    topics = tree["sections"][0]["topics"]
    assert [t["name"] for t in topics] == ["甲考向", "乙考向"], topics
    assert topics[0]["example"] == "例：示例甲。", topics[0]


def test_bare_topic_line_inherits_its_level_bucket():
    """裸名行写在 L2 下就归 L2，不会被硬塞进 L1 打乱既有层级。"""

    tree = kt.parse_tree_text(
        "第一章 测试章\n"
        "├─ 1.1 第一节\n"
        "│  ├─ L1 概念理解\n"
        "│  │  └─ 考向1：甲考向\n"
        "│  ├─ L2 基础运算\n"
        "│  │  └─ 考向乙考向\n"
    )
    topics = tree["sections"][0]["topics"]
    assert [(t["level"], t["name"]) for t in topics] == [("L1", "甲考向"), ("L2", "乙考向")]


def test_digit_only_name_is_dropped_instead_of_becoming_a_topic():
    """漏了冒号的残行不能变成名叫「3」的幽灵考点。"""

    tree = kt.parse_tree_text(
        "第一章 测试章\n"
        "├─ 1.1 第一节\n"
        "│  ├─ L1 概念理解\n"
        "│  │  ├─ 考向1：甲考向\n"
        "│  │  └─ 考向3\n"
    )
    topics = tree["sections"][0]["topics"]
    assert [t["name"] for t in topics] == ["甲考向"], topics


def test_leading_colon_is_stripped_from_the_name():
    """光有冒号没有序号的行，名字里不该带着那个冒号。"""

    tree = kt.parse_tree_text(
        "第一章 测试章\n"
        "├─ 1.1 第一节\n"
        "│  ├─ L1 概念理解\n"
        "│  │  └─ 考向：待定\n"
    )
    assert [t["name"] for t in tree["sections"][0]["topics"]] == ["待定"]


# ---------------------------------------------------------------------------
# 渲染 ↔ 解析的往返纯函数（方案 C 的成果，与 TXT 是否作为数据源无关）
# ---------------------------------------------------------------------------

def test_render_roundtrip_on_sample_txt_reaches_fixed_point():
    """SAMPLE_TXT → 建树 → 渲染 → 再解析：内容签名必须一条不差。"""

    tree = kt.parse_tree_text(SAMPLE_TXT)
    sections, _ = kt.build_sections("必修一", "1. 测试章", tree, [])
    for style in ("tree", "indent"):
        rendered = kt.render_tree_text({"title": "第一章 测试章", "sections": sections}, style)
        reparsed, _ = kt.build_sections(
            "必修一", "1. 测试章", kt.parse_tree_text(rendered), []
        )
        assert kt._canonical_signature(reparsed) == kt._canonical_signature(sections), style


def test_relaxed_regex_matches_old_parser_on_numbered_lines():
    """放宽后的正则对「序号+冒号」的标准行判定必须与旧写法完全一致。

    旧受管 TXT 已归档，这里改用覆盖全部历史形态的合成行来守同一个结论：
    放宽只多认出裸行，绝不让原本能解析的行改变判定。
    """

    strict = re.compile(r"^考向\s*(\d+)?\s*[:：]\s*(.*)$")
    samples = [
        "考向1：甲考向",
        "考向12：长名字考向",
        "考向1:半角冒号",
        "考向 3 ：空格包裹",
        "├─ 考向4：树线形态",
        "│  │  └─ 考向5：深层树线",
        "考向无编号裸行",
    ]
    changed = []
    for content in samples:
        stripped = kt._split_prefix(content)[1].strip()
        if bool(strict.match(stripped)) != bool(kt._KAOXIANG_RE.match(stripped)):
            changed.append(content)
    # 只有最后一行（裸行）允许出现差异：旧写法认不出、新写法认得出。
    assert changed == ["考向无编号裸行"], changed


# ---------------------------------------------------------------------------
# JSON 单一数据源：TXT 同步链路必须彻底失效
# ---------------------------------------------------------------------------

def test_kaoxiang_sync_surface_is_gone():
    """模块不再暴露任何 pull/push/sync 入口，防止有人悄悄把链路接回来。"""

    for name in (
        "sync_from_txt",
        "sync_to_txt",
        "pull_from_txt",
        "push_to_txt",
        "auto_pull_if_needed",
        "_txt_writes_allowed",
        "_sources_changed",
        "_record_state",
        "protected_topic_ids",
    ):
        assert not hasattr(kt, name), name


def test_main_module_has_no_txt_pull_call():
    """`main.py` 导入期绝不再出现 TXT 拉取 —— 「启动改写 A.json」隐患的根。

    真跑一次 `import main` 需要独占 runtime lock（服务在跑时拿不到），所以这里守
    源码事实：pull 入口既不在模块里、也不可能在调用点出现。服务停止后可用
    `python -m scripts.check_kaoxiang_json_only` 再补一次真实导入验证。
    """

    for rel in ("main.py", "mathbank/routes_library.py", "mathbank/routes_settings.py"):
        source = (PROJECT_ROOT / rel).read_text(encoding="utf-8")
        assert "auto_pull_if_needed" not in source, "%s 又接回了 TXT 拉取" % rel
        assert "push_to_txt" not in source, "%s 又接回了 TXT 回写" % rel
    metadata_source = (PROJECT_ROOT / "mathbank" / "metadata.py").read_text(encoding="utf-8")
    assert "kaoxiang_tree" not in metadata_source, "自动建档又把大纲推回 TXT"
    assert "pull_from_txt" not in (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
    assert "routes_kaoxiang" not in (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
    assert not hasattr(kt, "auto_pull_if_needed")
    assert not hasattr(kt, "pull_from_txt")
def test_kaoxiang_routes_are_unmounted(client):
    """两个 TXT 同步接口都不存在了（404），前端不可能再误触发同步。"""

    for path in ("/api/kaoxiang/status", "/api/kaoxiang/sync"):
        response = client.get(path)
        assert response.status_code == 404, (path, response.status_code)


def test_frontend_has_no_txt_sync_entry():
    """设置页不再有「TXT → 网页 / 网页 → TXT」面板，也不再有对应 JS 助手。"""

    html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    api = (PROJECT_ROOT / "static" / "js" / "api.js").read_text(encoding="utf-8")
    assert "kaoxiangStatus" not in html
    assert "TXT → 网页" not in html and "网页 → TXT" not in html
    for token in ("kaoxiangRefresh", "kaoxiangSync", "kaoxiangRenderStatus"):
        assert token not in api, token


def test_txt_sync_helpers_are_gone_from_the_frontend():
    """`api.js` 里三个 TXT 面板助手与 `loadBothTrees` 的自动刷新都已移除。

    `loadBothTrees` 本身还在用（预设树刷新），所以只守它后面那一行调用。
    """

    api = (PROJECT_ROOT / "static" / "js" / "api.js").read_text(encoding="utf-8")
    assert "kaoxiangRenderStatus" not in api
    assert "window.kaoxiangRefresh" not in api
    assert "window.kaoxiangSync" not in api
    assert "/api/kaoxiang/" not in api
    # 大纲 Tab 切换时只剩一次 loadBothTrees，不再附带 TXT 状态刷新
    assert api.count("if (typeof loadBothTrees === 'function') loadBothTrees();") == 1
def test_txt_archive_is_intact():
    """归档完整性：``考向树/`` 已从项目目录移走，唯一副本在 scratch 归档里。

    守两件事：项目目录不得再出现 TXT（否则「JSON 单一来源」被悄悄打破），
    归档里的 6 份文件必须与 MANIFEST 的字节数和 SHA-256 逐一对得上
    （历史数据不能烂在归档里）。
    """

    import hashlib

    project_txt = list((PROJECT_ROOT / "考向树").glob("*.txt")) if (PROJECT_ROOT / "考向树").exists() else []
    assert project_txt == [], [p.name for p in project_txt]

    archive = PROJECT_ROOT / ".qoder-scratch" / "kaoxiang_txt_archive_20261001"
    if not archive.exists():
        pytest.skip("归档目录不在本机（例如 CI），跳过归档完整性检查")
    manifest = {}
    for line in (archive / "MANIFEST.txt").read_text(encoding="utf-8").splitlines():
        name, size, digest = line.split("	")
        manifest[name] = (int(size), digest)
    assert len(manifest) == 6, manifest
    for name, (size, digest) in manifest.items():
        data = (archive / name).read_bytes()
        assert len(data) == size, name
        assert hashlib.sha256(data).hexdigest() == digest, name


def test_active_outline_topics_all_have_id_and_name():
    """JSON 成为唯一来源后，考点 ID 只能由大纲自己保证：每条考向都得有 id/name。"""

    outline_path = PROJECT_ROOT / "data_backup" / "custom_metadata.json"
    if not outline_path.exists():
        # 这是**本机运行数据**的体检：data_backup/* 被 .gitignore 挡在库外，干净检出没有它。
        # 大纲结构本身的守卫在别处（考点 ID/名称的生成逻辑），不依赖这份快照。
        pytest.skip("自定义大纲快照不在本机（干净检出/CI），跳过这条本机数据体检")
    metadata = json.loads(outline_path.read_text(encoding="utf-8"))
    curriculum = metadata.get("curriculum", {})
    total = 0
    broken = []
    seen_ids = set()
    duplicated = []
    for compulsory, chapters in curriculum.items():
        for chapter, sections in (chapters or {}).items():
            for sec in sections or []:
                for topic in sec.get("topics", []) or []:
                    total += 1
                    tid = str(topic.get("id") or "")
                    name = str(topic.get("name") or "").strip()
                    if not tid or not name:
                        broken.append((compulsory, chapter, sec.get("name"), topic))
                    elif tid in seen_ids:
                        duplicated.append(tid)
                    else:
                        seen_ids.add(tid)
    assert total > 400, total
    assert broken == [], broken[:3]
    assert duplicated == [], duplicated[:3]


# ---------------------------------------------------------------------------
# AI 分类字段 → 大纲规范名（拆卷卡片「章节/小节/考向」自动填充的地基）
# ---------------------------------------------------------------------------

ALIGN_CURRICULUM = {
    "必修一": {
        "1. 集合与常用逻辑用语": [
            {"name": "1.1 集合的概念", "topics": [
                {"id": "k_a", "name": "集合的判断", "level": "L1"},
                {"id": "k_b", "name": "元素与集合的关系", "level": "L2"},
            ]},
            {"name": "1.3 集合的基本运算", "topics": [
                {"id": "k_c", "name": "交集运算", "level": "L1"}]},
        ],
        "3. 函数的概念与性质": [
            {"name": "3.2.1 函数的单调性", "topics": [
                {"id": "k_d", "name": "利用单调性解不等式", "level": "L3"}]},
        ],
    }
}


def _align(question, curriculum=None):
    """跑一遍归一，返回 (改写后的题, 调整记录, 用到的大纲)。"""
    book = copy.deepcopy(curriculum if curriculum is not None else ALIGN_CURRICULUM)
    q = dict(question)
    notes = kt.align_parsed_classification(book, [q])
    return q, notes, book


def test_split_chapter_section_handles_row_copying():
    r"""提示词里的教材清单是「章 > 小节」一整行，模型会把整行抄进 category_chapter。"""
    assert kt.split_chapter_section("3. 函数的概念与性质 > 3.1 函数的概念及表示") == (
        "3. 函数的概念与性质", "3.1 函数的概念及表示")
    assert kt.split_chapter_section("1. 集合与常用逻辑用语＞1.3 集合的基本运算") == (
        "1. 集合与常用逻辑用语", "1.3 集合的基本运算")
    assert kt.split_chapter_section("1. 集合与常用逻辑用语") == ("1. 集合与常用逻辑用语", "")
    assert kt.split_chapter_section("") == ("", "")


def test_align_maps_composite_chapter_back_to_outline_key():
    r"""2026-10-19 大庆实验卷真实形态：章被写成整行「章 > 小节」，必须归一回大纲章键。

    当时快路径 AI 的 22 条分类全给了、block_id 逐条对上，卡片却三排全空：章名对不上
    大纲键就选不中，而小节与考向依赖章先选中，于是连锁清空。
    """
    q, notes, _book = _align({
        "category_compulsory": "必修一",
        "category_chapter": "1. 集合与常用逻辑用语 > 1.1 集合的概念",
        "category_knowledge": "1.1 集合的概念",
        "topic": "集合的判断",
    })
    assert q["category_chapter"] == "1. 集合与常用逻辑用语", q
    assert q["category_knowledge"] == "1.1 集合的概念", q
    assert q["topic"] == "集合的判断", q
    assert any("章节" in n for n in notes), notes

    # 幂等：写库边界与卡片重渲染都会重复调用，第二次必须零改写
    again, notes2, _ = _align(q)
    assert again == q, (again, q)
    assert notes2 == [], notes2


def test_align_recovers_chapter_from_correct_section():
    """章名写飞、小节抄对：以「小节确实挂在哪一章」为准，别让三排下拉一起空。"""
    q, _notes, _book = _align({
        "category_compulsory": "必修一",
        "category_chapter": "9. 不存在的章",
        "category_knowledge": "3.2.1 函数的单调性",
        "topic": "利用单调性解不等式",
    })
    assert q["category_chapter"] == "3. 函数的概念与性质", q
    assert q["category_knowledge"] == "3.2.1 函数的单调性", q
    assert q["topic"] == "利用单调性解不等式", q


def test_align_accepts_names_without_numbering_and_strips_topic_level():
    """AI 常丢掉大纲键的编号前缀，考向名也可能带 (L1) 级别尾巴。"""
    q, _notes, _book = _align({
        "category_compulsory": "必修一",
        "category_chapter": "集合与常用逻辑用语",
        "category_knowledge": "集合的基本运算",
        "topic": "交集运算（L1）",
    })
    assert q["category_chapter"] == "1. 集合与常用逻辑用语", q
    assert q["category_knowledge"] == "1.3 集合的基本运算", q
    assert q["topic"] == "交集运算", q


def test_align_blanks_unmatched_names_yet_keeps_unknown_topic():
    """章/节对不上按大纲口径置空（启动自检对知识点也这么处理）；考向未知则留原文。"""
    q, notes, _book = _align({
        "category_compulsory": "必修一",
        "category_chapter": "量子力学初步",
        "category_knowledge": "波粒二象性",
        "topic": "教师大纲里还没有的新考向",
    })
    assert q["category_chapter"] == "", q
    assert q["category_knowledge"] == "", q
    assert q["topic"] == "教师大纲里还没有的新考向", q
    assert any("章节" in n for n in notes), notes


def test_align_leaves_other_textbook_versions_untouched():
    """教材本身不在当前大纲里（切了版本）：整套字段原样不动，不替教师抹掉线索。"""
    raw = {"category_compulsory": "必修九", "category_chapter": "X. 别版本的章",
           "category_knowledge": "Y 小节", "topic": "Z 考向"}
    q, notes, _book = _align(raw)
    assert q == raw, q
    assert notes == [], notes


def test_align_is_safe_on_missing_fields_and_dirty_rows():
    """没有分类字段的题、非 dict 的行都不许炸，也不许凭空造字段。"""
    question = {"content": "没有分类字段"}
    assert kt.align_parsed_classification(ALIGN_CURRICULUM, []) == []
    assert kt.align_parsed_classification(ALIGN_CURRICULUM, [question, None, "x"]) == []
    assert question == {"content": "没有分类字段"}, question
    assert kt.align_parsed_classification({}, [{"category_chapter": "任意"}]) == []
