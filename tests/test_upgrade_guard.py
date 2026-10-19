# -*- coding: utf-8 -*-
r"""scripts/upgrade_guard.py 的回归契约。

fixture 直接用本机官方便携包目录（默认 ``E:\math-question-bank``，可用环境变量
``MATHBANK_UPGRADE_SAMPLE_DIR`` 覆盖）。别人机器上没有这个目录时整套真实样本用例
**优雅 skip**，不许因此变红。

铁律：本文件只读仓库，绝不修改任何被检文件；合成场景一律写在 pytest 的 tmp_path 里。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import release_overlay, upgrade_guard


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RELEASE_DIR = Path(
    os.environ.get("MATHBANK_UPGRADE_SAMPLE_DIR", r"E:\math-question-bank")
).resolve()
RELEASE_MANIFEST = RELEASE_DIR / upgrade_guard.MANIFEST_NAME
HAS_SAMPLE = RELEASE_MANIFEST.is_file()

requires_sample = pytest.mark.skipif(
    not HAS_SAMPLE,
    reason="本机没有官方便携包样本 %s（可用 MATHBANK_UPGRADE_SAMPLE_DIR 指定）"
    % RELEASE_MANIFEST,
)

# 深度二开的代表：这三个必须出现在冲突清单里，漏一个就是漏报。
DEEP_FORKS = ("main.py", "mathbank/paper_helper.py", "static/js/import.js")
# 我们独有的模块：出现在冲突清单里就是误报。
OURS_EXCLUSIVE = ("mathbank/pdf_local_split.py", "mathbank/parsing_pipeline.py")
# 只读性探针（脚本必须只读它们，跑完 sha256 与 mtime 都不能变）。
PROBE_FILES = ("main.py", "mathbank/database.py", "static/index.html")
# 实测的覆盖升级同名冲突下限（上游 v2.7.0 样本）。
MIN_CONFLICTS = 40


def _sha_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write(root: Path, relative: str, content: str) -> Path:
    path = root.joinpath(*relative.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _snapshot(paths) -> dict:
    """记录「内容哈希 + mtime + 体积」：只读性判定必须同时盯内容与时间戳。"""
    state = {}
    for relative in paths:
        path = PROJECT_ROOT / relative
        raw = path.read_bytes()
        metadata = path.stat()
        state[relative] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "mtime_ns": metadata.st_mtime_ns,
            "size": metadata.st_size,
        }
    return state


def _run_cli(args, cwd=PROJECT_ROOT) -> subprocess.CompletedProcess:
    """跑真 CLI（含退出码）。-B 连 .pyc 都不落盘，免得把「只读」验证搞脏。"""
    return subprocess.run(
        [sys.executable, "-B", "-X", "utf8", "-m", "scripts.upgrade_guard", *map(str, args)],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )


def _conflict_paths(report: dict) -> set:
    buckets = set()
    for key in ("manual_merge", "ours_only", "pending_confirmation"):
        buckets.update(item["path"] for item in report[key])
    return buckets


def _parse_json_block(stdout: str) -> dict:
    marker = "===== 机器可读 JSON ====="
    assert marker in stdout, "带 --json 却没输出机器可读段"
    return json.loads(stdout.split(marker, 1)[1])


def _build_scenario(tmp_path: Path, ours: dict, package: dict, baseline: dict = None):
    """造一套「假仓库 + 假升级包 + 可选基线」，用于合成用例（不碰真实仓库）。"""
    repo = tmp_path / "repo"
    root = tmp_path / "package"
    for relative, content in ours.items():
        _write(repo, relative, content)
    for relative, content in package.items():
        _write(root, relative, content)

    entries = []
    for relative in sorted(package):
        data = root.joinpath(*relative.split("/")).read_bytes()
        entries.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            }
        )
    managed = sorted({relative.split("/", 1)[0] for relative in package})
    payload = {
        "app_version": "9.9.9",
        "platform": "windows-x64",
        "schema_version": 1,
        "overlay": {
            "managed_roots": managed,
            "protected_paths": [
                ".env",
                ".system_generated",
                "data_backup",
                "math_question_bank.db",
                "math_question_bank.db-wal",
                "math_question_bank.db-shm",
                "static/uploads",
                "venv",
            ],
        },
        "files": entries,
    }
    manifest_path = root / upgrade_guard.MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )

    baseline_path = None
    if baseline is not None:
        # baseline 的 value：包内实际哈希／我方实际哈希／"other"（故意不等于两侧）。
        lines = []
        for relative, marker in baseline.items():
            if marker == "package":
                digest = next(item["sha256"] for item in entries if item["path"] == relative)
            elif marker == "ours":
                digest = _sha_of_text(ours[relative])
            else:
                digest = _sha_of_text("baseline-%s" % marker)
            lines.append({"path": relative, "sha256": digest})
        baseline_path = tmp_path / upgrade_guard.INSTALLED_MANIFEST_NAME
        baseline_path.write_text(
            json.dumps({"files": lines}, ensure_ascii=False), encoding="utf-8"
        )
    return repo, root, manifest_path, baseline_path


@pytest.fixture(scope="session")
def sample_manifest():
    return upgrade_guard.load_manifest(RELEASE_MANIFEST)


@pytest.fixture(scope="session")
def sample_degraded_report(sample_manifest):
    """真实样本 + 强制缺失基线：这就是本仓库当前的实际口径（二方比对）。"""
    missing = PROJECT_ROOT / upgrade_guard.STATE_DIRECTORY / "absent-baseline-for-tests.json"
    baseline = upgrade_guard.load_baseline(PROJECT_ROOT, missing)
    assert not baseline["available"]
    return upgrade_guard.build_report(
        PROJECT_ROOT, sample_manifest, RELEASE_DIR, baseline
    )


@pytest.fixture(scope="session")
def sample_cli_run():
    """跑一次真实 CLI，并把「跑前／跑后」的仓库快照一起带回来（只读性证据）。"""
    before = _snapshot(PROBE_FILES)
    result = _run_cli([RELEASE_DIR, "--json"])
    after = _snapshot(PROBE_FILES)
    return {"result": result, "before": before, "after": after}


@requires_sample
def test_manifest_parses_real_sample(sample_manifest):
    """1. manifest 解析：条目数、platform、app_version、schema_version 都要拿到。"""
    assert sample_manifest["file_count"] > 0
    assert len(sample_manifest["files"]) == sample_manifest["file_count"]
    assert sample_manifest["schema_version"] == 1
    assert sample_manifest["platform"]
    assert sample_manifest["app_version"]
    assert sample_manifest["managed_roots"] and sample_manifest["protected_roots"]
    assert len(sample_manifest["sha256"]) == 64
    for entry in sample_manifest["files"]:
        assert ".." not in entry["relative"].parts
        assert len(entry["sha256"]) == 64


@requires_sample
def test_no_under_report_on_real_repo(sample_degraded_report):
    """2. 不许漏报：同名冲突必须全部进清单，深度二开三件套一个都不能少。"""
    counts = sample_degraded_report["counts"]
    conflicts = _conflict_paths(sample_degraded_report)
    assert counts["manual_merge_b"] + counts["ours_only_c"] + counts["pending_confirmation"] >= MIN_CONFLICTS
    assert len(conflicts) >= MIN_CONFLICTS
    for relative in DEEP_FORKS:
        assert relative in conflicts, "漏报：%s" % relative


@requires_sample
def test_no_over_report_for_our_exclusive_modules(sample_degraded_report):
    """3. 不许误报：我们独有的模块既不在冲突清单，也不该被当成升级包新增。"""
    conflicts = _conflict_paths(sample_degraded_report)
    new_detail = set()
    for names in sample_degraded_report["new_in_release"]["detail"].values():
        new_detail.update(names)
    for relative in OURS_EXCLUSIVE:
        assert (PROJECT_ROOT / relative).is_file(), "用例前提变了：%s 已不在仓库" % relative
        assert relative not in conflicts, "误报成冲突：%s" % relative
        assert relative not in new_detail, "误报成升级包新增：%s" % relative


@requires_sample
def test_cli_is_strictly_read_only(sample_cli_run):
    """4. 纯只读：代表文件的 sha256 + mtime + 体积跑前跑后必须逐字节一致。"""
    assert sample_cli_run["result"].returncode in (0, 2)
    assert sample_cli_run["before"] == sample_cli_run["after"]
    for relative, state in sample_cli_run["before"].items():
        assert (PROJECT_ROOT / relative).is_file(), relative
        assert state["size"] > 0, relative
    # 预检不许替用户创建基线文件。
    assert not (
        PROJECT_ROOT / upgrade_guard.STATE_DIRECTORY / upgrade_guard.INSTALLED_MANIFEST_NAME
    ).exists()


@requires_sample
def test_real_repo_exit_code_two_and_degraded_header(sample_cli_run):
    """6. 退出码语义 + 降级声明：本仓库跑必然有需人工合并项 → 退出码 2。"""
    result = sample_cli_run["result"]
    assert result.returncode == 2, result.stdout[-800:]
    assert "基线缺失，已降级为二方比对" in result.stdout
    assert "结论：需人工合并" in result.stdout
    payload = _parse_json_block(result.stdout)
    assert payload["exit_code"] == 2
    assert payload["baseline"]["degraded_two_way"] is True
    assert payload["counts"]["needs_manual_attention"] >= MIN_CONFLICTS
    assert payload["release"]["app_version"]
    assert payload["release"]["hash_mode"] == "包内 sha256 实算"


def _accepts(function, value) -> bool:
    """把校验函数的「接受／拒绝」压成布尔值，方便与上游口径逐条对齐。"""
    try:
        function(value, "清单")
    except Exception:
        return False
    return True


_PATH_CASES = [
    "main.py",
    "mathbank/database.py",
    "mathbank/resources/curriculums/A.json",
    "启动题库系统.bat",
    "python/python.exe",
    "static/uploads/.gitkeep",
    "MAIN.PY",
    "./main.py",
    "../main.py",
    "a/../b.py",
    "a//b",
    "a/./b",
    "/abs/main.py",
    "C:/x.py",
    "a\\b.py",
    "\x00bad",
    "",
    None,
    123,
]


def _write_sample_baseline(tmp_path, manifest, overrides=()) -> Path:
    """拿真清单造一份基线文件（只写 tmp_path，不碰仓库）。

    默认「包里 == 基线」，也就是全世界都是 C 类；`overrides` 里的路径改成别的哈希，
    就变成「双方都改过」的 B 类，用来钉死最危险那一格的判定与退出码。
    """
    path = tmp_path / upgrade_guard.INSTALLED_MANIFEST_NAME
    entries = []
    for item in manifest["files"]:
        relative = item["relative"].as_posix()
        digest = item["sha256"]
        if relative in overrides:
            digest = _sha_of_text("not-the-package-bytes")
        entries.append({"path": relative, "sha256": digest})
    path.write_text(
        json.dumps({"app_version": "0.0.0", "files": entries}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def test_missing_manifest_is_a_hard_error(tmp_path):
    """5. 目录里没有 RELEASE-MANIFEST.json → 明确报错 + 退出码 1，绝不猜。"""
    fake = tmp_path / "not-a-package"
    fake.mkdir()
    result = _run_cli([fake, "--repo", PROJECT_ROOT])
    assert result.returncode == 1
    assert "RELEASE-MANIFEST.json" in result.stderr
    assert "该目录不是 MathBank 便携包" in result.stderr


def test_requires_an_explicit_source_of_truth(tmp_path):
    """没给目录也没给 --manifest → 退出码 1，不猜任何默认升级包位置。"""
    result = _run_cli(["--repo", tmp_path])
    assert result.returncode == 1
    assert "预检失败" in result.stderr


def test_positional_dir_and_manifest_are_mutually_exclusive(tmp_path):
    result = _run_cli([tmp_path, "--manifest", tmp_path / "x.json", "--repo", tmp_path])
    assert result.returncode == 1
    assert "二选一" in result.stderr


@pytest.mark.parametrize("value", _PATH_CASES)
def test_relative_path_rules_match_release_overlay(value):
    """路径口径必须与上游 release_overlay 一致（含拒绝 .. 防穿越）。"""
    assert _accepts(upgrade_guard._relative_path, value) == _accepts(
        release_overlay._relative_path, value
    )


def test_manifest_with_traversal_path_is_refused(tmp_path):
    root = tmp_path / "package"
    root.mkdir()
    payload = {
        "app_version": "9.9.9",
        "platform": "windows-x64",
        "schema_version": 1,
        "overlay": {"managed_roots": ["main.py"], "protected_paths": [".env"]},
        "files": [{"path": "../escape.py", "sha256": _sha_of_text("x"), "size": 1}],
    }
    manifest_path = root / upgrade_guard.MANIFEST_NAME
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(upgrade_guard.GuardError) as excinfo:
        upgrade_guard.load_manifest(manifest_path)
    assert "路径穿越" in str(excinfo.value)
    result = _run_cli(["--manifest", manifest_path, "--repo", tmp_path])
    assert result.returncode == 1
    assert "路径穿越" in result.stderr


def test_baseline_loader_degrades_instead_of_guessing(tmp_path):
    """基线不可用的每一种形态都必须如实标记为「不可用」，不许兜底造数据。"""
    missing = tmp_path / "nope.json"
    assert upgrade_guard.load_baseline(tmp_path, missing)["available"] is False

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    state = upgrade_guard.load_baseline(tmp_path, broken)
    assert state["available"] is False and "无法解析" in state["reason"]

    no_files = tmp_path / "nofiles.json"
    no_files.write_text("{}", encoding="utf-8")
    assert upgrade_guard.load_baseline(tmp_path, no_files)["available"] is False

    junk = tmp_path / "junk.json"
    junk.write_text(json.dumps({"files": [{"path": "../x", "sha256": "z" * 64}, 7]}), encoding="utf-8")
    assert upgrade_guard.load_baseline(tmp_path, junk)["available"] is False

    good = tmp_path / "good.json"
    good.write_text(
        json.dumps({"files": [{"path": "Main.py", "sha256": _sha_of_text("a")}]}),
        encoding="utf-8",
    )
    state = upgrade_guard.load_baseline(tmp_path, good)
    assert state["available"] is True
    assert state["index"]["main.py"] == _sha_of_text("a")


def test_three_way_buckets_and_exit_code_two(tmp_path):
    """三方比对：包里≠基线 → B 必须人工合并；包里==基线 → C 仅我方改过。"""
    repo, root, manifest_path, baseline_path = _build_scenario(
        tmp_path,
        ours={
            "main.py": "our forked main\n" * 3,
            "mathbank/ours_only.py": "we changed this\n",
            "mathbank/untouched.py": "same bytes\n",
            "static/js/both.py": "our version\n",
        },
        package={
            "main.py": "upstream main\n",
            "mathbank/ours_only.py": "upstream too\n",
            "mathbank/untouched.py": "same bytes\n",
            "static/js/both.py": "upstream version\n",
            "mathbank/new_module.py": "brand new\n",
        },
        baseline={
            "mathbank/ours_only.py": "package",
            "main.py": "something-else",
            "static/js/both.py": "ours",
        },
    )
    report = upgrade_guard.build_report(
        repo,
        upgrade_guard.load_manifest(manifest_path),
        root,
        upgrade_guard.load_baseline(repo, baseline_path),
    )
    counts = report["counts"]
    assert report["baseline"]["degraded_two_way"] is False
    assert [item["path"] for item in report["manual_merge"]] == [
        "main.py",
        "static/js/both.py",
    ]
    assert [item["path"] for item in report["ours_only"]] == ["mathbank/ours_only.py"]
    assert report["pending_confirmation"] == []
    assert counts["safe_a"] == 1
    assert counts["manual_merge_b"] == 2 and counts["ours_only_c"] == 1
    assert report["new_in_release"]["detail"]["mathbank"] == ["mathbank/new_module.py"]

    entry = report["manual_merge"][0]
    assert entry["ours_sha256"] and entry["package_sha256"] and entry["baseline_sha256"]
    assert entry["ours_lines"] == 3 and entry["package_lines"] == 1
    assert entry["package_hash_source"] == "computed"

    text = "\n".join(upgrade_guard.render_lines(report))
    assert "这是我们深度二开过的文件，直接覆盖会丢改动" in text
    assert "可备份后由升级包替换，或保留我方版本" in text
    assert "🟢 A 类安全放行" in text

    result = _run_cli([root, "--repo", repo, "--baseline", baseline_path])
    assert result.returncode == 2
    assert "🔴 B 类：双方都改过 —— 必须人工合并（2 个）" in result.stdout


def test_only_ours_changed_exits_zero(tmp_path):
    """全是 C 类（只有我们改过）时不该拦路：退出码 0。"""
    repo, root, manifest_path, baseline_path = _build_scenario(
        tmp_path,
        ours={"main.py": "ours\n", "mathbank/a.py": "ours\n"},
        package={"main.py": "theirs\n", "mathbank/a.py": "theirs\n", "mathbank/new.py": "n\n"},
        baseline={"main.py": "package", "mathbank/a.py": "package"},
    )
    report = upgrade_guard.build_report(
        repo,
        upgrade_guard.load_manifest(manifest_path),
        root,
        upgrade_guard.load_baseline(repo, baseline_path),
    )
    assert report["counts"]["manual_merge_b"] == 0
    assert report["counts"]["ours_only_c"] == 2
    assert report["exit_code"] == 0
    assert _run_cli([root, "--repo", repo, "--baseline", baseline_path]).returncode == 0


def test_degraded_two_way_never_fakes_b_or_c(tmp_path):
    """没基线就不硬判：全部标待确认，并在报告头声明降级。"""
    repo, root, manifest_path, _ = _build_scenario(
        tmp_path,
        ours={"main.py": "ours\n"},
        package={"main.py": "theirs\n"},
    )
    report = upgrade_guard.build_report(
        repo, upgrade_guard.load_manifest(manifest_path), root
    )
    assert report["baseline"]["degraded_two_way"] is True
    assert report["counts"]["manual_merge_b"] == 0
    assert report["counts"]["ours_only_c"] == 0
    assert report["counts"]["pending_confirmation"] == 1
    text = "\n".join(upgrade_guard.render_lines(report))
    assert "基线缺失，已降级为二方比对" in text
    assert "结论：需人工合并 1 个" in text


def test_quiet_keeps_conflicts_and_conclusion_only(tmp_path):
    """--quiet：只留冲突清单与结论行；结论行绝不能被吞。"""
    repo, root, manifest_path, baseline_path = _build_scenario(
        tmp_path,
        ours={"main.py": "ours\n", "mathbank/ours_only.py": "ours\n"},
        package={"main.py": "theirs\n", "mathbank/ours_only.py": "theirs\n"},
        baseline={"main.py": "package", "mathbank/ours_only.py": "package"},
    )
    quiet = _run_cli([root, "--repo", repo, "--baseline", baseline_path, "--quiet"])
    assert quiet.returncode == 0
    assert "报告头" not in quiet.stdout
    assert "A 类安全放行" not in quiet.stdout
    for relative in ("main.py", "mathbank/ours_only.py"):
        assert relative in quiet.stdout
    assert "结论：需人工合并 0 个 / 仅我方改动 2 个" in quiet.stdout


def test_manifest_without_release_root_only_uses_declared_hash(tmp_path):
    """只给 --manifest：比存在性与清单声明哈希，并如实标明未实算。"""
    repo, _root, manifest_path, baseline_path = _build_scenario(
        tmp_path,
        ours={"main.py": "ours\n"},
        package={"main.py": "theirs\n"},
        baseline={"main.py": "package"},
    )
    result = _run_cli(["--manifest", manifest_path, "--repo", repo, "--baseline", baseline_path])
    assert result.returncode == 0
    assert "仅用清单声明哈希" in result.stdout
    assert "未提供" in result.stdout
    assert "行数未知" in result.stdout


def test_windows_case_insensitive_path_matching(tmp_path):
    """大小写不敏感：清单写 Foo.py、仓库里是 foo.py 时不能误判成「升级包新增」。"""
    probe = tmp_path / "case"
    probe.mkdir(parents=True)
    _write(probe, "Upper.py", "x\n")
    if not (probe / "upper.py").exists():
        pytest.skip("该文件系统大小写敏感，本用例只钉 Windows 口径")

    repo, root, manifest_path, baseline_path = _build_scenario(
        tmp_path,
        ours={"mathbank/foo.py": "same bytes\n"},
        package={"mathbank/Foo.py": "same bytes\n", "mathbank/New.py": "brand new\n"},
    )
    report = upgrade_guard.build_report(
        repo,
        upgrade_guard.load_manifest(manifest_path),
        root,
        upgrade_guard.load_baseline(repo, baseline_path),
    )
    assert report["counts"]["safe_a"] == 1
    assert report["new_in_release"]["total"] == 1
    assert report["new_in_release"]["detail"]["mathbank"] == ["mathbank/New.py"]
    assert report["anomalies"]["missing_in_package"] == []


def test_manifest_duplicate_paths_differing_only_by_case_are_refused(tmp_path):
    """同一大小写口径也要挡住「只差大小写的重复条目」，否则会两份互相覆盖。"""
    _repo, root, _manifest, _baseline = _build_scenario(
        tmp_path, ours={"main.py": "ours\n"}, package={"main.py": "theirs\n"}
    )
    payload = {
        "app_version": "9.9.9",
        "platform": "windows-x64",
        "schema_version": 1,
        "overlay": {"managed_roots": ["main.py", "mathbank"], "protected_paths": [".env"]},
        "files": [
            {"path": "mathbank/a.py", "sha256": _sha_of_text("x"), "size": 1},
            {"path": "mathbank/A.py", "sha256": _sha_of_text("y"), "size": 1},
        ],
    }
    path = root / upgrade_guard.MANIFEST_NAME
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(upgrade_guard.GuardError, match="重复路径"):
        upgrade_guard.load_manifest(path)


@requires_sample
def test_new_in_release_aggregation_on_sample(sample_degraded_report):
    """升级包新增按顶层目录聚合，并逐条列出 mathbank/ 与 scripts/ 的新模块。"""
    new_info = sample_degraded_report["new_in_release"]
    assert new_info["total"] > 0
    assert new_info["by_top_dir"]["python"] > 1000
    assert set(new_info["detail"]) <= {"mathbank", "scripts"}
    assert new_info["detail"]["mathbank"], "新模块必须逐条列出，不能只给计数"
    assert sum(new_info["by_top_dir"].values()) == new_info["total"]
    for names in new_info["detail"].values():
        assert names == sorted(names)


@requires_sample
def test_real_sample_three_way_all_c_exits_zero(sample_manifest, sample_degraded_report, tmp_path):
    """真数据反证：若基线 == 包内，40+ 个冲突全落到 C 类，退出码归零。

    同时钉住「降级清单」与「三方清单」的差异集合完全一致——二方比对不许漏报。
    """
    baseline_path = _write_sample_baseline(tmp_path, sample_manifest)
    report = upgrade_guard.build_report(
        PROJECT_ROOT,
        sample_manifest,
        RELEASE_DIR,
        upgrade_guard.load_baseline(PROJECT_ROOT, baseline_path),
    )
    assert report["baseline"]["degraded_two_way"] is False
    assert report["counts"]["manual_merge_b"] == 0
    assert report["counts"]["pending_confirmation"] == 0
    assert {item["path"] for item in report["ours_only"]} == _conflict_paths(
        sample_degraded_report
    )
    assert report["exit_code"] == 0
    text = "\n".join(upgrade_guard.render_lines(report))
    assert "可备份后由升级包替换，或保留我方版本" in text


@requires_sample
def test_real_sample_manual_merge_b_class_blocks_upgrade(sample_manifest, tmp_path):
    """最危险的一格（双方都改过）必须在真数据上成立，并以退出码 2 拦路。"""
    baseline_path = _write_sample_baseline(tmp_path, sample_manifest, overrides=DEEP_FORKS)
    result = _run_cli(
        [
            "--manifest", RELEASE_MANIFEST,
            "--release-root", RELEASE_DIR,
            "--repo", PROJECT_ROOT,
            "--baseline", baseline_path,
            "--json",
        ]
    )
    assert result.returncode == 2, result.stdout[-800:]
    assert "这是我们深度二开过的文件，直接覆盖会丢改动" in result.stdout
    for relative in DEEP_FORKS:
        assert "  - %s" % relative in result.stdout
    assert "待确认" not in result.stdout
    payload = _parse_json_block(result.stdout)
    assert {item["path"] for item in payload["manual_merge"]} == set(DEEP_FORKS)
    assert payload["counts"]["manual_merge_b"] == len(DEEP_FORKS)
    assert payload["counts"]["pending_confirmation"] == 0
    assert payload["exit_code"] == 2
    for item in payload["manual_merge"]:
        assert item["ours_sha256"] != item["package_sha256"]
        assert item["baseline_sha256"] != item["package_sha256"]
        assert item["ours_lines"] is not None or item["ours_kind"] in ("binary", "large")


def test_binary_and_oversized_files_are_never_counted_as_text(tmp_path):
    """二进制只给体积、>8MB 只算哈希：不许把字节流当文本读。"""
    blob = tmp_path / "blob.bin"
    blob.write_bytes(b"\x00\x01\x02abc")
    assert upgrade_guard._text_line_count(blob, blob.stat().st_size) == (None, "binary")
    assert upgrade_guard._text_line_count(blob, 9 * 1024 * 1024) == (None, "large")
    assert upgrade_guard._text_line_count(blob, None) == (None, "absent")

    text = tmp_path / "note.md"
    text.write_text("one\ntwo\nthree", encoding="utf-8")
    assert upgrade_guard._text_line_count(text, text.stat().st_size) == (3, "text")

    entry = {
        "ours_lines": None, "package_lines": None,
        "ours_size": 9 * 1024 * 1024, "package_size": 2048,
        "ours_kind": "large", "package_kind": "binary",
    }
    assert "超大文件" in upgrade_guard._format_side(entry, "ours")
    assert "二进制" in upgrade_guard._format_side(entry, "package")


@requires_sample
def test_report_head_carries_the_required_facts(sample_degraded_report):
    """报告头六件套：git HEAD、我方 __version__、包版本/平台、清单条数与自身哈希、时间戳。"""
    report = sample_degraded_report
    assert report["repo_git_rev"], "缺我方 git 短哈希"
    assert report["repo_version"] == "2.1.9"
    assert report["release"]["app_version"] == "2.7.0"
    assert report["release"]["platform"] == "windows-x64"
    assert report["release"]["schema_version"] == 1
    assert report["release"]["manifest_file_count"] > 0
    assert len(report["release"]["manifest_sha256"]) == 64
    assert report["generated_at"]
    assert report["read_only"] is True
    text = "\n".join(upgrade_guard.render_lines(report))
    for needle in (
        "我方 git HEAD : %s" % report["repo_git_rev"],
        "我方版本      : 2.1.9",
        "app_version=2.7.0 platform=windows-x64",
        "清单文件数    : %d 条" % report["release"]["manifest_file_count"],
        "清单 sha256   : %s" % report["release"]["manifest_sha256"],
        "比对时间      : %s" % report["generated_at"],
    ):
        assert needle in text


def test_bad_cli_flag_exits_one_not_two(tmp_path):
    """坏参数退 1：退出码 2 专属于「有 B 类要人工合并」，CI 才不会被误导。"""
    result = _run_cli(["--bogus-flag"])
    assert result.returncode == 1, result.stdout + result.stderr
    assert "预检失败" in result.stderr


def test_unknown_manifest_schema_is_refused(tmp_path):
    """schema_version != 1 的清单不猜口径：直接退出码 1 说明不支持。"""
    root = tmp_path / "package"
    root.mkdir()
    payload = {
        "app_version": "9.9.9",
        "platform": "windows-x64",
        "schema_version": 2,
        "overlay": {"managed_roots": ["main.py"], "protected_paths": [".env"]},
        "files": [{"path": "main.py", "sha256": _sha_of_text("x"), "size": 1}],
    }
    manifest_path = root / upgrade_guard.MANIFEST_NAME
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(upgrade_guard.GuardError, match="schema_version"):
        upgrade_guard.load_manifest(manifest_path)
    result = _run_cli(["--manifest", manifest_path, "--repo", tmp_path])
    assert result.returncode == 1
    assert "schema_version" in result.stderr
