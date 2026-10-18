"""第四录入方案 HTTP 端到端契约测试。

走真实 HTTP 接口跑完整流水线：
    session(multipart) -> plan -> recognize(后台任务) -> risk -> commit
视觉识别与页面渲染被 mock，避免调用外部模型；磁盘写入用临时目录隔离。
锁定前后端字段契约：session_id / preview_hash / riskId+reason / acceptances /
expected_revision / 任务状态轮询接口。
"""

import os
import shutil
import tempfile
import time

import pytest

from mathbank import import_batch


def _token():
    from main import LOCAL_TOKEN

    return LOCAL_TOKEN


@pytest.fixture
def auth_headers():
    return {"X-Local-Token": _token()}


@pytest.fixture
def isolated_dirs(tmp_path, monkeypatch):
    """把批次状态目录与 PDF 暂存目录都指向临时目录，避免污染真实数据。"""

    import mathbank.routes_import_batch as rb

    d = str(tmp_path / "batches")
    os.makedirs(d, exist_ok=True)
    monkeypatch.setattr(rb, "SESSION_DIR", d)
    monkeypatch.setattr(import_batch, "BATCH_DIR", d)
    return d


def test_import_batch_http_flow(client, auth_headers, isolated_dirs, monkeypatch):
    import mathbank.routes_import_batch as rb

    # mock 视觉识别 + 页面渲染，避免真实调用外部模型
    monkeypatch.setattr(rb, "_render_page", lambda *a, **k: b"fake-png")
    monkeypatch.setattr(
        rb,
        "_vision_recognize",
        lambda mode, png, page_no, total_pages: {
            "items": [
                {
                    "stem": f"第{page_no}题 求 $1+1$。",
                    "question_type": "single_choice",
                    "options": ["1", "2"],
                    "analysis": "代入得 $2$。",
                    "paper_question_no": str(page_no),
                    "figures": [],
                }
            ]
        },
    )

    # 1) session (multipart)
    files = {"files": ("a.pdf", b"%PDF-1.4 fake content", "application/pdf")}
    r = client.post(
        "/api/import/batch/session",
        files=files,
        data={"mode": "exam", "title": "HTTP测试卷"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success"
    sid = body["session"]["session_id"]
    assert body["session"]["files"]

    # 在批次目录下放占位 PDF，让"源文件存在"判定通过（render 已被 mock）
    sdir = rb._session_dir(sid)
    os.makedirs(sdir, exist_ok=True)
    with open(os.path.join(sdir, "a.pdf"), "wb") as fh:
        fh.write(b"%PDF-1.4 fake content")

    # 2) plan
    r = client.post(
        "/api/import/batch/plan", json={"session_id": sid}, headers=auth_headers
    )
    assert r.status_code == 200, r.text
    items = r.json()["session"]["items"]
    assert len(items) >= 1

    # 3) recognize -> 后台任务，轮询状态直到完成
    r = client.post(
        "/api/import/batch/recognize", json={"session_id": sid}, headers=auth_headers
    )
    assert r.status_code == 200, r.text
    task_id = r.json()["task_id"]

    deadline = time.time() + 40
    final = None
    while time.time() < deadline:
        snap = client.get(
            f"/api/tasks/{task_id}/status", headers=auth_headers
        ).json()
        if str(snap.get("status")) in ("completed", "failed", "cancelled"):
            final = snap
            break
        time.sleep(0.3)
    assert final is not None, "识别任务未在 40s 内结束"
    assert final["status"] == "completed", final.get("message")

    # 4) risk —— 假数据合法，应无风险
    r = client.get(
        f"/api/import/batch/risk?session_id={sid}", headers=auth_headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["risks"] == []

    # 5) commit —— 取最新 preview_hash 后提交
    detail = client.get(
        f"/api/import/batch/detail?session_id={sid}", headers=auth_headers
    ).json()
    phash = detail["session"]["preview_hash"]
    r = client.post(
        "/api/import/batch/commit",
        json={"session_id": sid, "preview_hash": phash},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "success"
    assert out["count"] >= 1


def test_import_batch_http_flow_with_risk_requires_reason(
    client, auth_headers, isolated_dirs, monkeypatch
):
    """当识别产生风险时：必须逐项填 reason 才能确认，否则提交被拒。"""

    import mathbank.routes_import_batch as rb

    monkeypatch.setattr(rb, "_render_page", lambda *a, **k: b"fake-png")
    monkeypatch.setattr(
        rb,
        "_vision_recognize",
        lambda mode, png, page_no, total_pages: {
            # 空题干 -> 触发 empty_stem 风险
            "items": [{"stem": "", "question_type": "detailed_answer"}]
        },
    )

    files = {"files": ("b.pdf", b"%PDF-1.4 fake content", "application/pdf")}
    r = client.post(
        "/api/import/batch/session",
        files=files,
        data={"mode": "exam", "title": "风险卷"},
        headers=auth_headers,
    )
    sid = r.json()["session"]["session_id"]
    sdir = rb._session_dir(sid)
    os.makedirs(sdir, exist_ok=True)
    with open(os.path.join(sdir, "b.pdf"), "wb") as fh:
        fh.write(b"%PDF-1.4 fake content")

    client.post("/api/import/batch/plan", json={"session_id": sid}, headers=auth_headers)
    r = client.post(
        "/api/import/batch/recognize", json={"session_id": sid}, headers=auth_headers
    )
    task_id = r.json()["task_id"]
    deadline = time.time() + 40
    while time.time() < deadline:
        snap = client.get(
            f"/api/tasks/{task_id}/status", headers=auth_headers
        ).json()
        if str(snap.get("status")) in ("completed", "failed", "cancelled"):
            break
        time.sleep(0.3)

    r = client.get(
        f"/api/import/batch/risk?session_id={sid}", headers=auth_headers
    ).json()
    risks = r["risks"]
    assert risks, "空题干应被识别为风险"
    revision = r["revision"]

    # 不填 reason 直接确认 -> 应失败
    r = client.post(
        "/api/import/batch/risk",
        json={
            "session_id": sid,
            "acceptances": [{"riskId": risks[0]["id"], "reason": "   "}],
            "expected_revision": revision,
        },
        headers=auth_headers,
    )
    assert r.status_code != 200 or r.json().get("status") != "success"

    # 填 reason 确认 -> 成功，随后 commit 成功
    r = client.post(
        "/api/import/batch/risk",
        json={
            "session_id": sid,
            "acceptances": [{"riskId": risks[0]["id"], "reason": "扫描缺页，按现状入库"}],
            "expected_revision": revision,
        },
        headers=auth_headers,
    )
    assert r.status_code == 200 and r.json()["status"] == "success"

    detail = client.get(
        f"/api/import/batch/detail?session_id={sid}", headers=auth_headers
    ).json()
    phash = detail["session"]["preview_hash"]
    r = client.post(
        "/api/import/batch/commit",
        json={"session_id": sid, "preview_hash": phash},
        headers=auth_headers,
    )
    assert r.status_code == 200 and r.json()["status"] == "success"
