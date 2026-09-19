"""Helpers for structured question metadata.

Covers three additive features layered on top of the core ``Question`` model:

* structured multiple-choice ``options`` extraction,
* import-batch bookkeeping (for whole-batch rollback),
* the TikZ version tree.

Everything here is best-effort and side-effect-safe: unusable input degrades to
"do nothing" rather than raising, so it can be called from the create/update
endpoints without risking the main save path.
"""

from __future__ import annotations

import hashlib
import json
import re

from mathbank.database import ImportBatch, TikzVersion

# Option markers like "A．" / "A." / "A、" / "A)" / "A）" at the start of a line.
_OPTION_LINE_RE = re.compile(
    r"(?:^|\n)[ \t]*([A-Ha-h])[ \t]*[．.、\)）:：][ \t]*(.*?)(?=(?:\n[ \t]*[A-Ha-h][ \t]*[．.、\)）:：])|$)",
    re.DOTALL,
)


def extract_structured_options(content: str, question_type: str = "") -> list[dict]:
    """Best-effort extraction of A/B/C/D options from a choice question's stem.

    Returns a list of ``{"label": "A", "content": "..."}``. Non-choice questions
    and stems without recognizable option markers return an empty list, so the
    caller can safely persist the result as-is.
    """

    if question_type not in {"single_choice", "multi_choice"}:
        return []
    text = str(content or "")
    if not text:
        return []
    options: list[dict] = []
    for match in _OPTION_LINE_RE.finditer(text):
        label = match.group(1).strip().upper()
        body = (match.group(2) or "").strip()
        if not body:
            continue
        options.append({"label": label, "content": body})
    # Guard against false positives on prose that merely starts with "A." etc.
    if len(options) < 2:
        return []
    return options


def _load_ids(raw) -> list[int]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    out = []
    for item in parsed:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def record_import_batch_question(db, token, question_id, *, name="", source_name=""):
    """Attach a freshly created question to an import batch (creating it lazily).

    ``token`` is a client-supplied batch identifier; the same token reuses the
    same batch row, so one import task maps to exactly one rollback unit.
    """

    token = str(token or "").strip()
    if not token or question_id is None:
        return None
    batch = db.query(ImportBatch).filter(ImportBatch.token == token).first()
    if batch is None:
        batch = ImportBatch(
            token=token,
            name=str(name or "").strip(),
            source_name=str(source_name or "").strip(),
        )
        db.add(batch)
        db.flush()
    ids = _load_ids(batch.question_ids)
    if question_id not in ids:
        ids.append(int(question_id))
    batch.question_ids = json.dumps(ids)
    batch.question_count = len(ids)
    if name and not batch.name:
        batch.name = str(name).strip()
    if source_name and not batch.source_name:
        batch.source_name = str(source_name).strip()
    return batch


def record_tikz_versions(db, question_id, slots, *, model="", renderer_version=""):
    """Append new TikZ versions for each changed asset.

    ``slots`` maps a slot name (``content`` / ``answer``) to its asset list. An
    asset whose source matches the latest stored version for that slot is
    skipped, so repeated saves do not create noise versions.
    """

    if question_id is None:
        return 0
    created = 0
    for slot_name, assets in (slots or {}).items():
        if not isinstance(assets, list):
            continue
        for index, asset in enumerate(assets):
            if not isinstance(asset, dict):
                continue
            code = str(asset.get("tikz_code") or "").strip()
            if not code:
                continue
            slot_key = f"{slot_name}:{index}"
            source_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
            last = (
                db.query(TikzVersion)
                .filter(
                    TikzVersion.question_id == question_id,
                    TikzVersion.slot == slot_key,
                )
                .order_by(TikzVersion.version.desc())
                .first()
            )
            if last is not None and last.source_hash == source_hash:
                continue
            db.add(
                TikzVersion(
                    question_id=question_id,
                    slot=slot_key,
                    version=(last.version + 1) if last else 1,
                    parent_version_id=(last.id if last else None),
                    code=code,
                    source_hash=source_hash,
                    renderer_version=str(renderer_version or ""),
                    model=str(model or ""),
                )
            )
            created += 1
    return created
