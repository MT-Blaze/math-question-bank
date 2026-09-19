# -*- coding: utf-8 -*-
"""考向树 TXT 底层文件的同步接口。

* ``GET  /api/kaoxiang/status`` —— 每个 TXT 与 Web 大纲的比对状态
* ``POST /api/kaoxiang/sync``   —— 手动触发 pull（TXT→Web）/ push（Web→TXT）
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from mathbank.auth import LOCAL_TOKEN
from mathbank.kaoxiang_tree import ensure_tree_dir, push_to_txt, pull_from_txt, status

router = APIRouter(tags=["kaoxiang"])


def _check_token(x_local_token: str | None) -> None:
    """本地令牌校验：只有本机发起的请求可以改写底层 TXT。"""

    if LOCAL_TOKEN and x_local_token != LOCAL_TOKEN:
        raise HTTPException(status_code=403, detail="缺少有效的本地访问令牌")


@router.get("/api/kaoxiang/status")
def get_kaoxiang_status():
    ensure_tree_dir()
    return status()


@router.post("/api/kaoxiang/sync")
def sync_kaoxiang(
    payload: dict | None = None,
    x_local_token: str = Header(None, alias="X-Local-Token"),
):
    _check_token(x_local_token)
    body = payload or {}
    direction = str(body.get("direction") or "pull").lower()
    if direction == "push":
        result = push_to_txt()
    elif direction == "pull":
        result = pull_from_txt()
    else:
        raise HTTPException(status_code=400, detail=f"不支持的同步方向: {direction}")
    result["direction"] = direction
    result["status"] = status()
    return result
