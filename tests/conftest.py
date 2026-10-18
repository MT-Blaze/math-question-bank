import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker
from mathbank import database
from mathbank.database import Base, Question

# Configure database module to use in-memory SQLite with StaticPool for tests
test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

# Patch the database module's engine and SessionLocal
database.engine = test_engine
database.SessionLocal = TestSessionLocal


@pytest.fixture(scope="session", autouse=True)
def dispose_test_engine_after_session():
    yield
    test_engine.dispose()


# Patch sync_helper output paths globally during testing to avoid overwriting or clearing real data_backup files
from mathbank import sync_helper
import tempfile
import os
test_backup_dir = tempfile.mkdtemp()
sync_helper.BACKUP_DIR = test_backup_dir
sync_helper.JSON_BACKUP_PATH = os.path.join(test_backup_dir, "questions_backup.json")
sync_helper.MD_BACKUP_PATH = os.path.join(test_backup_dir, "questions_library.md")

# 完整备份同样必须改道：`create_full_backup()` 的默认参数取的是**真实** FULL_BACKUP_DIR，
# 漏掉这一步时，任何走到「删卷前建备份」的用例都会把**真实库**打包进 data_backup/snapshots/
# （实测一次反向验证留下 10 个 ZIP、11.9 MB）。
# 改道必须落在 `mathbank.paths` 这一层，而不是只覆盖 `mathbank.backup` 里的那份引用：
# backup.py 用 `database/{DATABASE_FILE.name}` 作为归档成员名（它和 verify/restore 两侧都
# 要按这个名字对上），所以只换值会让所有显式传 database_path 的备份用例名字对不上。
# 保持真名、只换父目录，既让默认落点离开项目，又不改变任何成员名。
from pathlib import Path as _Path
from mathbank import paths as _paths_mod

_TEST_DATA_BACKUP_DIR = _Path(tempfile.mkdtemp()) / "data_backup"
_paths_mod.DATA_BACKUP_DIR = _TEST_DATA_BACKUP_DIR
_paths_mod.FULL_BACKUP_DIR = _TEST_DATA_BACKUP_DIR / "snapshots"
_paths_mod.PRE_RESTORE_BACKUP_DIR = _TEST_DATA_BACKUP_DIR / "pre_restore"
_paths_mod.SCHEMA_SNAPSHOT_DIR = _TEST_DATA_BACKUP_DIR / "schema_snapshots"


def test_backups_stay_outside_the_repository():
    """夹具自检：测试期间的备份根目录绝不能落在项目内（防复发钉）。"""
    project_root = _Path(__file__).resolve().parents[1].resolve()
    assert not str(_paths_mod.DATA_BACKUP_DIR).startswith(str(project_root))
    assert not str(_paths_mod.FULL_BACKUP_DIR).startswith(str(project_root))
    assert _paths_mod.DATABASE_FILE.name == "math_question_bank.db"

@pytest.fixture(scope="function")
def db_session():
    # Create all tables in the in-memory database
    Base.metadata.create_all(bind=test_engine)
    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)

@pytest.fixture(scope="function")
def client(db_session):
    from fastapi.testclient import TestClient
    from main import app
    from mathbank.database import get_db

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
