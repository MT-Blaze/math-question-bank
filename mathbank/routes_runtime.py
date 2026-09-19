"""App-local Pandoc runtime endpoints for Word-native formula conversion."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from mathbank.runtime_components import PANDOC_INSTALL_MANAGER, pandoc_status

router = APIRouter(tags=["runtime"])


@router.get("/api/runtime/pandoc/status")
def get_pandoc_runtime_status():
    """Report whether Word-native formula conversion is currently available."""
    install_state = PANDOC_INSTALL_MANAGER.snapshot()
    if install_state.get("status") in {"queued", "downloading", "verifying"}:
        return {"status": "success", "pandoc": install_state}
    return {"status": "success", "pandoc": pandoc_status()}


@router.post("/api/runtime/pandoc/install")
def install_pandoc_runtime():
    """Start or join the single verified app-local Pandoc installation task."""
    state = PANDOC_INSTALL_MANAGER.ensure()
    status_code = 200 if state.get("status") == "ready" else 202
    return JSONResponse(
        status_code=status_code,
        content={"status": "success", "pandoc": state},
    )


@router.get("/api/runtime/pandoc/install/{task_id}")
def get_pandoc_install_status(task_id: str):
    state = PANDOC_INSTALL_MANAGER.snapshot()
    if not state.get("task_id") or state.get("task_id") != task_id:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": "Pandoc 安装任务不存在或已过期。"},
        )
    return {"status": "success", "pandoc": state}
