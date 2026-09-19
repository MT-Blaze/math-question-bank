import threading
import time
from pathlib import Path

import pytest

from mathbank.task_manager import (
    TaskCancelled,
    TaskManager,
    TaskQueueFull,
)


def test_cancelled_task_cannot_be_overwritten_as_complete():
    manager = TaskManager(max_workers=1, max_queue=0)
    manager.create("task-1", document_type="pdf")
    cancelled = manager.cancel("task-1")

    assert cancelled["status"] == "cancelled"
    assert manager.complete("task-1", data=[1, 2, 3]) is False
    assert manager.snapshot("task-1")["status"] == "cancelled"
    with pytest.raises(TaskCancelled):
        manager.check_cancelled("task-1")
    manager.shutdown()


def test_task_queue_is_bounded_and_releases_capacity():
    manager = TaskManager(max_workers=1, max_queue=0)
    started = threading.Event()
    release = threading.Event()

    def blocking_job():
        started.set()
        release.wait(timeout=2)

    manager.create("task-1")
    future = manager.submit("task-1", blocking_job)
    assert started.wait(timeout=1)
    manager.create("task-2")
    with pytest.raises(TaskQueueFull):
        manager.submit("task-2", lambda: None)

    release.set()
    future.result(timeout=2)
    second = manager.submit("task-2", lambda: "ok")
    assert second.result(timeout=2) == "ok"
    manager.shutdown()


def test_snapshot_is_isolated_from_internal_state():
    manager = TaskManager(max_workers=1, max_queue=0)
    manager.create("task-1", data={"items": [1]})

    snapshot = manager.snapshot("task-1")
    snapshot["data"]["items"].append(2)

    assert manager.snapshot("task-1")["data"] == {"items": [1]}
    manager.shutdown()


def test_terminal_state_rejects_late_updates_and_repeated_cancel():
    manager = TaskManager(max_workers=1, max_queue=0)
    manager.create("task-1", log="running")
    assert manager.complete("task-1", data=[1]) is True
    completed = manager.snapshot("task-1")

    assert manager.update("task-1", progress=25, log="late worker") is False
    assert manager.fail("task-1", "late error") is False
    cancelled = manager.cancel("task-1")
    assert cancelled == {
        key: value
        for key, value in completed.items()
        if key not in {"created_at", "updated_at"}
    }
    assert manager.snapshot("task-1") == completed
    manager.shutdown()


def test_duplicate_submit_is_rejected_without_leaking_queue_capacity():
    manager = TaskManager(max_workers=1, max_queue=1)
    started = threading.Event()
    release = threading.Event()

    def blocking_job():
        started.set()
        release.wait(timeout=2)

    manager.create("task-1")
    first = manager.submit("task-1", blocking_job)
    assert started.wait(timeout=1)
    with pytest.raises(ValueError, match="已提交"):
        manager.submit("task-1", lambda: None)

    manager.create("task-2")
    second = manager.submit("task-2", lambda: "queued")
    release.set()
    first.result(timeout=2)
    assert second.result(timeout=2) == "queued"
    assert manager.snapshot("task-1")["status"] == "completed"
    assert manager.snapshot("task-2")["status"] == "completed"
    manager.shutdown()


def test_unhandled_worker_error_becomes_terminal_and_releases_capacity():
    manager = TaskManager(max_workers=1, max_queue=0)
    manager.create("task-1")

    def fail_job():
        raise LookupError("private detail")

    failed = manager.submit("task-1", fail_job)
    with pytest.raises(LookupError):
        failed.result(timeout=2)
    assert manager.snapshot("task-1")["status"] == "error"
    assert manager.snapshot("task-1")["error"].endswith("LookupError")

    manager.create("task-2")
    assert manager.submit("task-2", lambda: "ok").result(timeout=2) == "ok"
    manager.shutdown()


def test_temp_assets_are_deduplicated_concurrently_after_terminal_state():
    manager = TaskManager(max_workers=1, max_queue=0)
    manager.create("task-1", temp_assets=[])
    assert manager.complete("task-1") is True

    threads = [
        threading.Thread(
            target=manager.add_temp_asset,
            args=("task-1", f"/tmp/crop-{index % 5}.png"),
        )
        for index in range(100)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    snapshot = manager.snapshot("task-1")
    assert snapshot["status"] == "completed"
    assert len(snapshot["temp_assets"]) == 5
    assert set(snapshot["temp_assets"]) == {
        f"/tmp/crop-{index}.png" for index in range(5)
    }
    assert manager.add_temp_asset("missing", "/tmp/missing.png") is False
    manager.shutdown()


def test_terminal_eviction_cleans_temp_assets_for_ttl_and_record_capacity():
    cleaned = []
    manager = TaskManager(
        max_workers=1,
        max_queue=0,
        terminal_ttl_seconds=0,
        max_records=1,
        temp_asset_cleanup=lambda paths: cleaned.append(paths),
    )
    manager.create("ttl-task", temp_assets=["/tmp/a.png", "/tmp/a.png"])
    manager.complete("ttl-task")

    assert manager.cleanup() == 1
    assert cleaned == [["/tmp/a.png"]]
    manager.shutdown()

    capacity_manager = TaskManager(
        max_workers=1,
        max_queue=0,
        terminal_ttl_seconds=3600,
        max_records=1,
        temp_asset_cleanup=lambda paths: cleaned.append(paths),
    )
    capacity_manager.create("capacity-task", temp_assets=["/tmp/b.png"])
    capacity_manager.complete("capacity-task")
    capacity_manager.create("replacement-task")

    assert cleaned == [["/tmp/a.png"], ["/tmp/b.png"]]
    assert capacity_manager.snapshot("capacity-task") is None
    capacity_manager.shutdown()


def test_periodic_maintenance_cleans_expired_assets_without_new_tasks():
    cleaned = threading.Event()
    manager = TaskManager(
        max_workers=1,
        max_queue=0,
        terminal_ttl_seconds=0,
        max_records=1,
        temp_asset_cleanup=lambda _paths: cleaned.set(),
    )
    manager.create("idle-terminal", temp_assets=["/tmp/idle.png"])
    manager.complete("idle-terminal")

    manager.start_maintenance(interval_seconds=0.01)

    assert cleaned.wait(timeout=1)
    assert manager.snapshot("idle-terminal") is None
    manager.shutdown()


def test_latest_returns_recent_completed_task_with_task_id():
    manager = TaskManager(max_workers=1, max_queue=0)
    manager.create("pdf-old", document_type="pdf")
    manager.complete("pdf-old", data=[1])
    manager.create("docx-new", document_type="docx")
    manager.complete("docx-new", data=[2])

    latest_pdf = manager.latest(document_type="pdf", statuses={"completed"})
    assert latest_pdf is not None
    assert latest_pdf["task_id"] == "pdf-old"
    assert latest_pdf["document_type"] == "pdf"
    assert latest_pdf["data"] == [1]

    # 只过滤 docx 时应返回 docx 任务。
    latest_docx = manager.latest(document_type="docx", statuses={"completed"})
    assert latest_docx["task_id"] == "docx-new"

    # 过滤不存在的类型时返回 None，供接口回 404。
    assert manager.latest(document_type="xlsx", statuses={"completed"}) is None
    manager.shutdown()


def test_status_transitions_record_per_stage_timings():
    """阶段切换必须记下"这一步用了多久 + 总计用了多久"。

    用户要求：整张卷子录入的每个步骤各用了多长时间、总计用了多长时间。
    时间戳由后端在阶段切换时采集，避免前端按轮询间隔推算（不准）。
    """

    from mathbank.task_manager import TaskManager, format_duration

    manager = TaskManager()
    task_id = "timing-task"
    manager.create(task_id, document_type="pdf")

    manager.update(task_id, status="processing_images", log="正在渲染 PDF 高清页面...")
    time.sleep(0.05)
    manager.update(task_id, status="ocr_extraction", log="开始视觉 OCR...")
    time.sleep(0.05)
    manager.complete(task_id, log="完成！")

    snap = manager.snapshot(task_id)
    timings = snap.get("timings")
    assert isinstance(timings, list)
    stages = [item["stage"] for item in timings]
    # 首次切换结掉 queued，其后依次结掉 processing_images / ocr_extraction
    assert stages == ["queued", "processing_images", "ocr_extraction"]
    # 每段都带可读标签与不小于 0 的秒数
    for item in timings:
        assert isinstance(item["seconds"], (int, float))
        assert item["seconds"] >= 0
    assert timings[1]["label"] == "正在渲染 PDF 高清页面..."
    # 总计耗时随阶段推进单调不减，且是数字
    assert isinstance(snap.get("elapsed_seconds"), (int, float))
    assert snap["elapsed_seconds"] >= 0
    # 同一阶段内反复更新进度不得刷出新的计时条目
    manager2 = TaskManager()
    task2 = "timing-task-2"
    manager2.create(task2)
    manager2.update(task2, status="processing_images", progress=10)
    manager2.update(task2, status="processing_images", progress=20)
    manager2.update(task2, status="processing_images", progress=30)
    assert [item["stage"] for item in manager2.snapshot(task2)["timings"]] == ["queued"]

    # 格式化：秒 / 分
    assert format_duration(15.34) == "15.3s"
    assert format_duration(67.2) == "1分07.2秒"
    assert format_duration(-5) == "0.0s"


def test_pdf_parse_logs_report_step_durations_and_a_total():
    """PDF 拆解的关键阶段日志必须带用时，最后一行必须给出分步明细与总计。"""

    source = Path("mathbank/routes_paper.py").read_text(encoding="utf-8")

    assert "from mathbank.task_manager import TaskCancelled, TaskQueueFull, format_duration" in source
    assert "def _lap() -> str:" in source
    # 各阶段都落一次 lap
    assert "render_took = _lap()" in source
    assert "extract_took = _lap()" in source
    assert "text_ready_took = _lap()" in source
    assert "split_took = _lap()" in source
    # 日志里真的带上了用时与总计
    assert "分步耗时：渲染 {render_took}" in source
    assert "总计 {total_took}" in source
    assert "原生文字公式提取完成" in source
