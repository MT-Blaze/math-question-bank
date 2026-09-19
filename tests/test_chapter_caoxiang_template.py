# -*- coding: utf-8 -*-
"""章节考向模板回归测试。

范本结构：章 → 节 → L1~L5 难度层 → 考向（序号 + 示例）。
对版本控制内的种子资源 A.json 做结构断言；custom_metadata.json 是用户可
随时编辑的活跃数据，不在此断言，避免用户改大纲就误报。
"""

import json
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
A_JSON = PROJECT_ROOT / "mathbank" / "resources" / "curriculums" / "A.json"

COMP = "必修一"
CHAP = "1. 集合与常用逻辑用语"
EXPECTED_SECTIONS = [
    "1.1 集合的概念",
    "1.2 集合间的基本关系",
    "1.3 集合的基本运算",
    "1.4 充分条件与必要条件",
    "1.5 全称量词与存在量词",
]
LEVELS = {"L1", "L2", "L3", "L4", "L5"}


@pytest.fixture(scope="module")
def chapter():
    data = json.loads(A_JSON.read_text(encoding="utf-8"))
    return data[COMP][CHAP]


def test_sections_match_template(chapter):
    assert [s["name"] for s in chapter] == EXPECTED_SECTIONS


def test_every_caoxiang_has_level_example_and_id(chapter):
    seen_levels = set()
    for sec in chapter:
        topics = sec["topics"]
        assert topics, sec["name"]
        # 考向序号在每节内从 1 连续编号
        assert [t.get("no") for t in topics] == list(range(1, len(topics) + 1))
        for t in topics:
            assert t.get("level") in LEVELS, t
            assert str(t.get("example") or "").strip(), t.get("name")
            assert str(t.get("id") or "").startswith("k_"), t.get("name")
            seen_levels.add(t["level"])
    assert seen_levels == LEVELS


def test_levels_are_grouped_in_order(chapter):
    """同一节内考向按 L1→L5 归组排列（不出现层级交错）。"""

    order = ["L1", "L2", "L3", "L4", "L5"]
    for sec in chapter:
        ranks = [order.index(t["level"]) for t in sec["topics"]]
        assert ranks == sorted(ranks), sec["name"]
