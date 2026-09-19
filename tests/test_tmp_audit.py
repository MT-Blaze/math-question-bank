"""1.pdf 全量审计：整卷解析 -> 逐题图片归属 / 孤儿图 / 表格直方图 / 跨题引用。

**这个文件会真金白银调用付费 AI**（整页视觉 OCR + 拆题），所以默认跳过。
只在显式要求时运行：`PDF_AUDIT=1 python -m pytest tests/test_tmp_audit.py -q -s`。
只在隔离测试库与 static/test_uploads 内运行，不触碰正式题库。
"""
import json
import os
import re
import time

import pytest

from main import LOCAL_TOKEN

PDF = r"E:\15972\Desktop\1.pdf"
HDRS = {"X-Local-Token": LOCAL_TOKEN}
OUT = os.path.join(".system_generated", "scratch", "audit_full.json")


def _wait(client, task_id, timeout=1200):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = client.get(f"/api/tasks/{task_id}/status").json()
        if st.get("status") in ("completed", "error", "cancelled"):
            return st
        time.sleep(2)
    raise AssertionError("timeout")


@pytest.mark.skipif(
    os.getenv("PDF_AUDIT") != "1",
    reason="付费 AI 全卷审计：只在 PDF_AUDIT=1 时显式运行",
)
def test_audit_full_paper(client):
    if not os.path.exists(PDF):
        pytest.skip("sample pdf missing")
    with open(PDF, "rb") as fh:
        payload = fh.read()
    r = client.post(
        "/api/upload/pdf-task",
        headers=HDRS,
        files={"file": ("1.pdf", payload, "application/pdf")},
        data={"generate_answers": "false", "pdf_strategy": "native_preferred",
              "paper_title": "审计卷"},
    )
    assert r.status_code == 200, r.text
    task_id = r.json()["task_id"]
    st = _wait(client, task_id)
    assert st["status"] == "completed", st
    detail = client.get(f"/api/tasks/{task_id}/status").json()
    qs = detail.get("data") or []
    json.dump({"task_id": task_id, "questions": qs,
               "page_images": detail.get("page_images"),
               "temp_assets": detail.get("temp_assets"),
               "diagnostics": detail.get("diagnostics")},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    GFX = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]*)\}")
    used = {}
    print(f"\n解析结果：{len(qs)} 题")
    for i, q in enumerate(qs, 1):
        c = q.get("content") or ""
        a = q.get("answer_markdown") or ""
        ci, ai = GFX.findall(c), GFX.findall(a)
        for p in ci:
            used.setdefault(p, []).append(f"Q{i}题干")
        for p in ai:
            used.setdefault(p, []).append(f"Q{i}解析")
        print(f"\n{'=' * 70}\nQ{i}  type={q.get('question_type')}  原题号={q.get('paper_question_no')!r}")
        print(f"  正文图 {len(ci)} 张 {ci}")
        print(f"  解析图 {len(ai)} 张 {ai}")
        print(f"  image_paths={q.get('image_paths')}")
        print(f"  _image_review={q.get('_image_review')}")
        print(f"  含表格: {'tabular' if chr(92) + 'begin{tabular' in c else 'NO'}"
              f"  含占位: {[m for m in re.findall(r'插图待补[^)）]*', c)]}")
        print("  题干:", c[:200].replace("\n", " / "))
        print("  解析:", a[:160].replace("\n", " / "))

    print(f"\n{'=' * 70}\n跨题引用检查（一张图被多题使用即为串题）：")
    dup = {k: v for k, v in used.items() if len(v) > 1}
    print("  ", dup or "无")

    assets = set()
    for root, _dirs, files in os.walk(os.path.join("static", "test_uploads", "tmp")):
        for f in files:
            if f.startswith("pdf_crop_") and task_id[:8] in f or f.startswith(f"pdf_crop_{task_id}"):
                assets.add(os.path.relpath(os.path.join(root, f), "static").replace("\\", "/"))
    reg = {os.path.basename(str(p)) for p in (detail.get("temp_assets") or [])} | \
          {os.path.basename(str(p)) for p in (detail.get("page_images") or [])}
    print(f"\n本任务裁剪出的图片文件：{len(assets)} 张")
    print("  ", sorted(os.path.basename(x) for x in assets))
    print(f"\n正文实际引用：{len(used)} 张")
    orphan = sorted(os.path.basename(x) for x in assets if os.path.basename(x) not in used)
    print(f"孤儿图（裁出来但没进任何题）：{len(orphan)} 张\n  ", orphan)

    # 裁图完整性：每张裁剪图的像素尺寸必须等于该页某个真实图形锚点（内嵌位图边界）。
    # 尺寸对不上就说明模型框把图裁成了碎片或裁到了正文行上。
    import pymupdf as fitz
    from PIL import Image
    from mathbank.illustration_crop import page_figure_anchor_px

    doc = fitz.open(PDF)
    anchors_by_page = {}
    for pno in range(len(doc)):
        page_png = next((p for p in (detail.get("page_images") or [])
                         if p.endswith(f"_{pno}.png")), None)
        if not page_png:
            continue
        path = page_png.lstrip("/")
        with Image.open(path) as im:
            w, h = im.size
        anchors_by_page[pno] = page_figure_anchor_px(payload, pno, w, h)

    print("\n裁图尺寸 vs 页面真实图形锚点：")
    bad = []
    for asset in sorted(assets):
        name = os.path.basename(asset)
        m = re.match(r"pdf_crop_.+?_(\d+)_(\d+)\.png$", name)
        if not m:
            continue
        pno = int(m.group(1))
        path = os.path.join("static", asset.lstrip("/"))
        with Image.open(path) as im:
            size = im.size
        hit = any(
            abs(size[0] - (a[2] - a[0])) <= 8 and abs(size[1] - (a[3] - a[1])) <= 8
            for a in anchors_by_page.get(pno, [])
        )
        print(f"  {name}: {size[0]}x{size[1]}px  {'OK 对齐真实图形' if hit else '⚠ 未对齐任何图形锚点'}")
        if not hit:
            bad.append(name)
    print(f"\n裁图未对齐锚点的张数：{len(bad)}  {bad}")
