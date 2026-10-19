"""组卷页万题架构压测（Playwright 无头 Chromium）。

用法：
    python -m scripts.perf_paper_stress --mode new
    python -m scripts.perf_paper_stress --mode legacy

脚本自带一条完整链路：用 MATHBANK_DATABASE_FILE 指向压测库另起一个 uvicorn
实例（绝不碰教师真实库，也不干扰本机 27182 上正在跑的服务），再用无头浏览器
真实打开组卷页，量：

  · 进入页面到首屏卡片渲染完成耗时（点击切工作台 → 可视区出现题目卡）
  · 列表接口的传输字节与耗时（老架构是整库 JSON，新架构是每页摘要）
  · 连续滚动 3 秒的帧时长分布（p50/p95/p99、掉帧数、最长帧）
  · long task 数量与总时长、DOM 节点总数、可视窗口内的卡片数、JS 堆
  · 刷新后购物车题目是否一根不少（分页改造最容易丢题的地方）

`--mode legacy` 通过 ?perfLegacyStream=1 让前端退回「整库拉取 + 整表渲染」，
两套数字跑在同一个压测库、同一份题面内容上，才叫真正的对照实验。

`--first-timeout`（默认 180s）控制首屏/可视区水合的等待上限：老架构在万题
下可能永远等不到第一行。超时不算脚本失败，而是最重要的一组对照数据，脚本会
记下 stalled 标记并照常输出报告。
"""

from __future__ import annotations

import argparse
import json
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STRESS_DB = "math_question_bank_stress.db"
DEFAULT_OUTPUT = PROJECT_ROOT / "outputs" / "perf_paper_stress.json"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def start_server(port: int, database_file: str) -> subprocess.Popen:
    import os

    env = dict(os.environ)
    env["MATHBANK_DATABASE_FILE"] = database_file
    env["PYTHONIOENCODING"] = "utf-8"
    env["NO_COLOR"] = "1"
    python = str(PROJECT_ROOT / "venv" / "Scripts" / "python.exe")
    if not Path(python).exists():
        python = sys.executable
    handle = subprocess.Popen(
        [python, "-B", "-X", "utf8", "-u", "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(port), "--no-access-log", "--log-level", "critical"],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    import urllib.request

    deadline = time.time() + 60
    while time.time() < deadline:
        if handle.poll() is not None:
            raise SystemExit("压测服务启动失败（进程已退出），请确认压测库已注入：python -m scripts.seed_perf_load")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as response:
                if response.status == 200:
                    return handle
        except Exception:
            time.sleep(0.4)
    handle.terminate()
    raise SystemExit("压测服务 60 秒内未就绪")


# 页面内执行的滚动采样：每帧记录时间差，同时把 scrollTop 匀速推进。
SCROLL_PROBE = r"""
async (params) => {
  const el = document.getElementById('paperQuestionStream');
  if (!el) throw new Error('题库流容器不存在');
  const deltas = [];
  let last = performance.now();
  let running = true;
  const loop = (t) => { deltas.push(t - last); last = t; if (running) requestAnimationFrame(loop); };
  requestAnimationFrame(loop);
  const observer = new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) longTasks.push({ dur: entry.duration, start: entry.startTime });
  });
  const longTasks = [];
  try { observer.observe({ entryTypes: ['longtask'] }); } catch (e) {}
  const t0 = performance.now();
  while (performance.now() - t0 < params.ms) {
    el.scrollTop += params.step;
    await new Promise((resolve) => requestAnimationFrame(resolve));
  }
  running = false;
  try { observer.disconnect(); } catch (e) {}
  const frames = deltas.slice(1);
  const sorted = frames.slice().sort((a, b) => a - b);
  const pick = (q) => sorted.length ? sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * q))] : 0;
  return {
    frames: frames.length,
    p50: pick(0.5),
    p95: pick(0.95),
    p99: pick(0.99),
    worst: sorted.length ? sorted[sorted.length - 1] : 0,
    dropped: frames.filter(f => f > 50).length,
    fps: frames.length ? Math.round((frames.length / ((performance.now() - t0) / 1000)) * 10) / 10 : 0,
    longTasks: longTasks,
    scrollTop: el.scrollTop,
    scrollHeight: el.scrollHeight
  };
}
"""

DOM_PROBE = r"""
() => {
  const totalNodes = document.getElementsByTagName('*').length;
  const rows = document.querySelectorAll('#paperStreamVirtualRoot .paper-stream-row').length;
  const cards = document.querySelectorAll('#paperStreamVirtualRoot .rounded-2xl').length;
  const katex = document.querySelectorAll('#paperQuestionStream .katex').length;
  const stream = document.getElementById('paperQuestionStream');
  return {
    domNodes: totalNodes,
    renderedRows: rows,
    renderedCards: cards,
    katexNodes: katex,
    scrollerClientHeight: stream ? stream.clientHeight : 0,
    jsHeapMb: performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1048576) : null
  };
}
"""


SEED_CART_JS = r"""
async (params) => {
  // 用真实存在的题目 ID 拼一辆购物车，再塞一道**已不存在**的题：
  // 一趟压测同时验「万题性能」和「分页改造不丢题 + 失效题不崩卷面」。
  const response = await fetch(`/api/questions?page=1&page_size=${params.size}&mode=lean&sort=desc`);
  const payload = await response.json();
  const items = payload.items || [];
  const cart = items.map(q => ({ id: q.id, score: q.question_type === 'detailed_answer' ? 12 : 5 }));
  cart.push({ id: params.deadId, score: 6 });
  localStorage.setItem('mathbank_paper_cart', JSON.stringify(cart));
  return cart.length;
}
"""

CART_STORAGE_SIZE_JS = r"""
() => {
  try {
    return (JSON.parse(localStorage.getItem('mathbank_paper_cart') || '[]') || []).length;
  } catch (e) {
    return -1;
  }
}
"""

# 虚拟窗口在视口上下各多挂几行 overscan，那些行按设计就只该是 140 字预览。
# 就绪判据必须只看**真正落在视口里**的行，否则永远等不到 aria-busy 清零。
VISIBLE_HYDRATED_JS = r"""
() => {
  const scroller = document.getElementById('paperQuestionStream');
  if (!scroller) return false;
  const box = scroller.getBoundingClientRect();
  const rows = document.querySelectorAll('#paperStreamVirtualRoot .paper-stream-row');
  if (!rows.length) return false;
  let waiting = 0;
  for (const row of rows) {
    const rect = row.getBoundingClientRect();
    if (rect.bottom <= box.top || rect.top >= box.bottom) continue;
    if (row.querySelector('[aria-busy="true"]')) waiting += 1;
  }
  return waiting === 0;
}
"""

CART_STATE_JS = r"""
() => {
    const state = window.PaperStore.cartQuestionLoad;
    return {
        cartIds: window.PaperStore.cart.length,
        hydrated: Object.keys(window.PaperStore.questionsMap).length,
        missing: state.missingIds.length,
        confirmedMissing: state.confirmedMissingIds.length,
        failed: state.failedIds.length,
        canvasQuestions: document.querySelectorAll('#paperCanvasSection .paper-q-item').length
    };
}
"""


def _json_probe(page, probe: str, timeout_ms: int, arg=None):
    """轮询式取快照。老架构跑万题时主线程会被渲染压死，`page.evaluate` 可能永远排在
    队列里；`wait_for_function` 以 JSON 字符串形式轮询，只要长任务之间有一丝空隙就能
    取到现场数据，超时返回 None 而不是把整个压测吊死。"""
    if arg is None:
        expr = "async () => JSON.stringify(await (%s)())" % probe.strip()
        raw = page.wait_for_function(expr, timeout=timeout_ms)
    else:
        # 滚动探针只许跑一次：把 Promise 缓存在页面里，避免超时轮询重复发起滚动实验。
        expr = ("async (params) => { if (!window.__stressProbePromise) "
                "window.__stressProbePromise = (%s)(params); "
                "return JSON.stringify(await window.__stressProbePromise); }") % probe.strip()
        raw = page.wait_for_function(expr, arg=arg, timeout=timeout_ms)
    if hasattr(raw, "json_value"):
        raw = raw.json_value()
    return json.loads(raw)


VISIBLE_BUSY_JS = r"""
() => {
  const scroller = document.getElementById('paperQuestionStream');
  if (!scroller) return {ready: false, visible: 0, busy: -1, katex: 0};
  const box = scroller.getBoundingClientRect();
  const rows = document.querySelectorAll('#paperStreamVirtualRoot .paper-stream-row');
  const katex = document.querySelectorAll('#paperQuestionStream .katex').length;
  let waiting = 0;
  let visible = 0;
  for (const row of rows) {
    const rect = row.getBoundingClientRect();
    if (rect.bottom <= box.top || rect.top >= box.bottom) continue;
    visible += 1;
    if (row.querySelector('[aria-busy="true"]')) waiting += 1;
  }
  return {ready: visible > 0 && waiting === 0, visible: visible, busy: waiting, katex: katex};
}
"""


def wait_visible_hydrated(page, started, timeout_ms: int):
    """轮询等待可视区 aria-busy 清零，并周期性轻推滚动容器。

    headless Chromium 会在页面 idle 后重度节流 rAF，而组卷流的 KaTeX 排版与
    「140 字预览 → 完整题面」的行重建都由 rAF 驱动；纯 `wait_for_function`（raf
    轮询）会与「rAF 停摆」形成死锁，页面永远读不到推进一步。真实用户始终在看、
    在滚，页面每帧都在推进，所以这里用 4px 来回轻推（净位移为 0）模拟该稳态，
    每轮再用 `evaluate` 主动跑一次页面 JS 读取最新 DOM。
    """
    deadline = time.monotonic() + timeout_ms / 1000.0
    i = 0
    while time.monotonic() < deadline:
        i += 1
        page.evaluate(
            "(k) => { const el = document.getElementById('paperQuestionStream');"
            " if (el) el.scrollTop += (k % 2 ? 4 : -4); }",
            i,
        )
        state = page.evaluate(VISIBLE_BUSY_JS)
        if state.get("ready"):
            return page.evaluate("(t0) => performance.now() - t0", started)
        time.sleep(0.26)
    return None


def measure(mode: str, port: int, scroll_ms: int, scroll_step: int, cart_size: int,
            first_timeout_ms: int = 180000) -> dict:
    from playwright.sync_api import sync_playwright

    url = f"http://127.0.0.1:{port}/" + (("?perfLegacyStream=1") if mode == "legacy" else "")
    report: dict = {"mode": mode, "url": url, "first_timeout_ms": first_timeout_ms}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--enable-precise-memory-info", "--js-flags=--expose-gc"],
        )
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        api_rows = []

        def on_response(response):
            path = response.url.split("?")[0]
            if path.endswith(("/api/questions", "/api/paper/questions", "/healthz")):
                body = b""
                try:
                    body = response.body()
                except Exception:
                    body = b""
                api_rows.append({
                    "url": response.url[:160],
                    "status": response.status,
                    "bytes": len(body),
                    "duration_ms": round(response.request.timing.get("responseEnd", -1), 1)
                    if isinstance(response.request.timing, dict) else None,
                })

        page.on("response", on_response)
        # 购物车必须**先落在 localStorage 里、再让页面加载**：paper.js 在
        # DOMContentLoaded 就把车读进内存，种子写晚了会被随后的 saveCartToStorage
        # （空车 = removeItem）覆盖回去，压测就变成「零题购物车」的假数据。
        page.goto(url, wait_until="networkidle")
        seeded_cart = page.evaluate(SEED_CART_JS, {"size": cart_size, "deadId": 99999999})
        page.reload(wait_until="networkidle")
        cart_in_storage = page.evaluate(CART_STORAGE_SIZE_JS)

        started = page.evaluate("() => performance.now()")
        page.evaluate("() => { window.selectWorkspace('paper', '组卷排版工作台'); }")

        # —— 首屏两个关键耗时：legacy 在万题下可能永远等不到，超时本身就是对照结论 ——
        stalled = {"first_card": False, "visible_hydrated": False}
        first_paint_ms = None
        try:
            page.wait_for_selector("#paperStreamVirtualRoot .paper-stream-row", timeout=first_timeout_ms)
            first_paint_ms = page.evaluate("(t0) => performance.now() - t0", started)
        except Exception:
            stalled["first_card"] = True

        hydrated_ms = None
        if first_paint_ms is not None:
            hydrated_ms = wait_visible_hydrated(page, started, first_timeout_ms)
            if hydrated_ms is None:
                stalled["visible_hydrated"] = True

        # —— 现场快照一律走轮询探针：主线程停摆就取 None，绝不吊死脚本 ——
        try:
            dom_after_load = _json_probe(page, DOM_PROBE, 60000)
        except Exception:
            dom_after_load = None
        try:
            cart_state = _json_probe(page, CART_STATE_JS, 60000)
        except Exception:
            cart_state = None

        scroll = None
        dom_after_scroll = None
        if not stalled["first_card"]:
            try:
                scroll = _json_probe(page, SCROLL_PROBE, scroll_ms * 8 + 30000,
                                     arg={"ms": scroll_ms, "step": scroll_step})
            except Exception:
                scroll = None
            if scroll is not None:
                try:
                    dom_after_scroll = _json_probe(page, DOM_PROBE, 60000)
                except Exception:
                    dom_after_scroll = None

        browser.close()

    report.update({
        "seeded_cart": seeded_cart,
        "cart_in_storage": cart_in_storage,
        "stalled": stalled,
        "first_card_ms": round(first_paint_ms, 1) if first_paint_ms is not None else None,
        "visible_hydrated_ms": round(hydrated_ms, 1) if hydrated_ms is not None else None,
        "dom_after_load": dom_after_load,
        "dom_after_scroll": dom_after_scroll,
        "cart": cart_state,
        "scroll": scroll,
        "api": api_rows,
        "api_total_bytes": sum(row["bytes"] for row in api_rows),
    })
    return report


def print_report(report: dict) -> None:
    print(f"\n===== 组卷页压测 [{report['mode']}] =====")
    stalled = report.get("stalled") or {}
    wait_s = (report.get("first_timeout_ms") or 180000) / 1000
    if report.get("first_card_ms") is None:
        print(f"  首屏第一张卡片      : 未出首屏（等待 {wait_s:.0f}s 超时；老架构在万题下停摆，这本身就是结论）")
    else:
        print(f"  首屏第一张卡片      : {report['first_card_ms']:.1f} ms")
    if report.get("visible_hydrated_ms") is None:
        if stalled.get("first_card"):
            print("  可视区题面水合完成  : 不适用（未出首屏）")
        else:
            print(f"  可视区题面水合完成  : 未完成（等待 {wait_s:.0f}s 超时）")
    else:
        print(f"  可视区题面水合完成  : {report['visible_hydrated_ms']:.1f} ms")
    scroll = report.get("scroll")
    if scroll is None:
        print("  滚动帧时长 p50/p95  : 不可测（未出首屏或主线程停摆）")
    else:
        print(f"  滚动帧时长 p50/p95  : {scroll['p50']:.2f} / {scroll['p95']:.2f} ms   (p99 {scroll['p99']:.2f} ms, 最长 {scroll['worst']:.1f} ms)")
        print(f"  滚动帧率 / 掉帧     : {scroll['fps']:.1f} fps, >50ms 帧 {scroll['dropped']} 个（共 {scroll['frames']} 帧）")
        print(f"  long task           : {len(scroll['longTasks'])} 个，累计 {sum(t['dur'] for t in scroll['longTasks']):.0f} ms")
    dom_load = report.get("dom_after_load") or {}
    dom_scroll = report.get("dom_after_scroll") or {}
    print(f"  DOM 节点（载入/滚动后）: {dom_load.get('domNodes', '-')} / {dom_scroll.get('domNodes', '-')}")
    print(f"  窗口内题目行        : {dom_load.get('renderedRows', '-')} → {dom_scroll.get('renderedRows', '-')}")
    print(f"  KaTeX 节点          : {dom_load.get('katexNodes', '-')} → {dom_scroll.get('katexNodes', '-')}")
    js_heap = dom_scroll.get("jsHeapMb") if dom_scroll else None
    if js_heap is None and dom_load:
        js_heap = dom_load.get("jsHeapMb")
    print(f"  JS 堆               : {js_heap if js_heap is not None else '-'} MB")
    print(f"  接口累计传输        : {report['api_total_bytes'] / 1024:.1f} KB（{len(report['api'])} 次请求）")
    cart = report.get("cart")
    if cart is None:
        print("  购物车              : 探针超时未取到（主线程停摆）")
    else:
        print(f"  购物车              : {cart['cartIds']} 条 → 水合 {cart['hydrated']}，缺失待取 {cart['missing']}，确认失效 {cart['confirmedMissing']}，读取失败 {cart['failed']}，卷面渲染 {cart['canvasQuestions']}")
        print(f"  购物车持久化        : 种子 {report['seeded_cart']} 条 → 重载后 localStorage 仍有 {report['cart_in_storage']} 条"
              "（分页改造绝不能把刷新后的车清空）")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="组卷页万题压测")
    parser.add_argument("--mode", choices=("new", "legacy"), default="new")
    parser.add_argument("--db", default=DEFAULT_STRESS_DB)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--scroll-ms", type=int, default=3000)
    parser.add_argument("--scroll-step", type=int, default=220)
    parser.add_argument("--cart-size", type=int, default=12)
    parser.add_argument("--first-timeout", type=int, default=180000,
                    help="等待首屏/可视区水合的毫秒上限；老架构超时会记 stalled 并照常出报告")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)

    if not (PROJECT_ROOT / args.db).exists():
        raise SystemExit(f"压测库不存在：{args.db}，先跑 python -m scripts.seed_perf_load --target 10000")

    port = args.port or free_port()
    server = start_server(port, args.db)
    try:
        report = measure(args.mode, port, args.scroll_ms, args.scroll_step,
                           args.cart_size, args.first_timeout)
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except Exception:
            server.kill()

    print_report(report)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    history = []
    if output.exists():
        try:
            existing = json.loads(output.read_text(encoding="utf-8"))
            history = existing if isinstance(existing, list) else [existing]
        except Exception:
            history = []
    history.append(report)
    output.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n原始数据: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
