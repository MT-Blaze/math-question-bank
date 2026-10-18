"""Shared metadata cache, curriculum topic helpers and startup loader.

This module owns the authoritative in-memory ``METADATA_CACHE`` together with
the helpers that read, decorate and resolve question topics against the active
curriculum. It was extracted from ``main.py`` so that route modules
(``mathbank/routes_*.py``) can depend on the metadata state without importing
the root ``main`` module (which would re-create a root <-> package cycle).

The on-disk metadata lives in ``data_backup/custom_metadata.json`` and is
loaded once at startup by :func:`load_or_init_metadata` (called from
``main.py``). ``get_active_version_code`` and the topic helpers always read
the live cache via :func:`get_metadata_cache`.
"""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy

from fastapi import BackgroundTasks

from mathbank.asset_security import write_private_text_atomic
from mathbank.curriculums import (
    CURRICULUM_NAMES,
    DEFAULT_APPLICABLE_GRADES,
    DEFAULT_DIFFICULTIES,
    build_default_metadata,
    load_curriculum,
    set_current_curriculum,
)
from mathbank.paths import DATA_BACKUP_DIR, IS_TESTING
from mathbank.sync_helper import export_database_to_files
from mathbank.topics_helper import (
    normalize_topic_list,
    parse_extra_topics,
    topic_id_for,
    topic_id_for_slot,
    topic_id_to_name,
)


METADATA_FILE = str(DATA_BACKUP_DIR / ("custom_metadata_test.json" if IS_TESTING else "custom_metadata.json"))

# Canonical, process-wide metadata dictionary. Access only through
# get_metadata_cache() / set_metadata_cache() so every module (including the
# routes that mutate it) shares the same object reference.
METADATA_CACHE: dict = {}
_PENDING_AUTO_TOPICS: list = []
# mtime of the on-disk metadata file at the moment it was last loaded. Used to
# hot-reload the cache when the file is edited outside the app (e.g. by a
# repair script) without restarting the server.
_METADATA_FILE_MTIME = 0


def _metadata_file_mtime_ns() -> int:
    """Nanosecond mtime (Windows truncates ``getmtime`` to whole seconds)."""

    try:
        return os.stat(METADATA_FILE).st_mtime_ns
    except OSError:
        return 0


def _record_metadata_mtime() -> None:
    global _METADATA_FILE_MTIME
    _METADATA_FILE_MTIME = _metadata_file_mtime_ns()


def get_metadata_cache() -> dict:
    """Return the live metadata cache dictionary."""

    return METADATA_CACHE


def set_metadata_cache(value: dict) -> None:
    """Replace the live metadata cache and refresh the active curriculum."""

    global METADATA_CACHE
    METADATA_CACHE = value
    set_current_curriculum(METADATA_CACHE.get("curriculum"))


# === TOPIC_ID_HELPERS ===


def _topic_display(topic_id: str) -> str:
    """Translate a stored topic ID to its current display name."""

    return topic_id_to_name(METADATA_CACHE.get("curriculum", {}), topic_id)


def _decorate_topic_field(item: dict) -> dict:
    """Rename serialized topic_id -> topic (current display name)."""

    tid = str(item.get("topic_id") or "")
    item["topic"] = _topic_display(tid)
    return item


def _decorate_topic_slots(slots) -> list:
    """Add display names to question_topics slot dicts (keeps topic_id)."""

    for slot in slots:
        tid = str(slot.get("topic_id") or "")
        slot["topic"] = _topic_display(tid)
    return slots


# When enabled (default) a kaoxiang can only be auto-created under a chapter
# that already owns that exact kaoxiang in the canonical preset. This stops an
# AI-inferred or mistyped topic (e.g. a probability kaoxiang filed under 复数)
# from being permanently grafted into an unrelated section and then offered in
# every section dropdown. Set MATHBANK_STRICT_AUTO_TOPICS=0 to fall back to the
# legacy permissive behaviour.
STRICT_AUTO_TOPIC_CREATION = os.environ.get(
    "MATHBANK_STRICT_AUTO_TOPICS", "1"
).lower() not in ("0", "false", "no", "off")


def _iter_canonical_curriculums():
    """Yield the pristine preset trees, active version first.

    Every version is consulted because switching textbooks keeps questions from
    older versions around; a slot that is canonical in any version is a real
    slot, so a version switch must not turn a valid kaoxiang into a rejected
    one.
    """

    codes = list(CURRICULUM_NAMES)
    try:
        active = get_active_version_code()
    except Exception:
        active = "A"
    if active in codes:
        codes.remove(active)
        codes.insert(0, active)
    for code in codes:
        try:
            yield load_curriculum(code)
        except ValueError:
            continue


def _is_canonical_section(compulsory: str, chapter: str, knowledge: str) -> bool:
    """True when the (册次/章节/小节) slot exists in the canonical preset.

    The canonical preset is the pristine tree shipped in
    ``mathbank/resources/curriculums/<version>.json``; it is used as the
    whitelist for auto-created topics so a mistyped or AI-inferred kaoxiang can
    never be written into an unrelated section.
    """

    target = str(knowledge or "").strip()
    for canonical in _iter_canonical_curriculums():
        chapters = canonical.get(compulsory)
        if not isinstance(chapters, dict):
            continue
        for section in chapters.get(chapter, []) or []:
            if isinstance(section, dict) and str(section.get("name") or "").strip() == target:
                return True
    return False


def _canonical_chapter_topic_names(compulsory: str, chapter: str) -> set[str]:
    """All kaoxiang names the canonical preset owns under one 章节.

    Used as the whitelist for auto-created topics: a name that does not belong
    to the chapter is a mis-inference, not a new kaoxiang.
    """

    names: set[str] = set()
    for canonical in _iter_canonical_curriculums():
        chapters = canonical.get(compulsory)
        if not isinstance(chapters, dict):
            continue
        for section in chapters.get(chapter, []) or []:
            if not isinstance(section, dict):
                continue
            for topic in section.get("topics", []) or []:
                if isinstance(topic, dict):
                    name = str(topic.get("name") or "").strip()
                else:
                    name = str(topic or "").strip()
                if name:
                    names.add(name)
    return names


def _resolve_topic_id(compulsory: str, chapter: str, knowledge: str, name: str) -> str:
    """Return the topic ID for a slot; auto-create unknown topics.

    Auto-creation is best-effort. When the target section also exists in a
    canonical preset, the kaoxiang name must belong to that chapter, so a
    mis-inferred kaoxiang (for example a probability topic filed under 复数)
    can never be written into an unrelated section and then offered in every
    section dropdown. User-authored sections stay freely extensible.
    """

    clean = str(name or "").strip()
    if not clean:
        return ""
    curriculum = METADATA_CACHE.get("curriculum", {})
    tid = topic_id_for_slot(curriculum, compulsory, chapter, knowledge, clean)
    if tid:
        return tid
    if _is_canonical_section(compulsory, chapter, knowledge):
        # Known (preset-owned) section: the kaoxiang must belong to that chapter,
        # otherwise an AI-inferred or mistyped name would be grafted into an
        # unrelated section and then offered in every section dropdown.
        if STRICT_AUTO_TOPIC_CREATION:
            allowed = _canonical_chapter_topic_names(compulsory, chapter)
            if allowed and clean not in allowed:
                print(
                    "[Metadata] Skipped auto topic creation (kaoxiang does not "
                    f"belong to this chapter): {compulsory} / {chapter} / {clean}"
                )
                return ""
    # Unknown section: a user-authored branch of the tree, so extending it is
    # intentional and stays allowed.

    tid = topic_id_for(compulsory, chapter, knowledge, clean)
    _PENDING_AUTO_TOPICS.append((compulsory, chapter, knowledge, clean, tid))
    return tid


def _resolve_extra_topic_ids(raw_extra: str) -> list:
    """Attach resolved topic IDs to every extra kaoxiang slot."""

    slots = parse_extra_topics(raw_extra)
    for slot in slots:
        slot["topic_id"] = _resolve_topic_id(
            slot["compulsory"], slot["chapter"], slot["knowledge"], slot["topic"]
        )
    return slots


def _flush_auto_topics() -> None:
    """Best-effort write-back of auto-created topics into the curriculum."""

    if not _PENDING_AUTO_TOPICS:
        return
    pending = _PENDING_AUTO_TOPICS[:]
    _PENDING_AUTO_TOPICS.clear()
    try:
        curriculum = METADATA_CACHE.setdefault("curriculum", {})
        changed = False
        for comp, chap, know, name, tid in pending:
            chapters = curriculum.setdefault(comp, {})
            sections = chapters.setdefault(chap, [])
            section = next(
                (s for s in sections if isinstance(s, dict) and s.get("name") == know),
                None,
            )
            if section is None:
                section = {"name": know, "topics": []}
                sections.append(section)
            topics = section.setdefault("topics", [])
            if any(
                isinstance(t, dict)
                and (t.get("id") == tid or t.get("name") == name)
                for t in topics
            ):
                continue
            topics.append({"id": tid, "name": name})
            changed = True
        if changed:
            write_private_text_atomic(
                METADATA_FILE,
                json.dumps(METADATA_CACHE, ensure_ascii=False, indent=2),
            )
    except Exception as exc:
        print(
            f"[Metadata] Auto topic write-back failed "
            f"(type={type(exc).__name__})."
        )


# === TOPIC_ID_HELPERS_END ===


def _is_plain_value(value) -> bool:
    """Whether a curriculum extra field can be safely round-tripped as JSON."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, (list, dict)):
        try:
            json.dumps(value, ensure_ascii=False)
            return True
        except (TypeError, ValueError):
            return False
    return False


def _normalize_topics_keep_extras(topics, compulsory, chapter, knowledge):
    """``normalize_topic_list`` 只保留 id/name，考向树还需要 level/no/example。

    这些字段是「节 → L1~L5 → 考向」四维结构的组成部分，丢失后设置页保存会
    把层级信息抹平，因此这里逐项回填原始字典里的附加字段。
    """

    normalized = normalize_topic_list(topics, compulsory, chapter, knowledge)
    originals = [t for t in (topics or []) if isinstance(t, dict)]
    if len(originals) != len(normalized):
        # 有空名条目被丢弃，无法一一对应，保守起见只返回规范化的结果。
        return normalized
    for target, original in zip(normalized, originals):
        for key, value in original.items():
            if key in ("id", "name") or not _is_plain_value(value):
                continue
            target[key] = value
    return normalized


def normalize_metadata_curriculum(curriculum):
    """Normalize curriculum sections: bare string leaves become {name, topics}.

    Returns (normalized_curriculum, modified). Used by both metadata loading
    (self-heal) and metadata saving so the on-disk and in-memory structures
    stay consistent.
    """

    if not isinstance(curriculum, dict):
        return curriculum, False
    mappings = {
        "选择性必修一": "选修一",
        "选择性必修二": "选修二",
        "选择性必修三": "选修三",
        "必修第一册": "必修一",
        "必修第二册": "必修二",
        "必修第三册": "必修三",
        "必修第四册": "必修四",
    }
    modified = False
    new_curriculum = {}
    for comp, chapters in curriculum.items():
        mapped_comp = mappings.get(comp, comp)
        if mapped_comp != comp:
            modified = True
        if isinstance(chapters, dict):
            normalized_chapters = {}
            for chapter, sections in chapters.items():
                if isinstance(sections, list):
                    normalized_sections = []
                    for section in sections:
                        if isinstance(section, str):
                            normalized_sections.append({"name": section, "topics": []})
                            modified = True
                        elif isinstance(section, dict) and "name" in section:
                            topics = section.get("topics")
                            if isinstance(topics, list) and any(
                                not isinstance(t, dict) or not str(t.get("id") or "").strip()
                                for t in topics
                            ):
                                modified = True
                            normalized_section = {
                                "name": section["name"],
                                "topics": _normalize_topics_keep_extras(
                                    topics, mapped_comp, chapter, section["name"]
                                ),
                            }
                            # 节的附加字段（考向树的分组、L1~L5 标题）也要原样保留
                            for extra_key, extra_value in section.items():
                                if extra_key in ("name", "topics"):
                                    continue
                                if _is_plain_value(extra_value):
                                    normalized_section[extra_key] = extra_value
                            normalized_sections.append(normalized_section)
                        else:
                            normalized_sections.append({"name": str(section), "topics": []})
                            modified = True
                    normalized_chapters[chapter] = normalized_sections
                else:
                    normalized_chapters[chapter] = sections
            new_curriculum[mapped_comp] = normalized_chapters
        else:
            new_curriculum[mapped_comp] = chapters
    return new_curriculum, modified


def load_or_init_metadata():
    """Load custom metadata from disk, self-healing old formats, or initialize.

    Populates the live cache (and the active curriculum mirror) as a side
    effect. Safe to call once at startup.
    """

    global METADATA_CACHE
    default_metadata = build_default_metadata("A")

    # Ensure backup directory exists
    os.makedirs(os.path.dirname(METADATA_FILE), exist_ok=True)

    if os.path.exists(METADATA_FILE):
        try:
            with open(METADATA_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                # Verify schema
                if (
                    isinstance(loaded, dict)
                    and "applicable_grades" in loaded
                    and "question_types" in loaded
                    and "difficulties" in loaded
                    and "curriculum" in loaded
                ):
                    # Self-heal metadata file: migrate old difficulty tiers to the
                    # new 4-tier set, ensure applicable_grades, and normalize the
                    # curriculum leaves from bare strings to {name, topics}.
                    modified = False

                    # 1. Difficulties: old 4-tier -> L1/L2/L3/L4
                    current_difficulty_values = {
                        d.get("value") for d in loaded.get("difficulties", [])
                    }
                    if current_difficulty_values & {
                        "easy_error",
                        "normal",
                        "challenge",
                        "qiangji",
                    }:
                        loaded["difficulties"] = deepcopy(DEFAULT_DIFFICULTIES)
                        modified = True

                    # 2. applicable_grades: 5-tier -> 3-tier (高一/高二/高三)
                    _grade_values = {
                        g.get("value")
                        for g in loaded.get("applicable_grades", [])
                        if isinstance(g, dict)
                    }
                    if (
                        not isinstance(loaded.get("applicable_grades"), list)
                        or not loaded["applicable_grades"]
                        or bool(
                            _grade_values
                            & {
                                "g1_sync",
                                "g2_sync",
                                "g3_round1",
                                "g3_round2",
                                "g3_sprint",
                            }
                        )
                    ):
                        loaded["applicable_grades"] = deepcopy(
                            DEFAULT_APPLICABLE_GRADES
                        )
                        modified = True

                    curriculum = loaded.get("curriculum", {})
                    new_curriculum, norm_modified = normalize_metadata_curriculum(
                        curriculum
                    )
                    modified = modified or norm_modified
                    if modified:
                        loaded["curriculum"] = new_curriculum
                        try:
                            write_private_text_atomic(
                                METADATA_FILE,
                                json.dumps(loaded, ensure_ascii=False, indent=2),
                            )
                            print(
                                f"[Metadata Self-Heal] Upgraded {METADATA_FILE} "
                                "with applicable grades and new difficulty tiers."
                            )
                        except Exception as e:
                            print(
                                f"[Metadata Self-Heal Error] Failed to write "
                                f"updated metadata: {e}"
                            )

                    METADATA_CACHE = loaded
                    set_current_curriculum(METADATA_CACHE.get("curriculum"))
                    _record_metadata_mtime()
                    print(f"[Metadata] Loaded custom metadata from {METADATA_FILE}")
                    return
        except Exception as e:
            print(
                f"[Metadata Warning] Error loading {METADATA_FILE}: {e}. "
                "Overwriting with default."
            )

    # Self-heal / initialize
    try:
        write_private_text_atomic(
            METADATA_FILE,
            json.dumps(default_metadata, ensure_ascii=False, indent=2),
        )
        print(f"[Metadata] Initialized default metadata at {METADATA_FILE}")
    except Exception as e:
        print(f"[Metadata Error] Could not write default metadata: {e}")

    METADATA_CACHE = default_metadata
    set_current_curriculum(METADATA_CACHE.get("curriculum"))
    _record_metadata_mtime()


def reload_metadata_if_changed(force: bool = False) -> bool:
    """Hot-reload the cache when ``custom_metadata.json`` changed on disk.

    The cache is normally filled once at startup, so repairing the file outside
    the app (a cleanup script removing misplaced kaoxiang, a manual edit) used
    to require a full server restart before the UI picked it up. Read-only
    endpoints call this so the running server refreshes on the next request.
    Returns True when a reload actually happened.
    """

    global _METADATA_FILE_MTIME
    if not os.path.exists(METADATA_FILE):
        return False
    mtime = _metadata_file_mtime_ns()
    if mtime == 0:
        return False
    if not force and mtime == _METADATA_FILE_MTIME:
        return False
    try:
        with open(METADATA_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if not (
            isinstance(loaded, dict)
            and "applicable_grades" in loaded
            and "question_types" in loaded
            and "difficulties" in loaded
            and "curriculum" in loaded
        ):
            return False
        normalized, _ = normalize_metadata_curriculum(loaded.get("curriculum", {}))
        loaded["curriculum"] = normalized
        set_metadata_cache(loaded)
        _METADATA_FILE_MTIME = mtime
        print(f"[Metadata] Hot-reloaded custom metadata from {METADATA_FILE}")
        return True
    except Exception as exc:
        print(
            "[Metadata Warning] Hot reload failed "
            f"(type={type(exc).__name__}). Keeping previous cache."
        )
        return False


def get_active_version_code() -> str:
    """Detect the active curriculum version code from the live curriculum tree."""

    curriculum = METADATA_CACHE.get("curriculum", {})
    combined_chapters = ""
    for book_content in curriculum.values():
        if isinstance(book_content, dict):
            combined_chapters += " ".join(book_content.keys())
    if "不等式·比较大小·选填技巧" in curriculum or "集合·复数·逻辑" in curriculum:
        return "G"
    if "第一章" in combined_chapters:
        return "B"
    if (
        "第 1 章 集合与逻辑" in combined_chapters
        or "数学建模活动案例" in combined_chapters
        or "第 2 章 等式与不等式" in combined_chapters
        or "第 3 章 幂、指数与对数" in combined_chapters
    ):
        return "H"
    if "第1章" in combined_chapters:
        return "S"
    return "A"


def schedule_database_export(
    background_tasks: BackgroundTasks, *, operation: str
) -> None:
    """Best-effort export scheduling after a database transaction commits."""

    def run_export_safely() -> None:
        try:
            export_database_to_files(topic_translator=_topic_display)
        except Exception as exc:
            print(
                f"[Database Export] Post-commit export failed for {operation} "
                f"(type={type(exc).__name__}); the next write/startup export can retry."
            )

    try:
        background_tasks.add_task(run_export_safely)
    except Exception as exc:
        print(
            f"[Database Export] Post-commit scheduling failed for {operation} "
            f"(type={type(exc).__name__}); the next write/startup export can retry."
        )


def parse_version_tuple(v_str: str):
    """Parse version string like 'v2.0.1' or '2.0.1' into integer tuple for comparison."""

    if not v_str:
        return (0, 0, 0)
    cleaned = v_str.strip().lstrip("vV").split("-")[0].split("+")[0]
    parts = []
    for p in cleaned.split("."):
        try:
            parts.append(int(re.sub(r"\D", "", p) or "0"))
        except Exception:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])
