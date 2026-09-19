# -*- coding: utf-8 -*-
"""MinerU 云端解析集成测试：客户端纯函数、设置读写、pdf 任务分支。"""

import os
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# mineru_client 纯函数
# ---------------------------------------------------------------------------

def test_replace_image_refs_basic():
    from mathbank.mineru_client import replace_image_refs

    mapping = {"image_1.png": "/static/uploads/mineru_abc_1.png"}
    md = "![fig](images/image_1.png)\n\n正文内容"
    out = replace_image_refs(md, mapping)
    assert "/static/uploads/mineru_abc_1.png" in out
    assert "images/image_1.png" not in out


def test_replace_image_refs_handles_all_reference_forms():
    from mathbank.mineru_client import replace_image_refs

    mapping = {"pic.png": "/static/uploads/mineru_pic.png"}
    for form in ["pic.png", "./images/pic.png", "images/pic.png", "../images/pic.png"]:
        out = replace_image_refs(f"![a]({form})", mapping)
        assert "/static/uploads/mineru_pic.png" in out


def test_replace_image_refs_keeps_unmatched():
    from mathbank.mineru_client import replace_image_refs

    md = "![a](images/missing.png)"
    out = replace_image_refs(md, {"other.png": "/x/y.png"})
    assert "images/missing.png" in out


def test_replace_image_refs_empty_mapping_is_noop():
    from mathbank.mineru_client import replace_image_refs

    md = "![a](images/x.png)"
    assert replace_image_refs(md, {}) == md


def test_replace_image_refs_empty_markdown():
    from mathbank.mineru_client import replace_image_refs

    assert replace_image_refs("", {"x.png": "/y.png"}) == ""


def test_masked_token():
    from mathbank.mineru_client import masked_token

    with patch.dict(os.environ, {"MINERU_API_TOKEN": "abcdefghij123456"}):
        assert masked_token() == "abcd••••3456"
    with patch.dict(os.environ, {"MINERU_API_TOKEN": ""}):
        assert masked_token() == ""
    with patch.dict(os.environ, {"MINERU_API_TOKEN": "short"}):
        assert masked_token() == "••••"


def test_stage_images_writes_into_static(tmp_path):
    from mathbank.mineru_client import STATIC_DIR, stage_images

    # 上传目录必须是 STATIC_DIR 的子目录，staged 文件才能被静态服务正确映射。
    upload_dir = STATIC_DIR / "uploads" / "mineru_test_staging"
    try:
        src = tmp_path / "src.png"
        src.write_bytes(b"123")
        mapping = stage_images([str(src)], upload_dir, prefix="mineru")

        assert "src.png" in mapping
        url = mapping["src.png"]
        assert url.startswith("/static/uploads/")
        target = STATIC_DIR / url[len("/static/"):]
        assert target.exists()
    finally:
        staged = list(upload_dir.glob("mineru_*")) if upload_dir.exists() else []
        for f in staged:
            f.unlink(missing_ok=True)
        if upload_dir.exists():
            upload_dir.rmdir()


# ---------------------------------------------------------------------------
# settings 接口：MinerU Token 读写
# ---------------------------------------------------------------------------

def test_settings_get_exposes_mineru_fields(client):
    response = client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()
    assert "mineru_token" in data
    assert "mineru_api_base" in data


def test_settings_save_roundtrip_mineru_token(client, tmp_path, monkeypatch):
    from mathbank import routes_settings

    env_file = tmp_path / ".env"
    monkeypatch.setattr(routes_settings, "ENV_FILE", env_file)

    from main import LOCAL_TOKEN

    payload = {
        "deepseek_key": "",
        "mineru_token": "mineru_real_token_123",
        "mineru_api_base": "https://mineru.net/api/v4",
    }
    resp = client.post(
        "/api/settings/save",
        data=payload,
        headers={"X-Local-Token": LOCAL_TOKEN},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"

    # 返回的设置不应回显明文，且应包含掩码
    settings = client.get("/api/settings").json()
    assert "••••" in settings["mineru_token"]
    assert settings["mineru_token"].startswith("mine")
    assert settings["mineru_api_base"] == "https://mineru.net/api/v4"

    # .env 中应落盘真实 Token
    assert "MINERU_API_TOKEN=mineru_real_token_123" in env_file.read_text(encoding="utf-8")
    assert "MINERU_API_BASE=https://mineru.net/api/v4" in env_file.read_text(encoding="utf-8")


def test_mineru_test_endpoint_unconfigured(client):
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MINERU_API_TOKEN", None)
        resp = client.get("/api/mineru/test")
    assert resp.status_code == 200
    assert resp.json()["status"] == "error"


# ---------------------------------------------------------------------------
# pdf 任务分支：mineru_cloud 调度
# ---------------------------------------------------------------------------

def test_pdf_task_dispatches_to_mineru_branch():
    from main import DOCUMENT_TASKS, run_pdf_parsing_task
    from mathbank import mineru_client

    with patch.object(mineru_client, "is_configured", return_value=True), \
         patch.object(
             mineru_client,
             "parse_pdf",
             return_value={"markdown": "# 试卷标题\n\n题干内容", "image_paths": []},
         ), \
         patch.object(mineru_client, "stage_images", return_value={}), \
         patch.object(mineru_client, "replace_image_refs", side_effect=lambda md, m: md), \
         patch("main.parse_paper_text_internal", return_value=[{"content": "题目一"}]), \
         patch("main.post_process_pdf_parsed_questions", return_value=[{"content": "题目一"}]):
        task_id = "mineru-dispatch-1"
        DOCUMENT_TASKS.create(task_id, status="pending", log="", document_type="pdf", temp_assets=[])
        run_pdf_parsing_task(task_id, b"%PDF-1.4 fake", "test.pdf", False, None, "mineru_cloud")

        state = DOCUMENT_TASKS.snapshot(task_id)
        assert state is not None
        assert state["status"] == "completed"
        assert state["data"] == [{"content": "题目一"}]


def test_mineru_branch_fails_without_token():
    from main import DOCUMENT_TASKS, run_mineru_pdf_parsing_task

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MINERU_API_TOKEN", None)
        task_id = "mineru-notoken-1"
        DOCUMENT_TASKS.create(task_id, status="pending", log="", document_type="pdf", temp_assets=[])
        run_mineru_pdf_parsing_task(task_id, b"%PDF-1.4 fake", "test.pdf", False)

        state = DOCUMENT_TASKS.snapshot(task_id)
        assert state is not None
        assert state["status"] == "error"
        assert "MinerU" in (state.get("error") or "")


# ---------------------------------------------------------------------------
# 整页原图：MinerU 分支同样必须渲染
# ---------------------------------------------------------------------------

def _build_two_page_pdf(path):
    import pymupdf as fitz

    document = fitz.open()
    for _ in range(2):
        document.new_page(width=200, height=300)
    document.save(str(path))
    document.close()


def test_render_pdf_page_images_writes_files_and_registers_them(tmp_path):
    """整页原图必须落盘、登记为临时资产，并写进任务快照的 page_images。"""

    from main import DOCUMENT_TASKS
    from mathbank.routes_paper import _render_pdf_page_images

    pdf_path = tmp_path / "paper.pdf"
    _build_two_page_pdf(pdf_path)
    tmp_upload = tmp_path / "tmp"
    tmp_upload.mkdir()

    task_id = "render-pages-1"
    temp_assets: list = []
    DOCUMENT_TASKS.create(
        task_id, status="pending", log="", document_type="pdf", temp_assets=[]
    )

    with patch("main.TMP_UPLOAD_DIR", str(tmp_upload)):
        page_paths, page_urls = _render_pdf_page_images(pdf_path, task_id, temp_assets)

    assert [os.path.basename(u) for u in page_urls] == [
        f"pdf_page_{task_id}_0.png",
        f"pdf_page_{task_id}_1.png",
    ]
    for url in page_urls:
        assert (tmp_upload / os.path.basename(url)).is_file()
    # 登记为临时资产 → 任务存续期间不会被孤儿清理误删
    assert temp_assets == page_urls
    assert page_paths == [str(tmp_upload / os.path.basename(u)) for u in page_urls]
    assert DOCUMENT_TASKS.snapshot(task_id)["page_images"] == page_urls


def test_mineru_branch_renders_full_page_images(tmp_path):
    """MinerU 分支也必须产出整页原图。

    少了它，MinerU 路径下「手动裁图」（manual_crop_pdf 直接读 pdf_page_*.png）
    会必然失败，拆完题之后也无从对照原页核对「这道题到底有没有图」。
    """

    import main as main_module
    from main import DOCUMENT_TASKS, run_pdf_parsing_task
    from mathbank import mineru_client

    pdf_path = tmp_path / "paper.pdf"
    _build_two_page_pdf(pdf_path)
    tmp_upload = tmp_path / "tmp"
    tmp_upload.mkdir()

    with patch("main.TMP_UPLOAD_DIR", str(tmp_upload)), \
         patch.object(mineru_client, "is_configured", return_value=True), \
         patch.object(
             mineru_client,
             "parse_pdf",
             return_value={"markdown": "# 试卷标题\n\n题干内容", "image_paths": []},
         ), \
         patch.object(mineru_client, "stage_images", return_value={}), \
         patch.object(mineru_client, "replace_image_refs", side_effect=lambda md, m: md), \
         patch("main.parse_paper_text_internal", return_value=[{"content": "题目一"}]), \
         patch("main.post_process_pdf_parsed_questions", return_value=[{"content": "题目一"}]):
        task_id = "mineru-render-pages-1"
        DOCUMENT_TASKS.create(
            task_id, status="pending", log="", document_type="pdf", temp_assets=[]
        )
        run_pdf_parsing_task(
            task_id, pdf_path.read_bytes(), "test.pdf", False, None, "mineru_cloud"
        )

        state = DOCUMENT_TASKS.snapshot(task_id)
        assert state["status"] == "completed"
        assert [os.path.basename(u) for u in state["page_images"]] == [
            f"pdf_page_{task_id}_0.png",
            f"pdf_page_{task_id}_1.png",
        ]
        # 整页图必须被登记为临时资产，否则会被孤儿清理当垃圾删掉
        # （上传目录前缀在测试态是 static/test_uploads，所以按实际值拼）
        assert (
            f"/{main_module.UPLOAD_DIR_REL}/tmp/pdf_page_{task_id}_0.png"
            in state["temp_assets"]
        )


# ---------------------------------------------------------------------------
# 逐页文本重建：MinerU 路径下「题目来自第几页」的唯一线索
# ---------------------------------------------------------------------------

def test_collect_page_texts_rebuilds_per_page_text(tmp_path):
    """结果包的结构化清单能重建「每页文本」，供来源页推断使用。

    原先只读 ``full.md``，逐页信息整块丢失 → MinerU 路径下无法判断题目来自第几页
    （前端也就没有「源自第 N 页 / 查看原页」入口）。
    """
    import json

    from mathbank import mineru_client

    workdir = tmp_path / "result"
    workdir.mkdir()
    (workdir / "full.md").write_text("# 试卷", encoding="utf-8")
    (workdir / "demo_content_list.json").write_text(
        json.dumps(
            [
                {"type": "text", "text": "第一页三角函数", "page_idx": 0},
                {"type": "text", "text": "第二页立体几何", "page_idx": 1},
                {"type": "image", "img_path": "images/a.jpg", "page_idx": 1},
                {"type": "table", "table_body": "立体几何用到的表格", "page_idx": 1},
                {"type": "text", "text": "这一项没有页码"},
                {"type": "text", "text": "第三页概率统计", "page_idx": 2},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    pages = mineru_client.collect_page_texts(workdir)
    assert len(pages) == 3
    assert "第一页三角函数" in pages[0]
    assert "第二页立体几何" in pages[1]
    assert "立体几何用到的表格" in pages[1]
    assert "第三页概率统计" in pages[2]
    # 缺页码的项被跳过；图片项本身不产出文本（否则文件名会混进重合度计算）
    assert "这一项没有页码" not in "".join(pages)
    assert "a.jpg" not in "".join(pages)

    # 没有清单、或清单损坏时返回空列表：行为退化成改动前，且不得抛错
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "full.md").write_text("只有 markdown", encoding="utf-8")
    assert mineru_client.collect_page_texts(plain) == []

    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "bad_content_list.json").write_text("{不是 json", encoding="utf-8")
    assert mineru_client.collect_page_texts(broken) == []


def test_collect_page_texts_accepts_alternate_page_index_keys(tmp_path):
    """不同 MinerU 版本用的页码键名不同，都必须能认出来。"""
    import json

    from mathbank import mineru_client

    workdir = tmp_path / "alt"
    workdir.mkdir()
    (workdir / "x_content_list.json").write_text(
        json.dumps(
            [{"type": "text", "text": "第二页的内容", "page_no": 1}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    pages = mineru_client.collect_page_texts(workdir)
    assert len(pages) == 2      # 第一页留空，维持页码与索引对齐
    assert pages[0] == ""
    assert "第二页的内容" in pages[1]


def test_mineru_branches_feed_page_texts_into_post_process():
    """MinerU 分支必须把逐页文本传进后处理，否则 `_source_page` 永远缺。

    「算了却不传」是这次最容易犯的接线错误，所以直接从源码锁住调用形态。
    （本地 MinerU 结果包 zip 导入已下线——包里没有源 PDF，渲染不了整页原图、
     也算不出来源页——所以这里只剩云端一条分支。）
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "mathbank" / "routes_paper.py"
    ).read_text(encoding="utf-8")
    assert source.count("mineru_client.collect_page_texts(") == 1
    assert source.count("task_id, page_texts or None") == 1
    # 本地包导入入口已彻底下线：前端单选、后端端点与任务函数都不应再存在。
    assert "mineru-local-task" not in source
    assert "def run_mineru_local_parsing_task(" not in source


def test_mineru_local_zip_import_is_removed():
    """本地 MinerU 结果包（zip）导入已从录入路线中移除。

    用户明确不用这条路；且它与「对照整页原图核对配图」这条能力冲突
    ——zip 里没有源 PDF，`_render_pdf_page_images` 无从渲染，`_source_page` 也恒缺。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index.html").read_text(encoding="utf-8")
    assert 'value="mineru_local"' not in html
    for value in ("native_preferred", "force_ocr", "mineru_cloud"):
        assert f'value="{value}"' in html, value

    import_js = (root / "static" / "js" / "import.js").read_text(encoding="utf-8")
    assert "mineru_local" not in import_js
    # 三条路线统一走 pdf-task，由 pdf_strategy 分派
    assert "'/api/upload/pdf-task'" in import_js
    assert "pdfFormData.append('pdf_strategy', pdfStrategy)" in import_js

    main_py = (root / "main.py").read_text(encoding="utf-8")
    assert "upload_mineru_local_task" not in main_py
    assert "run_mineru_local_parsing_task" not in main_py
