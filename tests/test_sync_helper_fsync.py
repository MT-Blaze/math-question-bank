# -*- coding: utf-8 -*-
"""Regression test for the Windows-only fsync bug in sync_helper.py.

`_fsync_file` opened a freshly written file with "rb" and called os.fsync(),
which raises OSError: [Errno 9] Bad file descriptor on Windows and made every
post-commit JSON/Markdown export fail.  Fixed by opening with "r+b".
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mathbank.sync_helper import _fsync_file  # noqa: E402


def test_fsync_file_readwrite_handle(tmp_path: Path) -> None:
    """_fsync_file must complete without raising on Windows-style handles."""
    target = tmp_path / "export.md"
    target.write_text("# 题目\n", encoding="utf-8")
    _fsync_file(str(target))  # must not raise OSError / EBADF
    assert target.read_text(encoding="utf-8") == "# 题目\n"
