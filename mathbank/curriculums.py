"""Curriculum preset loading and metadata defaults.

The four textbook trees live in JSON resources so the backend and browser use
one authoritative copy instead of maintaining independent Python and
JavaScript constants.
"""

from copy import deepcopy
from functools import lru_cache
import json

from mathbank.paths import CURRICULUMS_DIR


CURRICULUM_NAMES = {
    "A": "人教A版",
    "B": "人教B版",
    "S": "苏教版",
    "H": "沪教版",
    "G": "高三专题复习",
}

DEFAULT_QUESTION_TYPES = [
    {"value": "single_choice", "label": "单选题"},
    {"value": "multi_choice", "label": "多选题"},
    {"value": "fill_in_blank", "label": "填空题"},
    {"value": "detailed_answer", "label": "解答题"},
]

DEFAULT_APPLICABLE_GRADES = [
    {"value": "g1", "label": "高一"},
    {"value": "g2", "label": "高二"},
    {"value": "g3", "label": "高三"},
]

DEFAULT_DIFFICULTIES = [
    {
        "value": "L1",
        "label": "L1 概念定义",
        "color": "text-green-600 bg-green-50 border-green-200",
    },
    {
        "value": "L2",
        "label": "L2 基本运算",
        "color": "text-blue-600 bg-blue-50 border-blue-200",
    },
    {
        "value": "L3",
        "label": "L3 性质应用",
        "color": "text-orange-600 bg-orange-50 border-orange-200",
    },
    {
        "value": "L4",
        "label": "L4 综合运用",
        "color": "text-red-600 bg-red-50 border-red-200",
    },
    {
        "value": "L5",
        "label": "L5 创新拓展",
        "color": "text-purple-600 bg-purple-50 border-purple-200",
    },
]


def normalize_version_code(version: str) -> str:
    """Validate and normalize a curriculum version code."""

    code = str(version or "A").strip().upper()
    if code not in CURRICULUM_NAMES:
        raise ValueError(f"不支持的教材大纲版本: {version}")
    return code


@lru_cache(maxsize=len(CURRICULUM_NAMES))
def _load_curriculum_cached(version: str) -> dict:
    code = normalize_version_code(version)
    path = CURRICULUMS_DIR / f"{code}.json"
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"教材大纲资源格式错误: {path}")
    return data


def load_curriculum(version: str = "A") -> dict:
    """Return an isolated copy of one curriculum tree."""

    return deepcopy(_load_curriculum_cached(normalize_version_code(version)))


def build_default_metadata(version: str = "A") -> dict:
    """Build the editable metadata payload used by settings and first boot."""

    code = normalize_version_code(version)
    return {
        "applicable_grades": deepcopy(DEFAULT_APPLICABLE_GRADES),
        "question_types": deepcopy(DEFAULT_QUESTION_TYPES),
        "difficulties": deepcopy(DEFAULT_DIFFICULTIES),
        "curriculum": load_curriculum(code),
    }


def get_curriculum_preset(version: str = "A") -> dict:
    """Return the API representation of a curriculum preset."""

    code = normalize_version_code(version)
    return {
        "version": code,
        "name": CURRICULUM_NAMES[code],
        "metadata": build_default_metadata(code),
    }


def save_curriculum_preset(version: str, curriculum: dict) -> None:
    """Persist a curriculum tree back to its JSON resource and clear cache."""
    code = normalize_version_code(version)
    if not isinstance(curriculum, dict):
        raise ValueError("curriculum must be a dict")
    path = CURRICULUMS_DIR / f"{code}.json"
    with path.open("w", encoding="utf-8") as file:
        json.dump(curriculum, file, ensure_ascii=False, indent=2)
    _load_curriculum_cached.cache_clear()


# Active (possibly user-edited) curriculum tree. main.py populates it at
# startup via set_current_curriculum so route modules can read it without
# depending on root main.py module state.
_CURRENT_CURRICULUM = None


def get_current_curriculum():
    """Return the active curriculum tree.

    Falls back to the 人教A版 preset until the metadata cache is populated at
    startup (the user-edited curriculum takes precedence once loaded).
    """

    if _CURRENT_CURRICULUM is None:
        return load_curriculum("A")
    return _CURRENT_CURRICULUM


def set_current_curriculum(curriculum):
    """Update the active curriculum tree returned by get_current_curriculum."""

    global _CURRENT_CURRICULUM
    _CURRENT_CURRICULUM = curriculum
