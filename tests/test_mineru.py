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


# ---------------------------------------------------------------------------
# MinerU 装饰图过滤（水印/二维码/页眉条/公式碎片）
# ---------------------------------------------------------------------------

def _save_gray_image(tmp_path, name, width, height, draw_fn):
    """生成一张按坐标回调上色的灰度图，用于精确构造像素特征。"""

    from PIL import Image

    path = tmp_path / name
    image = Image.new("L", (width, height))
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            pixels[x, y] = draw_fn(x, y)
    image.save(path)
    return path


def _qr_like_image(tmp_path):
    # 近正方形 + 深色模块占比高 + 边缘密集
    return _save_gray_image(
        tmp_path, "qr.png", 60, 60, lambda x, y: 0 if ((x // 6 + y // 6) % 2 == 0) else 255
    )


def _watermark_like_image(tmp_path):
    # 大面积、几乎全白、边缘极少
    return _save_gray_image(
        tmp_path,
        "wm.png",
        500,
        500,
        lambda x, y: 248 if 240 <= y < 260 and 200 <= x < 300 else 252,
    )


def _formula_strip_image(tmp_path):
    # 细长条：高度小、宽高比大、边缘丰富
    return _save_gray_image(
        tmp_path,
        "strip.png",
        400,
        40,
        lambda x, y: 0 if (x // 8 + y // 8) % 2 == 0 else 255,
    )


def _parabola_like_image(tmp_path):
    """真实函数图像形态：白底、少量笔画、非细长——必须保留。"""

    def paint(x, y):
        if abs(y - 80) <= 1 or abs(x - 100) <= 1:
            return 120
        curve = int(80 + (x - 100) ** 2 / 120)
        if abs(y - curve) <= 1:
            return 0
        return 255

    return _save_gray_image(tmp_path, "para.png", 200, 160, paint)


def _balance_like_image(tmp_path):
    """天平示意图形态：低边缘 + 白底 + 近方形，容易被误判成水印——必须保留。"""

    def paint(x, y):
        if abs(y - 120) <= 2 and 30 <= x <= 170:
            return 0
        if abs(x - 100) <= 2 and 40 <= y <= 122:
            return 0
        if 60 <= y <= 70 and abs(x - 60) <= 3:
            return 0
        if 60 <= y <= 70 and abs(x - 140) <= 3:
            return 0
        return 255

    return _save_gray_image(tmp_path, "bal.png", 200, 160, paint)


def test_classify_rejects_qr_logo(tmp_path):
    from mathbank.mineru_client import classify_mineru_decoration_image

    assert classify_mineru_decoration_image(str(_qr_like_image(tmp_path))) == "二维码/Logo"


def test_classify_rejects_watermark(tmp_path):
    from mathbank.mineru_client import classify_mineru_decoration_image

    assert classify_mineru_decoration_image(str(_watermark_like_image(tmp_path))) == "水印"


def test_classify_rejects_formula_strip(tmp_path):
    from mathbank.mineru_client import classify_mineru_decoration_image

    reason = classify_mineru_decoration_image(str(_formula_strip_image(tmp_path)))
    assert reason == "页眉页脚/公式条/表格"


def test_classify_keeps_real_figures(tmp_path):
    """抛物线与天平是合法配图：误杀它们比漏掉水印更糟（用户无从补图）。"""

    from mathbank.mineru_client import classify_mineru_decoration_image

    assert classify_mineru_decoration_image(str(_parabola_like_image(tmp_path))) == ""
    assert classify_mineru_decoration_image(str(_balance_like_image(tmp_path))) == ""


def test_classify_is_safe_for_balance_like_low_edge_figure(tmp_path):
    """低边缘但面积不足水印阈值的图形不得被判成水印。

    天平图 200x160=32000 px，低于 ``_WATERMARK_MIN_AREA``；若只按「白底 + 少边缘」
    一刀切，这类图会被整批吃掉。
    """

    from mathbank.mineru_client import classify_mineru_decoration_image

    path = _balance_like_image(tmp_path)
    reason = classify_mineru_decoration_image(str(path))
    assert reason == "", reason


def test_filter_mineru_decoration_images_cleans_markdown_and_keeps_real(tmp_path):
    from mathbank.mineru_client import filter_mineru_decoration_images

    qr = _qr_like_image(tmp_path)
    wm = _watermark_like_image(tmp_path)
    para = _parabola_like_image(tmp_path)
    markdown = (
        "如图 ![](images/para.png) 所示。\n\n"
        "![校徽](images/qr.png)\n\n"
        "![](images/wm.png)\n\n\n正文继续。"
    )
    cleaned, kept = filter_mineru_decoration_images(
        markdown, [str(para), str(qr), str(wm)]
    )
    assert [str(p) for p in kept] == [str(para)]
    assert "images/para.png" in cleaned
    assert "images/qr.png" not in cleaned
    assert "images/wm.png" not in cleaned
    # 清掉引用后不得留下三个连续空行（会打乱后续拆题分段）
    assert "\n\n\n" not in cleaned


def test_filter_mineru_decoration_images_empty_inputs():
    from mathbank.mineru_client import filter_mineru_decoration_images

    assert filter_mineru_decoration_images("", []) == ("", [])
    md = "![](images/x.png)"
    assert filter_mineru_decoration_images(md, []) == (md, [])


# ---------------------------------------------------------------------------
# MinerU 结果缓存：同文件重传不得再烧云额度
# ---------------------------------------------------------------------------

from pathlib import Path  # noqa: E402  （追加段自带，不动文件头部既有导入）


def _cache_env(tmp_path):
    """把缓存根目录指到 tmp，避免测试写进真实 .system_generated。"""

    from mathbank import mineru_cache

    return patch.object(mineru_cache, "cache_root", lambda: tmp_path / "mineru_cache")


def _make_result(workdir):
    """造一个与 MinerU 解压产物同形的 workdir，返回 md 路径。"""

    target = Path(str(workdir))
    (target / "images").mkdir(parents=True, exist_ok=True)
    md_path = target / "full.md"
    md_path.write_text("# 试卷\n\n如图 ![](images/pic.png) 所示", encoding="utf-8")
    (target / "images" / "pic.png").write_bytes(b"png-bytes")
    return md_path


def _fake_cloud_package(target):
    """模拟 download_zip 的解压产物（含逐页清单）。"""

    handle = Path(str(target))
    (handle / "images").mkdir(parents=True, exist_ok=True)
    (handle / "full.md").write_text(
        "# 卷一\n\n设 $f(x)=x^2$，如图 ![](images/a.png)。", encoding="utf-8"
    )
    (handle / "images" / "a.png").write_bytes(bytes(range(32, 64)))
    (handle / "x_content_list.json").write_text(
        '[{"text":"第一页","page_idx":0},{"text":"第二页","page_idx":1}]',
        encoding="utf-8",
    )
    return handle


def test_mineru_cache_roundtrip_rematerialises_into_the_given_workdir(tmp_path):
    """命中后必须把结果复写进**本次** workdir。

    上层结束时会 rmtree(workdir)，直接把缓存目录交出去等于让缓存自我删除。
    """

    from mathbank import mineru_cache

    key = mineru_cache.cache_key(b"%PDF-1.4 a", "vlm", "https://mineru.net/api/v4")
    source = tmp_path / "source"
    md_path = _make_result(source)
    with _cache_env(tmp_path):
        assert mineru_cache.store(key, source, str(md_path)) is True

        fresh = tmp_path / "fresh"
        hit = mineru_cache.load(key, fresh)
        assert hit is not None
        assert hit["cache_hit"] is True
        assert "试卷" in hit["markdown"]
        assert hit["markdown_path"].startswith(str(fresh))
        assert len(hit["image_paths"]) == 1
        assert hit["image_paths"][0].startswith(str(fresh))
        # 缓存本体仍在，可反复命中
        assert mineru_cache.read_manifest(key) is not None
        assert mineru_cache.load(key, tmp_path / "fresh2") is not None


def test_mineru_cache_key_separates_content_model_and_endpoint():
    """三个维度任一变化都必须落到未命中——否则会把不同口径的结果互相污染。"""

    from mathbank import mineru_cache

    base = mineru_cache.cache_key(b"data", "vlm", "https://mineru.net/api/v4")
    assert base != mineru_cache.cache_key(b"data2", "vlm", "https://mineru.net/api/v4")
    assert base != mineru_cache.cache_key(b"data", "pipeline", "https://mineru.net/api/v4")
    assert base != mineru_cache.cache_key(b"data", "vlm", "https://mine.example/v4")
    assert base == mineru_cache.cache_key(b"data", "vlm", "https://mineru.net/api/v4/")
    assert len(base) == 49


def test_mineru_cache_rejects_tampered_and_foreign_entries(tmp_path):
    """字节被改、清单损坏、键非法时一律回退真实调用，绝不误用坏数据。"""

    from mathbank import mineru_cache

    key = mineru_cache.cache_key(b"x", "vlm", "https://mineru.net/api/v4")
    source = tmp_path / "src"
    md_path = _make_result(source)
    with _cache_env(tmp_path):
        mineru_cache.store(key, source, str(md_path))
        entry = mineru_cache.cache_root() / key
        (entry / "images" / "pic.png").write_bytes(b"tampered")
        assert mineru_cache.load(key, tmp_path / "out") is None

        mineru_cache.store(key, source, str(md_path))
        (entry / "manifest.json").write_text("{不是 json", encoding="utf-8")
        assert mineru_cache.load(key, tmp_path / "out") is None

        assert mineru_cache.load("../../etc/passwd", tmp_path / "out") is None
        assert mineru_cache.read_manifest("..\\..\\windows") is None


def test_mineru_cache_store_keeps_relative_paths_and_is_atomic(tmp_path):
    """整包归档必须保留 images/ 相对路径，且不得留下半个条目。"""

    from mathbank import mineru_cache

    key = mineru_cache.cache_key(b"y", "vlm", "https://mineru.net/api/v4")
    source = tmp_path / "src2"
    md_path = _make_result(source)
    (source / "mineru_result.zip").write_bytes(b"zip-blob")  # 结果包本体不必归档
    with _cache_env(tmp_path):
        root = mineru_cache.cache_root()
        root.mkdir(parents=True, exist_ok=True)
        assert mineru_cache.store(key, source, str(md_path)) is True
        assert not any(p.name.startswith(".tmp-") for p in root.iterdir())
        manifest = mineru_cache.read_manifest(key)
        assert set(manifest["files"]) == {"full.md", "images/pic.png"}
        assert all(
            not name.startswith("/") and ".." not in name for name in manifest["files"]
        )
        # 空内容不缓存：否则「这份 PDF 永远解析不出文字」会被固化
        empty = tmp_path / "empty"
        empty.mkdir()
        assert mineru_cache.store("0" * 32 + "-" + "0" * 16, empty, "") is False


def test_parse_pdf_skips_all_cloud_calls_on_cache_hit(tmp_path):
    """命中路径上 create_batch/upload/poll/download 一次都不能被调用。"""

    from mathbank import mineru_cache, mineru_client

    payload = b"%PDF-1.4 cached-content"
    key = mineru_cache.cache_key(
        payload, mineru_client.DEFAULT_MODEL, mineru_client.get_api_base()
    )
    source = tmp_path / "first"
    md_path = _make_result(source)
    states = []

    with _cache_env(tmp_path):
        mineru_cache.store(key, source, str(md_path))
        with patch.object(mineru_client, "create_batch") as create, \
             patch.object(mineru_client, "upload_bytes") as upload, \
             patch.object(mineru_client, "poll_batch") as poll, \
             patch.object(mineru_client, "download_zip") as download:
            result = mineru_client.parse_pdf(
                payload,
                "paper.pdf",
                tmp_path / "second",
                on_progress=lambda state, item: states.append(state),
            )
            assert result["cache_hit"] is True
            assert create.call_count == 0
            assert upload.call_count == 0
            assert poll.call_count == 0
            assert download.call_count == 0
        assert states == ["cached"]


def test_parse_pdf_populates_cache_after_a_real_call(tmp_path):
    """真实调用成功后回填缓存：第二次同样内容必须零云端请求。"""

    from mathbank import mineru_client

    payload = b"%PDF-1.4 fresh-content"
    with _cache_env(tmp_path), patch.object(
        mineru_client, "create_batch", return_value=("b1", "http://up")
    ) as create, patch.object(mineru_client, "upload_bytes"), patch.object(
        mineru_client, "poll_batch", return_value={"full_zip_url": "http://zip"}
    ), patch.object(
        mineru_client,
        "download_zip",
        side_effect=lambda url, target: _fake_cloud_package(target),
    ):
        first = mineru_client.parse_pdf(payload, "p.pdf", tmp_path / "w1")
        assert create.call_count == 1
        assert "cache_hit" not in first

        with patch.object(mineru_client, "create_batch") as second_create:
            again = mineru_client.parse_pdf(payload, "p.pdf", tmp_path / "w2")
            assert second_create.call_count == 0
        assert again["cache_hit"] is True
        assert again["markdown"] == first["markdown"]


def test_force_fresh_bypasses_the_mineru_cache(tmp_path):
    """force=True 时即便有缓存也要重新调用云端（用户明确要重跑）。"""

    from mathbank import mineru_cache, mineru_client

    payload = b"%PDF-1.4 forced"
    key = mineru_cache.cache_key(
        payload, mineru_client.DEFAULT_MODEL, mineru_client.get_api_base()
    )
    source = tmp_path / "src3"
    md_path = _make_result(source)
    with _cache_env(tmp_path):
        mineru_cache.store(key, source, str(md_path))
        with patch.object(
            mineru_client, "create_batch", return_value=("b", "http://up")
        ) as create, patch.object(mineru_client, "upload_bytes"), patch.object(
            mineru_client, "poll_batch", return_value={"full_zip_url": "http://z"}
        ), patch.object(
            mineru_client,
            "download_zip",
            side_effect=lambda url, t: _fake_cloud_package(t),
        ):
            result = mineru_client.parse_pdf(
                payload, "p.pdf", tmp_path / "w3", force=True
            )
            assert create.call_count == 1
            assert "cache_hit" not in result
            assert result["markdown"].startswith("# 卷一")


def test_disable_flag_turns_the_cache_off(tmp_path):
    from mathbank import mineru_cache

    with _cache_env(tmp_path), patch.dict(os.environ, {"MINERU_CACHE_DISABLE": "1"}):
        assert mineru_cache.is_enabled() is False
        key = mineru_cache.cache_key(b"z", "vlm", "https://mineru.net/api/v4")
        source = tmp_path / "src4"
        md_path = _make_result(source)
        assert mineru_cache.store(key, source, str(md_path)) is False
        assert mineru_cache.load(key, tmp_path / "out") is None


def test_mineru_cache_hit_is_field_identical_to_a_real_parse(tmp_path):
    """命中结果必须与真解析逐字段同构，而不只是「能跑」。

    这条是缓存的安全边界：上游 MinerU 分支在命中之后还要消费两样东西——
      * stage_images(image_paths) ——按文件名映射，路径形态不同会漏配；
      * collect_page_texts(workdir) ——读结果包里的 *_content_list.json，
        清单没被整包归档时它会恒空，于是「源自第 N 页」入口静默消失。
    两个退化都不会报错，只会让功能悄悄变少，所以必须在这里钉住。
    """

    from mathbank import mineru_client

    payload = b"%PDF-1.4 field-identical"
    with _cache_env(tmp_path), patch.object(
        mineru_client, "create_batch", return_value=("b", "http://up")
    ), patch.object(mineru_client, "upload_bytes"), patch.object(
        mineru_client, "poll_batch", return_value={"full_zip_url": "http://z"}
    ), patch.object(
        mineru_client,
        "download_zip",
        side_effect=lambda u, t: _fake_cloud_package(t),
    ):
        fresh = mineru_client.parse_pdf(payload, "p.pdf", tmp_path / "w1")

    def boom(*args, **kwargs):
        raise AssertionError("命中后不应再打云端")

    with _cache_env(tmp_path), patch.object(mineru_client, "create_batch", boom):
        hit = mineru_client.parse_pdf(payload, "p.pdf", tmp_path / "w2")

    assert hit["cache_hit"] is True
    assert hit["markdown"] == fresh["markdown"]
    assert [Path(p).name for p in hit["image_paths"]] == [
        Path(p).name for p in fresh["image_paths"]
    ]
    assert [str(Path(p).relative_to(hit["workdir"])) for p in hit["image_paths"]] == [
        str(Path(p).relative_to(fresh["workdir"])) for p in fresh["image_paths"]
    ]
    # 逐页文本在命中路径上不得退化成空
    assert mineru_client.collect_page_texts(hit["workdir"]) == ["第一页", "第二页"]
    # stage_images 之后必须仍能按文件名映射上（markdown 引用替换不了就是静默丢图）
    m1 = mineru_client.stage_images(fresh["image_paths"], tmp_path / "up1")
    m2 = mineru_client.stage_images(hit["image_paths"], tmp_path / "up2")
    assert {Path(k).name for k in m1} == {Path(k).name for k in m2} == {"a.png"}
    md1 = mineru_client.replace_image_refs(fresh["markdown"], m1)
    md2 = mineru_client.replace_image_refs(hit["markdown"], m2)
    assert md1 == md2
    # 测试态静态根前缀与生产不同，这里只锁「引用确实被换成了落盘地址」
    assert "images/a.png" not in md2 and "mineru_" in md2


def test_mineru_task_branch_reports_cache_hit_in_log(tmp_path):
    """任务日志要让用户看得出这次没花钱；同时锁住 force_fresh 的透传接线。"""

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
             return_value={
                 "markdown": "# 试卷标题\n\n题干内容",
                 "image_paths": [],
                 "cache_hit": True,
             },
         ) as parse, \
         patch.object(mineru_client, "stage_images", return_value={}), \
         patch.object(mineru_client, "replace_image_refs", side_effect=lambda md, m: md), \
         patch("main.parse_paper_text_internal", return_value=[{"content": "题目一"}]), \
         patch("main.post_process_pdf_parsed_questions", return_value=[{"content": "题目一"}]):
        task_id = "mineru-cache-hit-1"
        DOCUMENT_TASKS.create(
            task_id, status="pending", log="", document_type="pdf", temp_assets=[]
        )
        run_pdf_parsing_task(
            task_id, pdf_path.read_bytes(), "test.pdf", False, None, "mineru_cloud"
        )
        state = DOCUMENT_TASKS.snapshot(task_id)
        assert state["status"] == "completed"
        assert "缓存" in state["log"]
        assert parse.call_args.kwargs["force"] is False

        DOCUMENT_TASKS.create(
            "mineru-cache-hit-2",
            status="pending",
            log="",
            document_type="pdf",
            temp_assets=[],
        )
        run_pdf_parsing_task(
            "mineru-cache-hit-2",
            pdf_path.read_bytes(),
            "test.pdf",
            False,
            None,
            "mineru_cloud",
            force_fresh=True,
        )
        assert parse.call_args.kwargs["force"] is True
