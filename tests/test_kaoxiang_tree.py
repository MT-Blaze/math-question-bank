# -*- coding: utf-8 -*-
"""考向树 TXT 底层文件 ↔ Web 大纲双向同步的回归测试。

覆盖：解析三种历史版式、渲染回写、章节键匹配、id 稳定性、幂等性、
题目引用保护，以及 normalize 不再吞掉 level/no/example。
"""

import json
import shutil
from pathlib import Path

import pytest

from mathbank import kaoxiang_tree as kt
from mathbank.metadata import normalize_metadata_curriculum
from mathbank.topics_helper import topic_id_for

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TREE_DIR = PROJECT_ROOT / "考向树"

LEVELS = set(kt.LEVELS)


def _source_files():
    if not TREE_DIR.exists():
        return []
    return sorted(p for p in TREE_DIR.glob("*.txt") if not p.name.startswith("_"))


SOURCE_FILES = _source_files()


@pytest.mark.skipif(not SOURCE_FILES, reason="考向树 TXT 源文件缺失")
class TestParseRealFiles:
    def test_every_file_parses(self):
        assert len(SOURCE_FILES) >= 4
        for path in SOURCE_FILES:
            tree = kt.parse_tree_file(str(path))
            assert tree["compulsory"] == "必修一", path.name
            assert tree["chapter_title"], path.name
            assert tree["sections"], path.name

    def test_sections_are_well_formed(self):
        for path in SOURCE_FILES:
            tree = kt.parse_tree_file(str(path))
            for sec in tree["sections"]:
                topics = sec["topics"]
                assert topics, (path.name, sec["name"])
                # 序号在每节内从 1 连续编号
                assert [t["no"] for t in topics] == list(range(1, len(topics) + 1))
                for topic in topics:
                    assert topic["level"] in LEVELS, (path.name, topic)
                    assert topic["name"].strip(), (path.name, topic)

    def test_chapter_keys_match_active_metadata(self):
        """TXT 的章标题必须能匹配到活跃大纲里的章，否则同步会静默失效。"""

        metadata = json.loads(
            (PROJECT_ROOT / "data_backup" / "custom_metadata.json").read_text(
                encoding="utf-8"
            )
        )
        curriculum = metadata.get("curriculum", {})
        for path in SOURCE_FILES:
            tree = kt.parse_tree_file(str(path))
            key = kt.match_chapter_key(
                curriculum, tree["compulsory"], tree["chapter_title"]
            )
            assert key, (path.name, tree["chapter_title"])

    def test_three_level_sections_keep_group(self):
        """3.1.1 这类三级小节要记住它所属的 3.1 分组。"""

        grouped = []
        for path in SOURCE_FILES:
            for sec in kt.parse_tree_file(str(path))["sections"]:
                if sec.get("group"):
                    grouped.append((path.name, sec["group"], sec["name"]))
        assert grouped, "三级小节未识别出分组"
        assert any(name.count(".") == 2 for _, _, name in grouped)


@pytest.mark.skipif(not SOURCE_FILES, reason="考向树 TXT 源文件缺失")
@pytest.mark.parametrize("path", SOURCE_FILES, ids=lambda p: p.stem)
def test_render_roundtrip_preserves_tree(path):
    """解析 → 建树 → 渲染 → 再解析，内容必须完全一致（两种版式都要）。"""

    tree = kt.parse_tree_file(str(path))
    curriculum = {
        "必修一": {"1. 集合与常用逻辑用语": [], "3. 函数的概念与性质": []}
    }
    # 用真实章键构造一遍，保证 id 生成路径与线上一致
    chapter_key = kt.normalize_chapter_key(tree["chapter_title"])
    sections, _ = kt.build_sections("必修一", chapter_key, tree, [])
    expected = kt._signature_from_parsed(tree)
    for style in ("tree", "indent"):
        rendered = kt.render_tree_text(
            {"title": tree["chapter_title"], "sections": sections}, style
        )
        reparsed = kt.parse_tree_text(rendered)
        assert kt._signature_from_parsed(reparsed) == expected, (path.name, style)


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


@pytest.fixture()
def tree_dir(tmp_path, monkeypatch):
    target = tmp_path / "考向树"
    target.mkdir()
    (target / "必修一 第一章 测试章.txt").write_text(SAMPLE_TXT, encoding="utf-8")
    monkeypatch.setattr(kt, "KAOXIANG_DIR", target)
    monkeypatch.setattr(kt, "STATE_FILE", tmp_path / "state.json")
    kt._MTIME_CACHE.clear()
    yield target
    kt._MTIME_CACHE.clear()


def test_pull_rewrites_chapter_and_is_idempotent(tree_dir):
    curriculum = {
        "必修一": {
            "1. 测试章": [
                {"name": "1.1 第一节", "topics": [{"id": "k_old", "name": "过期考向"}]},
                {"name": "9.9 无关节", "topics": [{"id": "k_x", "name": "别的章的节"}]},
            ]
        }
    }
    report = kt.sync_from_txt(curriculum, protected=set())
    assert report["changed"] is True
    entry = report["files"][0]
    assert entry["action"] == "pulled"
    assert entry["chapter"] == "1. 测试章"
    assert entry["after"] == 4

    sections = curriculum["必修一"]["1. 测试章"]
    assert [s["name"] for s in sections] == ["1.1 第一节", "1.2 第二节"]
    first = sections[0]["topics"]
    assert [t["name"] for t in first] == ["甲考向", "乙考向", "丙考向"]
    assert [t["level"] for t in first] == ["L1", "L1", "L3"]
    assert [t["no"] for t in first] == [1, 2, 3]
    assert first[0]["example"] == "例：示例甲。"
    assert first[0]["id"] == topic_id_for("必修一", "1. 测试章", "1.1 第一节", "甲考向")

    # 第二次必须判定为已同步，且 id 不变
    ids_before = [t["id"] for s in sections for t in s["topics"]]
    again = kt.sync_from_txt(curriculum, protected=set())
    assert again["changed"] is False
    assert [t["id"] for s in sections for t in s["topics"]] == ids_before


def test_pull_keeps_topics_referenced_by_questions(tree_dir):
    curriculum = {
        "必修一": {
            "1. 测试章": [
                {"name": "1.1 第一节", "topics": [{"id": "k_used", "name": "被引用考向"}]}
            ]
        }
    }
    report = kt.sync_from_txt(curriculum, protected={"k_used"})
    assert report["carried_over"] == 1
    names = [t["name"] for t in curriculum["必修一"]["1. 测试章"][0]["topics"]]
    assert "被引用考向" in names


def test_push_writes_txt_and_txt_wins_after_edit(tree_dir):
    curriculum = {"必修一": {"1. 测试章": []}}
    kt.sync_from_txt(curriculum, protected=set())
    original = (tree_dir / "必修一 第一章 测试章.txt").read_text(encoding="utf-8")

    # 网页侧改名 + 新增考向
    section = curriculum["必修一"]["1. 测试章"][0]
    section["topics"][0]["name"] = "甲考向（已改名）"
    section["topics"].append(
        {
            "id": topic_id_for("必修一", "1. 测试章", "1.1 第一节", "新增考向"),
            "name": "新增考向",
            "level": "L2",
            "no": 4,
            "example": "例：新示例。",
        }
    )
    pushed = kt.sync_to_txt(curriculum)
    assert pushed["changed"] is True
    written = (tree_dir / "必修一 第一章 测试章.txt").read_text(encoding="utf-8")
    assert "甲考向（已改名）" in written and "新增考向" in written
    assert written != original

    # 再推一次不应重复落盘
    assert kt.sync_to_txt(curriculum)["changed"] is False

    # TXT 现在已是新内容 → 拉取应判定一致
    assert kt.sync_from_txt(curriculum, protected=set())["changed"] is False


def test_sync_to_txt_follows_indent_style(tree_dir):
    """纯缩进风格：顶层沿用 ├─/└─，更深层用两个空格递进。"""

    path = tree_dir / "必修一 第一章 测试章.txt"
    plain = (
        "第一章 测试章\n│\n├─ 1.1 第一节\n  L1 概念理解\n    考向1：甲考向\n"
        "  L2 基础运算\n└─ 1.2 第二节\n"
    )
    path.write_text(plain, encoding="utf-8")
    curriculum = {"必修一": {"1. 测试章": []}}
    kt.sync_from_txt(curriculum, protected=set())
    # 内容没变时不应重写用户的文件
    assert kt.sync_to_txt(curriculum)["changed"] is False

    # 网页侧新增考向 → 回写，并保持纯缩进风格
    curriculum["必修一"]["1. 测试章"][0]["topics"].append(
        {"id": "k_new", "name": "新考向", "level": "L2", "no": 2}
    )
    assert kt.sync_to_txt(curriculum)["changed"] is True
    rendered = path.read_text(encoding="utf-8")
    lines = rendered.splitlines()
    assert lines[2].startswith(("├─", "└─")) and "1.1 第一节" in lines[2]
    assert "  L1 概念理解" in rendered
    assert "    考向1：甲考向" in rendered


def test_pull_orders_topics_by_level(tree_dir):
    """无论 TXT 里怎么写，落到大纲里的考向都按 L1→L5 归组。"""

    path = tree_dir / "必修一 第一章 测试章.txt"
    path.write_text(
        "第一章 测试章\n│\n└─ 1.1 第一节\n   ├─ L3 性质应用\n   │  └─ 考向1：丙\n   └─ L1 概念理解\n      └─ 考向2：甲\n",
        encoding="utf-8",
    )
    curriculum = {"必修一": {"1. 测试章": []}}
    kt.sync_from_txt(curriculum, protected=set())
    topics = curriculum["必修一"]["1. 测试章"][0]["topics"]
    assert [(t["level"], t["name"]) for t in topics] == [("L1", "甲"), ("L3", "丙")]


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


def test_project_tree_is_read_only_under_pytest():
    """跑测试时绝不能改写项目里真实的考向树 TXT。"""

    assert kt.IS_TESTING is True
    assert kt._txt_writes_allowed() is False
    report = kt.sync_to_txt(
        {"必修一": {"1. 集合与常用逻辑用语": [{"name": "x", "topics": []}]}}
    )
    assert report["changed"] is False
    assert all(f["action"] in ("read-only", "unmatched") for f in report["files"])


def test_status_reports_mismatch(tree_dir):
    curriculum = {"必修一": {"1. 测试章": []}}
    info = kt.status(curriculum)
    assert info["count"] == 1
    item = info["files"][0]
    assert item["matched"] is True
    assert item["in_sync"] is False
    assert item["txt_topics"] == 4
    assert item["web_topics"] == 0


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


def test_kaoxiang_status_endpoint(client):
    response = client.get("/api/kaoxiang/status")
    assert response.status_code == 200
    payload = response.json()
    assert "files" in payload and isinstance(payload["files"], list)
    for item in payload["files"]:
        assert "filename" in item and "in_sync" in item


def test_kaoxiang_sync_endpoint_rejects_bad_direction(client):
    from mathbank.auth import LOCAL_TOKEN

    response = client.post(
        "/api/kaoxiang/sync",
        json={"direction": "whatever"},
        headers={"X-Local-Token": LOCAL_TOKEN},
    )
    assert response.status_code == 400


def test_kaoxiang_sync_endpoint_pull_is_safe(client):
    """带令牌的拉取请求可以正常返回，且不改动章节结构。"""

    from mathbank.auth import LOCAL_TOKEN

    response = client.post(
        "/api/kaoxiang/sync",
        json={"direction": "pull"},
        headers={"X-Local-Token": LOCAL_TOKEN},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["direction"] == "pull"
    assert "status" in payload
