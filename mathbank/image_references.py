"""题目插图引用的**统一扫描口径**。

历史上这里有两条互不相干的判据，其中一条漏判就直接删用户图片：

* ``main.clean_orphaned_images`` 只查 ``Question._image_paths`` 字段；
* ``routes_questions._referenced_question_assets`` 会扫 content/answer_markdown，
  但正则只认带 ``/static/uploads/`` 前缀的引用。

「彻底 LaTeX 化」之后，图片引用绝大多数只以**裸文件名**出现在
``\\includegraphics{文件名}`` 里（``image_paths`` 反而经常是空数组），
于是两条判据同时失效——表现为每次启动把 uploads 里一小时以上的图删光。

本模块提供唯一的扫描实现，两处清理都改调它，口径不会再分叉。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from mathbank.asset_security import AssetSecurityError, resolve_upload_asset


# 带 URL 前缀的引用（老形态，Markdown 与 image_paths 里都有）
_UPLOAD_URL_RE = re.compile(r"/static/(?:uploads|test_uploads)/[A-Za-z0-9_./-]+")
# LaTeX 形态：\includegraphics[可选参数]{文件名}——彻底 LaTeX 化后多为裸文件名
_INCLUDEGRAPHICS_RE = re.compile(r"\\includegraphics\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")
# Markdown 形态：![](路径 "可选标题")
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
# HTML 形态：<img src="...">（lecture_notes 里的知识梳理正文用）
_IMG_SRC_RE = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.I)


def iter_question_image_references(raw_image_paths=None, *texts: str) -> list[str]:
    """从 ``image_paths`` 字段与若干文本块里收集所有插图引用（原始字符串，未校验）。

    覆盖三种形态：
      * ``image_paths`` JSON 数组里的每一项（URL 或裸名都要认）；
      * ``\\includegraphics[...]{文件名}``（裸名或带路径都要认）；
      * Markdown ``![](路径 "标题")``（只取路径段）。
    """

    references: list[str] = []
    if raw_image_paths:
        try:
            parsed = json.loads(raw_image_paths)
        except (TypeError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, list):
            references.extend(str(value) for value in parsed if value)

    blob = "\n".join(text or "" for text in texts)
    if not blob:
        return references

    references.extend(_UPLOAD_URL_RE.findall(blob))
    for match in _INCLUDEGRAPHICS_RE.finditer(blob):
        value = match.group(1).strip()
        if value:
            references.append(value)
    for match in _MARKDOWN_IMAGE_RE.finditer(blob):
        value = match.group(1).strip().split()[0] if match.group(1).strip() else ""
        if value:
            references.append(value)
    return references


def iter_note_image_references(content: str) -> list[str]:
    """知识块正文里的插图引用：``<img src>``、Markdown、``\\includegraphics``，
    以及 @mindmap JSON 的 "image" 字段（前向兼容）。只吐原始字符串。"""
    references: list[str] = []
    text = str(content or "")
    if not text:
        return references
    if text.startswith("@mindmap"):
        try:
            data = json.loads(text[len("@mindmap"):].strip())
        except (TypeError, json.JSONDecodeError):
            data = None

        def walk(node) -> None:
            if isinstance(node, dict):
                image = node.get("image")
                if isinstance(image, str) and image.strip():
                    references.append(image.strip())
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(data)
    references.extend(_IMG_SRC_RE.findall(text))
    for match in _MARKDOWN_IMAGE_RE.finditer(text):
        value = match.group(1).strip().split()[0] if match.group(1).strip() else ""
        if value:
            references.append(value)
    for match in _INCLUDEGRAPHICS_RE.finditer(text):
        value = match.group(1).strip()
        if value:
            references.append(value)
    return references


def collect_referenced_assets(db, *, uploads_dir, url_prefix) -> set[Path]:
    """扫全库，返回所有被题目**或讲义知识块**引用到的插图绝对路径集合。

    判据同时覆盖 ``_image_paths`` / ``content`` / ``answer_markdown``，
    并且裸文件名与带 URL 前缀的写法都认。

    ⚠️ 必须把 ``lecture_notes`` 一并算进「被引用」：知识梳理引用的图片
    不在任何题目的台账里，漏扫会让启动清理把用户配到思维导图的图当孤儿
    删掉（与历史上 content 裸名漏判同一类事故，见模块 docstring）。

    每条引用都过一遍 ``resolve_upload_asset``：既借用它自带的目录穿越与
    类型白名单校验，也顺便把裸名、URL 统一归一化成同一个绝对路径。解析
    失败的引用（外部 URI、越界路径）一律跳过——它们本来也不在 uploads 里，
    不可能被这里的清理误删。
    """

    # 延迟导入：database 是不轻的模块，且本模块会被 main 与 routes 共同引用。
    from mathbank.database import LectureNote, Question

    resolved: set[Path] = set()
    rows = db.query(
        Question._image_paths,
        Question.content,
        Question.answer_markdown,
    ).all()
    for raw_paths, content, answer_markdown in rows:
        for reference in iter_question_image_references(
            raw_paths, content, answer_markdown
        ):
            try:
                resolved.add(
                    resolve_upload_asset(
                        reference,
                        uploads_dir=uploads_dir,
                        url_prefix=url_prefix,
                        require_file=False,
                    )
                )
            except AssetSecurityError:
                continue
    for note_row in db.query(LectureNote.content).all():
        # 单列查询在旧 SQLAlchemy 里可能返回行对象或一元组，两种形态都认
        note_content = note_row[0] if isinstance(note_row, tuple) else getattr(note_row, "content", note_row)
        for reference in iter_note_image_references(note_content):
            try:
                resolved.add(
                    resolve_upload_asset(
                        reference,
                        uploads_dir=uploads_dir,
                        url_prefix=url_prefix,
                        require_file=False,
                    )
                )
            except AssetSecurityError:
                continue
    return resolved
