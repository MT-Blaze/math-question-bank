"""拆卷保存的 image_paths 对账判据回归。

`reconcile_image_paths_with_text` 位于「晋升」与 `require_file` 校验之间，去留判据一旦
写错就是静默改数据：旧实现按「正文/绘图槽有没有引用」剔除，而拆卷卡片的题干截图按设计
**只登记在 image_paths、由配图条统一展示**（正文里根本不出现文件名），于是每道带手动裁图
的题都会「保存成功但配图消失」。这三条测试把判据钉回它唯一的正确口径——文件是否还在。
"""

import io
import json
from pathlib import Path
from unittest.mock import patch

import main  # routes_questions ← main 是循环导入，先加载入口再取子模块
from PIL import Image

from mathbank.routes_questions import (
    reconcile_image_paths_with_text,
)


def _make_png(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    Image.new("RGB", (6, 6), "white").save(buffer, format="PNG")
    path.write_bytes(buffer.getvalue())
    return path


def _reconcile(paths, *, content="", answer="", uploads_dir):
    with patch("main.UPLOAD_DIR", str(uploads_dir)), patch(
        "mathbank.routes_questions.UPLOAD_DIR", str(uploads_dir)
    ):
        return reconcile_image_paths_with_text(
            paths,
            content,
            answer,
            content_tikz_assets=None,
            answer_tikz_assets="[]",
            tikz_reference_image_path="",
        )


def test_existing_file_survives_even_when_referenced(tmp_path):
    live = _make_png(tmp_path / "tikz_live.png")
    kept = _reconcile(["/static/uploads/tikz_live.png"], uploads_dir=tmp_path)
    assert kept == ["/static/uploads/tikz_live.png"]
    assert live.exists()


def test_missing_file_is_dropped_and_logged(tmp_path, capsys):
    gone = tmp_path / "tikz_gone.png"
    assert not gone.exists()

    kept = _reconcile(["/static/uploads/tikz_gone.png"], uploads_dir=tmp_path)

    assert kept == []
    logged = capsys.readouterr().out
    assert "[asset-reconcile] drop stale image_path: tikz_gone.png" in logged


def test_strip_only_figure_registration_is_kept(tmp_path):
    """关键回归：正文完全不引用的合法配图必须保留。

    这就是实测踩到的形态 —— `[PDF PostProcess]` 归属或【手动截图】把 crop 塞进
    `q.image_paths`，题干里只有「如图所示」四个字，文件名一次都不出现。
    """
    crop = _make_png(tmp_path / "pdf_crop_task_1_0.png")

    kept = _reconcile(
        ["/static/uploads/pdf_crop_task_1_0.png"],
        content="如图所示，求阴影部分面积。",
        answer="",
        uploads_dir=tmp_path,
    )

    assert kept == ["/static/uploads/pdf_crop_task_1_0.png"]
    assert crop.exists()


def test_inline_referenced_but_missing_file_still_dropped(tmp_path, capsys):
    """正文引用不能替文件担保存在性：删掉的图仍要剔除，否则会撞上 require_file 报错。"""
    kept = _reconcile(
        ["/static/uploads/tikz_deleted.png"],
        content="如图 $x$ 所示 ![插图](/static/uploads/tikz_deleted.png)",
        uploads_dir=tmp_path,
    )
    assert kept == []
    out = capsys.readouterr().out
    assert "drop stale image_path: tikz_deleted.png" in out
    # 日志理由必须区分「有引用方但文件没了」与「压根没引用」，否则排查时看不出是哪类。
    assert "正文/绘图槽有引用" in out


def test_tikz_asset_rendered_image_kept_without_body_reference(tmp_path):
    """TikZ 工作台的渲染图只登记在资产槽与 image_paths，不进正文——同样必须保留。"""
    _make_png(tmp_path / "tikz_asset.png")
    paths = json.dumps(["/static/uploads/tikz_asset.png"])
    with patch("main.UPLOAD_DIR", str(tmp_path)), patch(
        "mathbank.routes_questions.UPLOAD_DIR", str(tmp_path)
    ):
        kept = reconcile_image_paths_with_text(
            ["/static/uploads/tikz_asset.png"],
            "题干没有任何图片语法",
            "",
            content_tikz_assets=json.dumps(
                [{"id": "t1", "image_path": "/static/uploads/tikz_asset.png"}]
            ),
            answer_tikz_assets="[]",
            tikz_reference_image_path="",
        )
    assert kept == ["/static/uploads/tikz_asset.png"]
    assert paths  # 仅确认夹具序列化正常
