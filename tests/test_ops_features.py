"""Tests for import batches / TikZ versions / structured options / blueprint."""

import io
import json

from PIL import Image

from mathbank.database import ImportBatch, Question, TikzVersion


def _auth_headers():
    from main import LOCAL_TOKEN

    return {"X-Local-Token": LOCAL_TOKEN}


def _distinct_png(color=(10, 20, 30)):
    buffer = io.BytesIO()
    Image.new("RGB", (3, 3), color).save(buffer, "PNG")
    return buffer.getvalue()


def test_extract_structured_options_parses_choice_stem():
    from mathbank.question_metadata import extract_structured_options

    content = "已知集合 $A$，则（  ）\nA．$1$\nB．$2$\nC．$3$\nD．$4$"
    options = extract_structured_options(content, "single_choice")
    assert [o["label"] for o in options] == ["A", "B", "C", "D"]
    assert options[0]["content"] == "$1$"

    # Non-choice questions and stems without markers yield nothing.
    assert extract_structured_options(content, "detailed_answer") == []
    assert extract_structured_options("普通题干，没有选项", "single_choice") == []


def test_api_create_persists_structured_options(client, db_session):
    response = client.post(
        "/api/questions",
        data={
            "content": "下列正确的是（  ）\nA．甲\nB．乙\nC．丙\nD．丁",
            "question_type": "single_choice",
            "difficulty": "L2",
        },
        headers=_auth_headers(),
    )
    assert response.status_code == 200, response.text
    question = db_session.query(Question).order_by(Question.id.desc()).first()
    assert [o["label"] for o in question.to_dict()["options"]] == ["A", "B", "C", "D"]


def test_import_batch_records_and_rolls_back(client, db_session):
    token = "batch-token-1"
    ids = []
    for index in range(2):
        response = client.post(
            "/api/questions",
            data={
                "content": f"批次题 {index}",
                "question_type": "detailed_answer",
                "difficulty": "L2",
                "import_batch": token,
                "import_batch_name": "高二期末卷",
            },
            headers=_auth_headers(),
        )
        assert response.status_code == 200, response.text
        ids.append(response.json()["question"]["id"])

    batch = db_session.query(ImportBatch).filter(ImportBatch.token == token).first()
    assert batch is not None
    assert batch.question_count == 2
    assert sorted(batch.to_dict()["question_ids"]) == sorted(ids)

    listing = client.get("/api/import-batches", headers=_auth_headers())
    assert listing.status_code == 200
    assert any(b["token"] == token for b in listing.json()["batches"])

    rollback = client.post(
        f"/api/import-batches/{batch.id}/rollback", headers=_auth_headers()
    )
    assert rollback.status_code == 200, rollback.text
    assert rollback.json()["deleted"] == 2
    assert db_session.query(Question).filter(Question.id.in_(ids)).count() == 0
    db_session.refresh(batch)
    assert batch.rolled_back == 1
    assert batch.question_count == 0


def test_tikz_version_tree_records_changes(client, db_session):
    headers = _auth_headers()
    image_path = client.post(
        "/api/upload",
        files={"file": ("fig.png", _distinct_png((5, 6, 7)), "image/png")},
        headers=headers,
    ).json()["file_path"]
    tikz_v1 = "\\begin{tikzpicture}\\draw (0,0)--(1,1);\\end{tikzpicture}"
    create = client.post(
        "/api/questions",
        data={
            "content": f"带图题\n\n![TikZ]({image_path})",
            "question_type": "detailed_answer",
            "difficulty": "L2",
            "image_paths": json.dumps([image_path]),
            "content_tikz_assets": json.dumps(
                [{"id": "a", "image_path": image_path, "tikz_code": tikz_v1}],
                ensure_ascii=False,
            ),
        },
        headers=headers,
    )
    assert create.status_code == 200, create.text
    question_id = create.json()["question"]["id"]

    versions = client.get(
        f"/api/questions/{question_id}/tikz-versions", headers=_auth_headers()
    ).json()["versions"]
    assert len(versions) == 1
    assert versions[0]["version"] == 1

    # Same code on update -> no new version (dedup by source hash).
    client.put(
        f"/api/questions/{question_id}",
        data={
            "content": f"带图题\n\n![TikZ]({image_path})",
            "question_type": "detailed_answer",
            "difficulty": "L2",
            "image_paths": json.dumps([image_path]),
            "content_tikz_assets": json.dumps(
                [{"id": "a", "image_path": image_path, "tikz_code": tikz_v1}],
                ensure_ascii=False,
            ),
        },
        headers=headers,
    )
    versions = client.get(
        f"/api/questions/{question_id}/tikz-versions", headers=_auth_headers()
    ).json()["versions"]
    assert len(versions) == 1

    # Changed code -> a second version chained to the first.
    tikz_v2 = "\\begin{tikzpicture}\\draw (0,0)--(2,2);\\end{tikzpicture}"
    client.put(
        f"/api/questions/{question_id}",
        data={
            "content": f"带图题\n\n![TikZ]({image_path})",
            "question_type": "detailed_answer",
            "difficulty": "L2",
            "image_paths": json.dumps([image_path]),
            "content_tikz_assets": json.dumps(
                [{"id": "a", "image_path": image_path, "tikz_code": tikz_v2}],
                ensure_ascii=False,
            ),
        },
        headers=headers,
    )
    versions = client.get(
        f"/api/questions/{question_id}/tikz-versions", headers=_auth_headers()
    ).json()["versions"]
    assert len(versions) == 2
    assert versions[1]["version"] == 2
    assert versions[1]["parent_version_id"] == versions[0]["id"]


def test_blueprint_match_selects_by_type_and_difficulty(client, db_session):
    for index in range(3):
        client.post(
            "/api/questions",
            data={
                "content": f"选择 {index}",
                "question_type": "single_choice",
                "difficulty": "L2",
                "category_knowledge": "集合",
                "difficulty_index": "0.5",
            },
            headers=_auth_headers(),
        )
    response = client.post(
        "/api/questions/blueprint-match",
        json={
            "rows": [
                {
                    "question_type": "single_choice",
                    "knowledge": "集合",
                    "difficulty_min": 0.3,
                    "difficulty_max": 0.7,
                    "count": 2,
                }
            ]
        },
        headers=_auth_headers(),
    )
    assert response.status_code == 200, response.text
    rows = response.json()["rows"]
    assert len(rows) == 1
    assert len(rows[0]["questions"]) == 2
    assert all(q["question_type"] == "single_choice" for q in rows[0]["questions"])


def test_image_upload_dedupes_identical_bytes(client):
    headers = _auth_headers()
    png = _distinct_png((1, 2, 3))
    first = client.post("/api/upload", files={"file": ("a.png", png, "image/png")}, headers=headers)
    second = client.post("/api/upload", files={"file": ("b.png", png, "image/png")}, headers=headers)
    other = client.post(
        "/api/upload", files={"file": ("c.png", _distinct_png((9, 9, 9)), "image/png")}, headers=headers
    )
    assert first.status_code == second.status_code == other.status_code == 200
    assert first.json()["file_path"] == second.json()["file_path"]
    assert first.json()["file_path"] != other.json()["file_path"]
    assert first.json()["sha256"] == second.json()["sha256"]
