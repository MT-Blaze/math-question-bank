"""Centralized, working-directory-independent paths for MathBank.

All persistent data and bundled resources are anchored to the project root so
the server and command-line tools behave identically regardless of the shell's
current working directory.
"""

import os
import re
import sys
import uuid
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Single source of truth for "running under pytest" so both main.py and the
# split mathbank modules compute it identically.
IS_TESTING = "pytest" in sys.modules or any("pytest" in arg for arg in sys.argv)

STATIC_DIR = PROJECT_ROOT / "static"
STATIC_JS_DIR = STATIC_DIR / "js"
STATIC_CSS_DIR = STATIC_DIR / "css"
UPLOADS_DIR = STATIC_DIR / "uploads"
TEST_UPLOADS_DIR = STATIC_DIR / "test_uploads"

TEMPLATES_DIR = PROJECT_ROOT / "templates"
DATA_BACKUP_DIR = PROJECT_ROOT / "data_backup"
FULL_BACKUP_DIR = DATA_BACKUP_DIR / "snapshots"
PRE_RESTORE_BACKUP_DIR = DATA_BACKUP_DIR / "pre_restore"
SCHEMA_SNAPSHOT_DIR = DATA_BACKUP_DIR / "schema_snapshots"
SYSTEM_GENERATED_DIR = PROJECT_ROOT / ".system_generated"

# 压测与运维脚本需要把数据库指向独立文件（例如注入 10,000 道合成题的压测库），
# 绝不能污染教师真实题库。未设置该环境变量时，落点与历史行为逐字一致。
_ENV_DATABASE_FILE = os.environ.get("MATHBANK_DATABASE_FILE", "").strip()
if _ENV_DATABASE_FILE:
    _database_override = Path(_ENV_DATABASE_FILE)
    DATABASE_FILE = (
        _database_override
        if _database_override.is_absolute()
        else PROJECT_ROOT / _database_override
    )
else:
    DATABASE_FILE = PROJECT_ROOT / "math_question_bank.db"
DATABASE_PATH = DATABASE_FILE
ENV_FILE = PROJECT_ROOT / ".env"

# 运行时互斥锁守护的是「那一个数据库」，所以锁文件名必须跟着数据库走。
# 默认题库沿用历史路径 runtime.lock（正式服务、恢复 CLI、旧脚本三方对得上，逐字不变）；
# 只有显式改用 MATHBANK_DATABASE_FILE 另起实例时，才换到独立锁文件——否则压测实例
# 会和教师正在跑的正式服务抢同一把锁，第二个实例直接起不来。
RUNTIME_LOCK_FILE = (
    SYSTEM_GENERATED_DIR / "runtime.lock"
    if not _ENV_DATABASE_FILE
    else SYSTEM_GENERATED_DIR / f"runtime-{DATABASE_FILE.stem}.lock"
)

# Stable per-process server identity. Computed once at import so both the app
# entry point and the split route routers observe the same value.
_ENV_LAUNCH_ID = os.environ.get("MATHBANK_LAUNCH_ID", "").strip().lower()
SERVER_INSTANCE_ID = (
    _ENV_LAUNCH_ID
    if re.fullmatch(r"[0-9a-f]{32}", _ENV_LAUNCH_ID)
    else uuid.uuid4().hex
)

DIST_DIR = PROJECT_ROOT / "dist"
BUILD_CACHE_DIR = PROJECT_ROOT / ".build_cache"
CURRICULUMS_DIR = Path(__file__).resolve().parent / "resources" / "curriculums"


def project_path(*parts: str) -> Path:
    """Return an absolute path below the MathBank project root."""

    return PROJECT_ROOT.joinpath(*parts)


def sqlite_url(path: Path = DATABASE_FILE) -> str:
    """Build a cross-platform SQLAlchemy URL for an absolute SQLite path."""

    return f"sqlite:///{path.resolve().as_posix()}"
