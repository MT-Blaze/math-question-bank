import io
import json
import os
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PIL import Image

from main import LOCAL_TOKEN, compile_tikz_to_png
from mathbank.asset_security import (
    AssetSecurityError,
    normalize_answer_tikz_assets,
    normalize_content_tikz_assets,
    normalize_optional_upload_asset_reference,
    normalize_upload_asset_reference,
    resolve_upload_asset,
    write_private_text_atomic,
)
from mathbank.database import Question
from mathbank.paper_helper import compile_tex_to_pdf


def _png_bytes(size=(8, 8)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_private_atomic_write_works_without_unix_fchmod(tmp_path, monkeypatch):
    monkeypatch.delattr("mathbank.asset_security.os.fchmod", raising=False)
    target = tmp_path / "private-token"

    write_private_text_atomic(target, "persistent-value")

    assert target.read_text(encoding="utf-8") == "persistent-value"
    assert not list(tmp_path.glob(".private-token.*"))


class _WinPermissionError(PermissionError):
    """带 winerror 的 PermissionError，模拟 Windows 共享违规。"""

    def __init__(self, winerror: int, message: str = "拒绝访问。"):
        super().__init__(message)
        self.winerror = winerror


def test_replace_retry_survives_transient_winerror5(tmp_path, monkeypatch):
    """读者短暂占用目标文件（瞬时 WinError 5）时，写盘必须靠重试自愈而不是抛错。"""

    from mathbank import asset_security as sec

    target = tmp_path / "custom_metadata.json"
    write_private_text_atomic(target, "old")

    calls = {"n": 0}
    real_replace = os.replace

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _WinPermissionError(5)
        return real_replace(src, dst)

    monkeypatch.setattr(sec.os, "replace", flaky_replace)
    sleeps = []
    monkeypatch.setattr(sec.time, "sleep", lambda s: sleeps.append(s))

    write_private_text_atomic(target, "new")

    assert target.read_text(encoding="utf-8") == "new"
    assert calls["n"] == 3, "应该前两次失败、第三次成功"
    assert len(sleeps) == 2 and all(s > 0 for s in sleeps), sleeps
    # 临时文件不得残留
    assert not list(tmp_path.glob(".custom_metadata.json.*"))


def test_replace_retry_gives_up_and_preserves_original(tmp_path, monkeypatch):
    """持续被占用时按预算放弃：抛出原始错误，旧内容一字不变、无半写、无临时残留。"""

    from mathbank import asset_security as sec

    target = tmp_path / "custom_metadata.json"
    write_private_text_atomic(target, "old")

    attempts = {"n": 0}

    def always_blocked(src, dst):
        attempts["n"] += 1
        raise _WinPermissionError(5)

    monkeypatch.setattr(sec.os, "replace", always_blocked)
    monkeypatch.setattr(sec.time, "sleep", lambda s: None)

    with pytest.raises(_WinPermissionError):
        write_private_text_atomic(target, "new")

    assert attempts["n"] == sec._REPLACE_ATTEMPTS, attempts["n"]
    assert target.read_text(encoding="utf-8") == "old"
    assert not list(tmp_path.glob(".custom_metadata.json.*"))


def test_replace_retry_does_not_mask_other_errors(tmp_path, monkeypatch):
    """非共享冲突类 OSError（winerror 不在白名单）必须立刻原样上抛，不做重试。"""

    from mathbank import asset_security as sec

    target = tmp_path / "custom_metadata.json"
    write_private_text_atomic(target, "old")

    calls = {"n": 0}

    def wrong_disk(src, dst):
        calls["n"] += 1
        raise _WinPermissionError(17, "不同盘符")

    monkeypatch.setattr(sec.os, "replace", wrong_disk)
    monkeypatch.setattr(sec.time, "sleep", lambda s: None)

    with pytest.raises(_WinPermissionError):
        write_private_text_atomic(target, "new")
    assert calls["n"] == 1, "不可重试的错误绝不能进入退避循环"
    assert target.read_text(encoding="utf-8") == "old"


def test_concurrent_readers_never_break_metadata_style_writes(tmp_path):
    """真并发压力：读者线程复刻热重载/后台导出的读取形态（open→read→close，
    每次之间留出让句柄彻底关闭的小间隔），写线程连续原子写 20 次——
    每一次都必须成功，且最终内容是某一次完整写入。

    这是竞态本身的回归：修复前在 Windows 上这条链路会随机 WinError 5。
    刻意用「纯 Python 读」而不是 msvcrt 文件锁来模拟读者——实测纯紧循环读者
    会让任何重试预算都耗尽（GIL 饿死写线程，属人造场景而非生产形态），
    真实读者的持窗是微秒级、按请求节奏出现。
    msvcrt 只在 Windows 存在；非 Windows 平台此用例如实跳过。
    """

    pytest.importorskip("msvcrt")

    target = tmp_path / "custom_metadata.json"
    target.write_text("seed", encoding="utf-8")

    stop_reader = threading.Event()

    def reader():
        # 与 reload_metadata_if_changed / export 同款：open→read→close；
        # 每轮 sleep(0.005) 对应真实世界「按请求/任务出现」的占空比。
        while not stop_reader.is_set():
            try:
                with open(target, "rb") as h:
                    h.read()
            except OSError:
                pass
            time.sleep(0.005)

    t = threading.Thread(target=reader, daemon=True)
    t.start()

    failures = []
    contents = []
    for i in range(20):
        payload = json.dumps({"payload": i}, ensure_ascii=False)
        try:
            write_private_text_atomic(target, payload)
            contents.append(payload)
        except OSError as exc:
            failures.append((i, type(exc).__name__, str(exc)[:80]))
    stop_reader.set()
    t.join(timeout=2)

    assert failures == [], failures[:3]
    final = target.read_text(encoding="utf-8")
    assert final in contents, "最终内容必须是某一次完整写入，不允许半写"
    assert not list(tmp_path.glob(".custom_metadata.json.*")), "临时文件必须被清干净"


def test_upload_asset_resolver_accepts_only_contained_regular_files(tmp_path):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    image_path = upload_dir / "safe.png"
    image_path.write_bytes(_png_bytes())

    resolved = resolve_upload_asset(
        "/static/uploads/safe.png",
        uploads_dir=upload_dir,
        url_prefix="static/uploads",
    )
    assert resolved == image_path.resolve()
    assert normalize_upload_asset_reference(
        "static/uploads/safe.png",
        uploads_dir=upload_dir,
        url_prefix="static/uploads",
    ) == "/static/uploads/safe.png"

    directory_with_image_suffix = upload_dir / "folder.png"
    directory_with_image_suffix.mkdir()
    with pytest.raises(AssetSecurityError, match="普通文件"):
        resolve_upload_asset(
            "/static/uploads/folder.png",
            uploads_dir=upload_dir,
            url_prefix="static/uploads",
        )


def test_upload_asset_resolver_rejects_traversal_and_symlinks(tmp_path):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(_png_bytes())

    with pytest.raises(AssetSecurityError):
        resolve_upload_asset(
            "/static/uploads/../outside.png",
            uploads_dir=upload_dir,
            url_prefix="static/uploads",
        )

    link = upload_dir / "linked.png"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("当前平台不支持测试符号链接")
    with pytest.raises(AssetSecurityError, match="符号链接"):
        resolve_upload_asset(
            "/static/uploads/linked.png",
            uploads_dir=upload_dir,
            url_prefix="static/uploads",
        )


def test_tikz_reference_assets_must_be_registered_with_question_images(tmp_path):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    rendered = upload_dir / "tikz.png"
    reference = upload_dir / "original.png"
    rendered.write_bytes(_png_bytes())
    reference.write_bytes(_png_bytes())
    rendered_url = "/static/uploads/tikz.png"
    reference_url = "/static/uploads/original.png"

    with pytest.raises(AssetSecurityError, match="image_paths"):
        normalize_optional_upload_asset_reference(
            reference_url,
            allowed_image_paths=[rendered_url],
            uploads_dir=upload_dir,
            url_prefix="static/uploads",
        )

    assets = normalize_answer_tikz_assets(
        [{
            "id": "tikz_reference_test",
            "image_path": rendered_url,
            "reference_image_path": reference_url,
            "tikz_code": "\\begin{tikzpicture}\\end{tikzpicture}",
            "instruction": "",
        }],
        allowed_image_paths=[rendered_url, reference_url],
        uploads_dir=upload_dir,
        url_prefix="static/uploads",
    )
    assert assets[0]["reference_image_path"] == reference_url

    content_assets = normalize_content_tikz_assets(
        [{
            "id": "content_tikz_reference_test",
            "image_path": rendered_url,
            "reference_image_path": reference_url,
            "tikz_code": "\\begin{tikzpicture}\\end{tikzpicture}",
            "instruction": "题干示意图",
        }],
        allowed_image_paths=[rendered_url, reference_url],
        uploads_dir=upload_dir,
        url_prefix="static/uploads",
    )
    assert content_assets[0]["instruction"] == "题干示意图"


def test_single_upload_reencodes_real_images_and_rejects_active_content(client, tmp_path):
    headers = {"X-Local-Token": LOCAL_TOKEN}
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()

    with patch("main.UPLOAD_DIR", str(upload_dir)), patch(
        "main.UPLOAD_DIR_REL", "static/uploads"
    ):
        valid = client.post(
            "/api/upload",
            files={"file": ("renamed.html", _png_bytes(), "text/html")},
            headers=headers,
        )
        fake = client.post(
            "/api/upload",
            files={"file": ("payload.html", b"<script>alert(1)</script>", "text/html")},
            headers=headers,
        )

    assert valid.status_code == 200
    stored_url = valid.json()["file_path"]
    assert stored_url.endswith(".png")
    stored_path = upload_dir / Path(stored_url).name
    with Image.open(stored_path) as stored:
        assert stored.format == "PNG"
    assert fake.status_code == 400
    assert list(upload_dir.glob("*.html")) == []


def test_batch_upload_uses_the_same_safe_reencoding(client, tmp_path):
    headers = {"X-Local-Token": LOCAL_TOKEN}
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    polyglot = _png_bytes() + b"<script>active-trailer</script>"

    with patch("main.UPLOAD_DIR", str(upload_dir)), patch(
        "main.UPLOAD_DIR_REL", "static/uploads"
    ):
        response = client.post(
            "/api/upload/batch",
            files=[("files", ("diagram.html", polyglot, "text/html"))],
            headers=headers,
        )

    assert response.status_code == 200
    stored_url = response.json()["mapping"]["diagram.html"]
    stored_path = upload_dir / Path(stored_url).name
    assert stored_path.suffix == ".png"
    assert b"active-trailer" not in stored_path.read_bytes()
    with Image.open(stored_path) as stored:
        assert stored.format == "PNG"


def test_single_ocr_and_pdf_uploads_stop_at_stream_limits(client, tmp_path):
    headers = {"X-Local-Token": LOCAL_TOKEN}

    with patch("main.MAX_SINGLE_IMAGE_BYTES", 8):
        image_response = client.post(
            "/api/upload",
            files={"file": ("large.png", b"123456789", "image/png")},
            headers=headers,
        )
    with patch("main.MAX_OCR_IMAGE_BYTES", 8):
        ocr_response = client.post(
            "/api/ocr",
            files={"file": ("large.png", b"123456789", "image/png")},
            headers=headers,
        )
    with patch("main.MAX_PDF_BYTES", 8):
        pdf_response = client.post(
            "/api/upload/pdf-task",
            files={"file": ("large.pdf", b"%PDF-1234", "application/pdf")},
            headers=headers,
        )

    assert image_response.status_code == 413
    assert ocr_response.status_code == 413
    assert pdf_response.status_code == 413


def test_pdf_upload_rejects_suffix_only_spoof(client):
    response = client.post(
        "/api/upload/pdf-task",
        files={"file": ("fake.pdf", b"not a pdf", "application/pdf")},
        headers={"X-Local-Token": LOCAL_TOKEN},
    )
    assert response.status_code == 400
    assert "不是有效的 PDF" in response.json()["message"]


def test_question_image_paths_reject_traversal_and_legacy_delete_never_unlinks_it(
    client, db_session, tmp_path
):
    headers = {"X-Local-Token": LOCAL_TOKEN}
    upload_dir = tmp_path / "uploads"
    temp_dir = upload_dir / "tmp"
    temp_dir.mkdir(parents=True)
    outside = tmp_path / "outside.png"
    outside.write_bytes(_png_bytes())
    unsafe_reference = "/static/uploads/../outside.png"

    payload = {
        "content": "安全路径测试题",
        "question_type": "single_choice",
        "difficulty": "medium",
        "image_paths": json.dumps([unsafe_reference]),
    }
    with patch("main.UPLOAD_DIR", str(upload_dir)), patch(
        "main.TMP_UPLOAD_DIR", str(temp_dir)
    ), patch("main.UPLOAD_DIR_REL", "static/uploads"):
        rejected = client.post("/api/questions", data=payload, headers=headers)
    assert rejected.status_code == 400
    assert outside.exists()

    legacy = Question(content="旧数据", question_type="single_choice", difficulty="medium")
    legacy.image_paths = [unsafe_reference]
    db_session.add(legacy)
    db_session.commit()
    legacy_id = legacy.id

    with patch("main.UPLOAD_DIR", str(upload_dir)), patch(
        "main.UPLOAD_DIR_REL", "static/uploads"
    ):
        deleted = client.delete(f"/api/questions/{legacy_id}", headers=headers)
    assert deleted.status_code == 200
    assert outside.exists()


def test_xelatex_commands_disable_shell_escape_and_restrict_kpathsea(tmp_path):
    paper_calls = []

    def fail_paper(cmd, *, cwd, **kwargs):
        paper_calls.append((cmd, cwd, kwargs))
        return SimpleNamespace(returncode=1, stdout="failure", stderr="")

    with patch("mathbank.paper_helper.subprocess.run", side_effect=fail_paper):
        pdf_bytes, _ = compile_tex_to_pdf("unique shell escape security test")
    assert pdf_bytes is None
    paper_cmd, paper_cwd, paper_kwargs = paper_calls[0]
    assert "-no-shell-escape" in paper_cmd
    assert paper_kwargs["env"]["openin_any"] == "p"
    assert paper_kwargs["env"]["openout_any"] == "p"
    assert paper_kwargs["env"]["TEXMFOUTPUT"] == os.path.abspath(paper_cwd)

    tikz_calls = []

    def fail_tikz(cmd, **kwargs):
        tikz_calls.append((cmd, kwargs))
        return SimpleNamespace(returncode=1, stdout=b"", stderr=b"")

    with patch("shutil.which", return_value="/usr/bin/xelatex"), patch(
        "subprocess.run", side_effect=fail_tikz
    ), patch("main.UPLOAD_DIR", str(tmp_path)), patch(
        "main.UPLOAD_DIR_REL", "static/uploads"
    ):
        with pytest.raises(RuntimeError, match="编译错误"):
            compile_tikz_to_png("\\begin{tikzpicture}\\end{tikzpicture}")
    tikz_cmd, tikz_kwargs = tikz_calls[0]
    assert "-no-shell-escape" in tikz_cmd
    assert tikz_kwargs["env"]["openin_any"] == "p"
    assert tikz_kwargs["env"]["openout_any"] == "p"


def test_invalid_token_log_does_not_echo_submitted_or_expected_secret(client, capsys):
    submitted = "attacker-supplied-secret"
    response = client.post(
        "/api/settings/save",
        data={"deepseek_key": "unused"},
        headers={"X-Local-Token": submitted},
    )
    captured = capsys.readouterr().out
    assert response.status_code == 403
    assert submitted not in captured
    assert LOCAL_TOKEN not in captured


def test_future_patch_routes_are_also_protected_by_local_token(client):
    blocked = client.patch("/api/not-yet-defined")
    authenticated = client.patch(
        "/api/not-yet-defined", headers={"X-Local-Token": LOCAL_TOKEN}
    )

    assert blocked.status_code == 403
    assert authenticated.status_code == 404


def test_manual_pdf_crop_requires_a_real_uuid_task_and_bounded_coordinates(
    client, tmp_path
):
    import main

    headers = {"X-Local-Token": LOCAL_TOKEN}
    task_id = str(uuid.uuid4())
    upload_dir = tmp_path / "uploads"
    temp_dir = upload_dir / "tmp"
    temp_dir.mkdir(parents=True)
    page_image = temp_dir / f"pdf_page_{task_id}_0.png"
    page_image.write_bytes(_png_bytes((100, 100)))
    main.DOCUMENT_TASKS.create(
        task_id, document_type="pdf", temp_assets=[], status="completed"
    )

    try:
        with patch("main.UPLOAD_DIR", str(upload_dir)), patch(
            "main.TMP_UPLOAD_DIR", str(temp_dir)
        ), patch("main.UPLOAD_DIR_REL", "static/uploads"):
            traversal = client.post(
                "/api/ai/manual-crop-pdf",
                json={"task_id": "../../outside", "page_index": 0},
                headers=headers,
            )
            invalid_box = client.post(
                "/api/ai/manual-crop-pdf",
                json={
                    "task_id": task_id,
                    "page_index": 0,
                    "xmin": 90,
                    "ymin": 10,
                    "xmax": 10,
                    "ymax": 80,
                },
                headers=headers,
            )
            valid = client.post(
                "/api/ai/manual-crop-pdf",
                json={
                    "task_id": task_id,
                    "page_index": 0,
                    "xmin": 10,
                    "ymin": 10,
                    "xmax": 80,
                    "ymax": 80,
                },
                headers=headers,
            )

        assert traversal.status_code == 400
        assert invalid_box.status_code == 400
        assert valid.status_code == 200
        crop_name = Path(valid.json()["image_path"]).name
        assert (temp_dir / crop_name).is_file()
        assert valid.json()["image_path"] in main.DOCUMENT_TASKS.snapshot(task_id)[
            "temp_assets"
        ]
    finally:
        main.DOCUMENT_TASKS.remove(task_id)


def test_cancel_endpoint_does_not_delete_assets_when_worker_wins_race(
    client, monkeypatch
):
    import main

    task_id = str(uuid.uuid4())
    asset = "/static/test_uploads/tmp/completed.png"
    main.DOCUMENT_TASKS.create(
        task_id, status="processing", temp_assets=[asset], document_type="pdf"
    )
    deleted = []
    monkeypatch.setattr(
        main.DOCUMENT_TASKS,
        "cancel",
        lambda _task_id: {"status": "completed", "temp_assets": [asset]},
    )
    monkeypatch.setattr(
        main,
        "_delete_task_temp_assets",
        lambda paths: deleted.extend(paths) or len(paths),
    )

    try:
        response = client.post(
            f"/api/tasks/{task_id}/cancel",
            headers={"X-Local-Token": LOCAL_TOKEN},
        )
        assert response.status_code == 409
        assert response.json()["task_status"] == "completed"
        assert deleted == []
    finally:
        main.DOCUMENT_TASKS.remove(task_id)
