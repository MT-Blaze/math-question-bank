"""讲义打印导出：把讲义 HTML 用无头 Chrome/Edge 打印成 PDF。

这是【导出 PDF】的新实现——前端把预览 iframe 的 srcdoc（渲染前原始 HTML，
含 ``\\(...\\)`` 未渲染公式）POST 过来，本模块内联 KaTeX 资源后交给本机
Chrome/Edge 无头打印，产物与网页预览所见即所得。

旧的 XeLaTeX 链路（``lecture_export.build_lecture_latex``）保留给
【导出 TEX】与试卷组卷功能，不在本模块职责内。
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from mathbank.paths import PROJECT_ROOT, STATIC_DIR

KATEX_DIR = STATIC_DIR / "lib" / "katex"
UPLOADS_BASE = (PROJECT_ROOT / "static" / "uploads").as_uri()
_CSS_LINK = '<link rel="stylesheet" href="/static/lib/katex/katex.min.css">'
_JS_TAGS = (
    '<script src="/static/lib/katex/katex.min.js"></script>',
    '<script src="/static/lib/katex/contrib/auto-render.min.js"></script>',
)
# srcdoc 在 file:// 基准下，/static/uploads/x.png 会解析到磁盘根而加载失败；
# 与 KaTeX 字体同套路，把两种相对形态统一重写为绝对 file:// URI。
_UPLOAD_REF_RE = re.compile(r"(?<![\w/.-])(?:/)?static/uploads/")


def detect_chrome() -> str | None:
    """返回第一个可用的 Chromium 系浏览器可执行文件路径，找不到返回 None。"""
    candidates = []
    env_path = os.environ.get("CHROME_PATH")
    if env_path:
        candidates.append(Path(env_path))
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        candidates.append(
            Path(local_appdata) / "Google/Chrome/Application/chrome.exe")
    candidates.extend([
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    ])
    for candidate in candidates:
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            continue
    return None


def build_print_html(raw_html: str) -> str:
    """把渲染前的 srcdoc HTML 变成自包含的可打印 HTML。

    - KaTeX CSS 内联进 ``<style>``，其中 ``url(fonts/...)`` 重写为指向项目
      字体目录的绝对 file:// URL（file:// 上下文里没有可用的相对基准）；
    - katex.min.js 与 auto-render.min.js 内联进 ``<script>``；
    - srcdoc 自带的 renderMathInElement 调用原样保留，由无头浏览器执行。
    """
    html = raw_html
    css_text = (KATEX_DIR / "katex.min.css").read_text(encoding="utf-8")
    fonts_base = (KATEX_DIR / "fonts").as_uri()
    css_text = re.sub(r"url\((?:\.\./)?fonts/", "url(%s/" % fonts_base, css_text)
    html = _UPLOAD_REF_RE.sub(UPLOADS_BASE + "/", html)
    if _CSS_LINK in html:
        html = html.replace(_CSS_LINK, "<style>%s</style>" % css_text)
    else:
        # 前端 HTML 形态变化时宁可不渲染公式，也不能静默产出缺样式的 PDF
        raise RuntimeError("讲义 HTML 里找不到 KaTeX 样式表引用，无法内联打印资源")
    for tag, rel in zip(_JS_TAGS, ("katex.min.js", "contrib/auto-render.min.js")):
        js_text = (KATEX_DIR / rel).read_text(encoding="utf-8")
        if tag in html:
            html = html.replace(tag, "<script>%s</script>" % js_text)
        else:
            # 脚本缺失 = srcdoc 里的 renderMathInElement 调用会静默失效，
            # PDF 里留下整页 \(..\) 原文；补挂内联标签而不是报错
            injection = "<script>%s</script>" % js_text
            if "</body>" in html:
                html = html.replace("</body>", injection + "</body>", 1)
            else:
                html = html + injection
    return html


def print_to_pdf(html: str, timeout: int = 60) -> bytes:
    """用无头 Chrome/Edge 把完整 HTML 打印成 PDF 字节流。

    启动参数带省内存一组（低配机器上 Chrome 渲染容易撞内存墙导致超时）；
    超时报错带分段计时（HTML 落盘 / Chrome 启动到首进程可见 / 等待渲染出件），
    让用户能看出这次卡在哪一段而不是只看到"导出失败"。
    """
    chrome = detect_chrome()
    if not chrome:
        raise RuntimeError("未找到 Chrome/Edge，无法进行无头打印")
    tmp_html = None
    tmp_pdf = None
    t_start = time.monotonic()
    try:
        with tempfile.NamedTemporaryFile(
                suffix=".html", delete=False) as fh:
            tmp_html = Path(fh.name)
            fh.write(html.encode("utf-8"))
        t_written = time.monotonic()
        tmp_pdf = tmp_html.with_suffix(".pdf")
        cmd = [
            chrome, "--headless=new", "--disable-gpu",
            "--no-pdf-header-footer",
            "--print-to-pdf=%s" % tmp_pdf,
            "--virtual-time-budget=20000",
            # 省内存组：/dev/shm、扩展、后台网络、默认应用全关；V8 堆限 256MB。
            # 严禁加 --single-process（渲染器会在大讲义上直接崩）。
            "--disable-dev-shm-usage",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-default-apps",
            "--js-flags=--max-old-space-size=256",
            tmp_html.as_uri(),
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            elapsed_ms = (time.monotonic() - t_start) * 1000
            detail = ("PDF 导出超时（%.0fs）：写临时 HTML %.0fms，Chrome 启动+渲染等待 %.0fms。"
                      "若反复超时，请关闭其他占内存的程序后重试（无头浏览器需要空闲内存），"
                      "或减少单次导出的讲义节数、改用【导出 TEX】离线编译。"
                      % (timeout, (t_written - t_start) * 1000,
                         elapsed_ms - (t_written - t_start) * 1000))
            print("[LecturePrint] %s" % detail)
            raise RuntimeError(detail)
        if not tmp_pdf.is_file() or tmp_pdf.stat().st_size == 0:
            stderr_tail = (result.stderr or "").strip()[-300:]
            detail = ("浏览器未产出 PDF（退出码 %s，耗时 %.1fs）：%s"
                      % (result.returncode, time.monotonic() - t_start,
                         stderr_tail or "无错误输出"))
            print("[LecturePrint] %s" % detail)
            raise RuntimeError(detail)
        return tmp_pdf.read_bytes()
    finally:
        for path in (tmp_html, tmp_pdf):
            if path is not None:
                try:
                    path.unlink()
                except OSError:
                    pass
