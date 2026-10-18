# -*- coding: utf-8 -*-
r"""考向树 JSON 单一数据源自检（只读，服务停止后跑）。

用途：TXT ↔ Web 大纲同步链路已下线，本脚本验证三件事——

1. ``import main`` 真的能跑起来（启动路径不再触发 TXT 拉取）；
2. 启动前后 `data_backup/custom_metadata.json` 与全部教材预设 JSON **逐字节不变**；
3. 项目目录里不存在任何被产品代码读取的 ``考向树/*.txt``。

从项目根目录运行::

    python3 -m scripts.check_kaoxiang_json_only

退出码 0 = 通过；1 = 检测到写入（隐患回归）；2 = 无法验证（例如服务仍在运行）。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

JSON_TARGETS = [
    PROJECT_ROOT / "data_backup" / "custom_metadata.json",
    *sorted((PROJECT_ROOT / "mathbank" / "resources" / "curriculums").glob("*.json")),
]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot() -> dict[str, str]:
    return {str(p.relative_to(PROJECT_ROOT)): _digest(p) for p in JSON_TARGETS if p.exists()}


def main() -> int:
    before = _snapshot()
    print("[check] 快照 %d 个 JSON 文件" % len(before))

    result = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", "-c", "import main"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()[-6:]
        if any("RuntimeLockError" in line for line in tail):
            print("[check] 跳过：本地服务正在运行，runtime lock 被占用。请先用网页电源键关闭服务再跑。")
            for line in tail:
                print("        " + line[:120])
            return 2
        print("[check] 失败：import main 退出码 %d" % result.returncode)
        for line in tail:
            print("        " + line[:160])
        return 1

    after = _snapshot()
    changed = {k: (before.get(k, "<absent>")[:12], after.get(k, "<absent>")[:12])
               for k in set(before) | set(after) if before.get(k) != after.get(k)}
    if changed:
        print("[check] 失败：启动改写了 JSON 大纲（TXT 覆盖隐患回归）")
        for key, (old, new) in sorted(changed.items()):
            print("        %s %s -> %s" % (key, old, new))
        return 1

    tree_dir = PROJECT_ROOT / "考向树"
    leftovers = sorted(f.name for f in tree_dir.glob("*.txt")) if tree_dir.exists() else []
    if leftovers:
        print("[check] 失败：JSON 为唯一来源的前提被打破——项目目录里出现了 %d 个 TXT" % len(leftovers))
        for name in leftovers[:8]:
            print("        " + name)
        return 1

    print("[check] 通过：启动零写入，JSON 是唯一数据源")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
