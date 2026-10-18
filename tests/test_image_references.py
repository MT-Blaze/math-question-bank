"""插图引用扫描口径的回归锁（P0：判据漏判 = 启动清理删掉已入库的图）。

背景：``main.clean_orphaned_images`` 每次服务启动都会删掉 uploads 里"未被引用"
且超过 1 小时的图片。曾经的判据只查 ``Question._image_paths``，而「彻底 LaTeX 化」
后图片引用几乎只以**裸文件名**出现在 content 的 ``\\includegraphics{...}`` 里，
``image_paths`` 反而常为空数组 —— 结果每次重启把用户插图删光。

这里钉死两件事：① 共享扫描函数认得全所有形态；② 两处清理都调它，不能各写一套。
"""
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mathbank.image_references import iter_question_image_references  # noqa: E402


def _uploads_dir():
    return Path(PROJECT_ROOT) / "static" / "uploads"


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDB:
    """最小 DB 桩：collect_referenced_assets 只读 (image_paths, content, answer_markdown)。"""

    def __init__(self, rows):
        self._rows = rows

    def query(self, *columns):  # noqa: ARG002 - 只关心返回行
        return _FakeQuery(self._rows)


def test_iter_references_covers_every_storage_form():
    """image_paths / \\includegraphics 裸名 / 带前缀 / Markdown 四种写法都要认。"""
    raw_paths = '["/static/uploads/a.png", "b.png"]'
    text = (
        r"\includegraphics[max width=0.42\linewidth]{c.jpg}" "\n"
        r"\includegraphics{/static/uploads/d.jpg}" "\n"
        r"![](/static/uploads/e.jpg)" "\n"
        r'![](/static/uploads/f.jpg "标题")' "\n"
        r"\includegraphics{g.png}"
    )
    found = iter_question_image_references(raw_paths, text, "")
    for expected in ("a.png", "b.png", "c.jpg", "d.jpg", "e.jpg", "f.jpg", "g.png"):
        assert any(expected in value for value in found), (
            f"引用 {expected} 未被识别: {found}"
        )


def test_collect_referenced_assets_ignores_external_and_empty():
    """外部 URI 与空引用不得被算成"已引用"（它们本来就不在 uploads 里）。"""
    from mathbank.image_references import collect_referenced_assets

    db = _FakeDB(
        [
            (
                '["https://example.com/x.png"]',
                r"\includegraphics{https://example.com/y.jpg}" "\n" r"![]()",
                "",
            )
        ]
    )
    assert collect_referenced_assets(
        db, uploads_dir=_uploads_dir(), url_prefix="static/uploads"
    ) == set()


def test_collect_referenced_assets_detects_bare_includegraphics():
    """真实形态：image_paths 为空、图只在 content 里以裸文件名出现 —— 必须被认成已引用。"""
    from mathbank.image_references import collect_referenced_assets

    content = (
        "设 $abc > 0$ 的图象可能是 \\paren\n"
        "\\begin{choices}\n"
        r"    \item \includegraphics[max width=0.42\linewidth]{opt_1.jpg}" "\n"
        r"    \item \includegraphics[max width=0.42\linewidth]{opt_2.jpg}" "\n"
        "\\end{choices}"
    )
    db = _FakeDB([("[]", content, "")])
    referenced = collect_referenced_assets(
        db, uploads_dir=_uploads_dir(), url_prefix="static/uploads"
    )
    names = {path.name for path in referenced}
    assert names == {"opt_1.jpg", "opt_2.jpg"}, names
    for path in referenced:
        assert str(path).startswith(str(_uploads_dir())), path


def test_both_cleanup_paths_share_one_reference_scan():
    """两处清理必须共用同一个扫描实现，否则口径还会再分叉。

    只做源码断言：``import mathbank.routes_questions`` 会连带 ``from main import``
    把整个应用拉起来（启线程、动数据库），单测里不该有这种副作用。
    """
    main_source = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
    routes_source = (
        PROJECT_ROOT / "mathbank" / "routes_questions.py"
    ).read_text(encoding="utf-8")

    assert "collect_referenced_assets" in main_source
    assert "collect_referenced_assets" in routes_source

    # 启动清理不得再退化成"只查 _image_paths"
    cleaner_start = main_source.index("def clean_orphaned_images():")
    cleaner_body = main_source[
        cleaner_start : main_source.index("def recalibrate_usage_counts():", cleaner_start)
    ]
    # 注意：注释里也会提到 "Question._image_paths"，所以必须匹配**查询语句**本身
    assert "db.query(Question._image_paths)" not in cleaner_body, (
        "启动清理又退回只查 image_paths 字段了 —— content 里的裸文件名引用会漏判"
    )
    assert "referenced_images" not in cleaner_body


def test_routes_helper_no_longer_uses_prefix_only_regex():
    """旧正则只认 /static/uploads/ 前缀，裸文件名漏判 —— 不得复活。"""
    routes_source = (
        PROJECT_ROOT / "mathbank" / "routes_questions.py"
    ).read_text(encoding="utf-8")
    start = routes_source.index("def _referenced_question_assets(")
    body = routes_source[start : start + 1200]
    assert r"r'/static/(?:uploads|test_uploads)/" not in body, "旧的前缀专用正则又回来了"
