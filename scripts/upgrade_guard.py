# -*- coding: utf-8 -*-
r"""覆盖升级预检（纯只读）：这次升级会冲掉我们哪些二开文件？

用法（项目根目录）::

    python -m scripts.upgrade_guard E:\math-question-bank
    python -m scripts.upgrade_guard --manifest <RELEASE-MANIFEST.json> --release-root <升级包目录>

背景：官方便携包的「覆盖升级」会按 ``RELEASE-MANIFEST.json`` 替换**同名**文件，而
``scripts/release_overlay.py`` 的 ``protected_paths`` 只保护 ``.env``／数据库／
``static/uploads``／``venv``，**一个源码文件都不保护**。本脚本把「即将被覆盖的同名文件」
做三方哈希比对：

- **A 类**（我方 == 包内）：安全放行，只计数。
- **B 类**（我方 != 包内 且 包内 != 基线）：双方都改过，必须人工合并。
- **C 类**（我方 != 包内 且 包内 == 基线）：只有我方改过，会被冲回上游版本。

「基线」取自安装后由 ``release_overlay.py`` 写进 ``.system_generated/`` 的
``installed-release-manifest.json``。**基线缺失时不硬判 B/C**——差异一律标为「待确认」，
并在报告头写明已降级为二方比对；不许猜。

严格边界：纯只读（绝不写文件、不改仓库、不碰 ``.env``／数据库／``.system_generated``）、
不联网、不做自动合并、不修改上游的 ``scripts/release_overlay.py``。

退出码：0 = 无需人工合并；2 = 存在 B 类（降级口径下含「待确认」）；1 = 参数或文件错误。
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 与 scripts/release_overlay.py 同名、同语义（那是上游文件，本脚本绝不修改它）。
MANIFEST_NAME = "RELEASE-MANIFEST.json"
STATE_DIRECTORY = ".system_generated"
INSTALLED_MANIFEST_NAME = "installed-release-manifest.json"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
# 超过这个体积只算 sha256，不再读进内存数行数。
MAX_TEXT_BYTES = 8 * 1024 * 1024
# release_overlay 唯一放行的受保护路径（空目录占位文件），比对口径要跟它一致。
PROTECTED_EXCEPTION = "static/uploads/.gitkeep"


class GuardError(RuntimeError):
    """参数或文件问题：一律退出码 1，不做任何猜测性兜底。"""


def _relative_path(value, label):
    """把清单里的路径解析成安全的 PurePosixPath（与 release_overlay 同口径）。"""
    valid = isinstance(value, str) and value and "\\" not in value and "\0" not in value
    if not valid:
        raise GuardError("%s 含非法路径：%r" % (label, value))
    relative = PurePosixPath(value)
    safe = relative.as_posix() == value and not relative.is_absolute()
    safe = safe and not any(
        part in {"", ".", ".."} or ":" in part for part in relative.parts
    )
    if not safe:
        raise GuardError("%s 含不安全路径（疑似路径穿越）：%r" % (label, value))
    return relative


def _fold(relative):
    """Windows 文件系统大小写不敏感：比对与查表都用 casefold 后的 posix 字符串。"""
    return relative.as_posix().casefold()


def _under(relative, roots):
    return any(relative == root or root in relative.parents for root in roots)


def _sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _human_size(size):
    if size is None:
        return "?"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return "%d %s" % (value, unit) if unit == "B" else "%.1f %s" % (value, unit)
        value /= 1024
    return "%d B" % size


def _short(digest):
    return digest[:8] if digest else "--------"


def _stat(path):
    try:
        return path.lstat()
    except OSError:
        return None


def _text_line_count(path, size):
    """返回 (行数, 形态)。二进制与超大文件不数行数，只给大小。"""
    if size is None:
        return None, "absent"
    if size > MAX_TEXT_BYTES:
        return None, "large"
    try:
        data = path.read_bytes()
    except OSError:
        return None, "unreadable"
    if b"\0" in data:
        return None, "binary"
    for encoding in ("utf-8", "gbk"):
        try:
            return len(data.decode(encoding).splitlines()), "text"
        except UnicodeDecodeError:
            continue
    return None, "binary"


def _git_short_head(repo):
    """本地 git 元数据（不联网）；拿不到返回 None，报告里写「不可用」。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def _repo_version(repo):
    """读 mathbank/__init__.py 的 __version__（纯文本解析，不导入应用包）。"""
    init = Path(repo) / "mathbank" / "__init__.py"
    try:
        content = init.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r"""^__version__\s*=\s*['"]([^'"]+)['"]""", content, re.MULTILINE)
    return match.group(1) if match else None


def load_manifest(manifest_path):
    """读取 RELEASE-MANIFEST.json，只取预检需要的字段；结构不合预期就直接报错。"""
    manifest_path = Path(manifest_path)
    metadata = _stat(manifest_path)
    if metadata is None:
        raise GuardError("清单文件不存在：%s" % manifest_path)
    if metadata.st_size > MAX_MANIFEST_BYTES:
        raise GuardError("清单文件异常过大：%s" % manifest_path)
    raw = manifest_path.read_bytes()
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GuardError("清单不是合法 UTF-8 JSON：%s（%s）" % (manifest_path, exc)) from exc
    if not isinstance(document, dict):
        raise GuardError("清单结构无法识别（不是对象）：%s" % manifest_path)
    schema = document.get("schema_version")
    if schema != 1:
        # release_overlay 只认 schema_version == 1；预检同样不对未知结构猜口径。
        raise GuardError("清单 schema_version=%r 暂不支持：%s" % (schema, manifest_path))
    overlay = document.get("overlay")
    if not isinstance(overlay, dict):
        raise GuardError("清单缺 overlay 策略段：%s" % manifest_path)
    managed = overlay.get("managed_roots")
    protected = overlay.get("protected_paths")
    entries = document.get("files")
    if not isinstance(managed, list) or not managed:
        raise GuardError("清单 overlay.managed_roots 不合法：%s" % manifest_path)
    if not isinstance(protected, list):
        raise GuardError("清单 overlay.protected_paths 不合法：%s" % manifest_path)
    if not isinstance(entries, list):
        raise GuardError("清单缺 files 列表：%s" % manifest_path)

    parsed = []
    seen = set()
    for index, raw_entry in enumerate(entries):
        if not isinstance(raw_entry, dict):
            raise GuardError("清单第 %d 条不是对象" % index)
        relative = _relative_path(raw_entry.get("path"), "清单")
        key = _fold(relative)
        if key in seen:
            raise GuardError("清单含重复路径：%s" % relative.as_posix())
        seen.add(key)
        declared = raw_entry.get("sha256")
        if not isinstance(declared, str) or not SHA256_PATTERN.fullmatch(declared):
            raise GuardError("清单 %s 的 sha256 不合法" % relative.as_posix())
        size = raw_entry.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise GuardError("清单 %s 的 size 不合法" % relative.as_posix())
        parsed.append(
            {"relative": relative, "key": key, "sha256": declared, "size": size}
        )

    version = document.get("app_version")
    platform = document.get("platform")
    return {
        "path": str(manifest_path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "app_version": version if isinstance(version, str) and version else None,
        "platform": platform if isinstance(platform, str) and platform else None,
        "schema_version": document.get("schema_version"),
        "managed_roots": [_relative_path(item, "managed_roots") for item in managed],
        "protected_roots": [
            _relative_path(item, "protected_paths") for item in protected if item
        ],
        "file_count": len(entries),
        "files": parsed,
    }


def load_baseline(repo, explicit_path=None):
    """基线 = 安装后写进 .system_generated 的 installed-release-manifest.json。

    这里只把它当「路径 → 当年安装时 sha256」的查找表，所以刻意宽松：解析不出来的
    条目直接跳过，绝不因为基线格式问题中断预检（基线不可用 == 降级二方比对）。
    """
    path = Path(explicit_path) if explicit_path else (
        Path(repo) / STATE_DIRECTORY / INSTALLED_MANIFEST_NAME
    )
    unavailable = {
        "available": False, "path": str(path), "sha256": None, "entry_count": 0,
        "index": {},
    }
    if not path.is_file():
        return dict(unavailable, reason="文件不存在")
    try:
        raw = path.read_bytes()
        document = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return dict(unavailable, reason="无法解析：%s" % exc)
    entries = document.get("files") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        return dict(unavailable, reason="基线清单缺 files 列表")
    index = {}
    for raw_entry in entries:
        if not isinstance(raw_entry, dict):
            continue
        value = raw_entry.get("path")
        digest = raw_entry.get("sha256")
        if not isinstance(value, str) or not isinstance(digest, str):
            continue
        if not SHA256_PATTERN.fullmatch(digest):
            continue
        try:
            index[_fold(_relative_path(value, "基线"))] = digest
        except GuardError:
            continue
    if not index:
        return dict(unavailable, reason="基线清单里没有任何可用条目")
    return {
        "available": True, "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(), "entry_count": len(index),
        "index": index, "reason": None,
    }


def _profile(path):
    """给一侧文件出「sha256 + 体积 + 行数」画像；缺失与非普通文件都如实标注。"""
    metadata = _stat(path) if path is not None else None
    if metadata is None:
        return {"exists": False, "regular": False, "sha256": None, "size": None,
                "lines": None, "kind": "absent"}
    regular = (metadata.st_mode & 0o170000) == 0o100000
    if not regular:
        return {"exists": True, "regular": False, "sha256": None,
                "size": metadata.st_size, "lines": None, "kind": "irregular"}
    lines, kind = _text_line_count(path, metadata.st_size)
    return {"exists": True, "regular": True, "sha256": _sha256_file(path),
            "size": metadata.st_size, "lines": lines, "kind": kind}


def _declared_profile(declared_sha, declared_size):
    """没给升级包目录时，包内哈希只能用清单声明值（报告里必须标明来源）。"""
    return {"exists": None, "regular": True, "sha256": declared_sha,
            "size": declared_size, "lines": None, "kind": "declared"}


def build_report(repo, manifest, release_root=None, baseline=None):
    """三方比对主体。repo/包目录只读，任何判断都只基于实算哈希。"""
    repo = Path(repo)
    managed = manifest["managed_roots"]
    protected = manifest["protected_roots"]
    baseline = baseline if baseline is not None else load_baseline(repo)
    baseline_index = baseline["index"] if baseline["available"] else {}

    manual_merge = []
    ours_only = []
    pending = []
    irregular = []
    mismatch = []
    missing_in_package = []
    safe = 0
    skipped_protected = 0
    skipped_unmanaged = 0
    new_total = 0
    new_by_top = {}
    new_detail = {}

    for entry in manifest["files"]:
        relative = entry["relative"]
        if not _under(relative, managed):
            skipped_unmanaged += 1
            continue
        if _under(relative, protected) and relative.as_posix() != PROTECTED_EXCEPTION:
            skipped_protected += 1
            continue

        ours_path = repo.joinpath(*relative.parts)
        ours = _profile(ours_path)
        package_path = (
            Path(release_root).joinpath(*relative.parts) if release_root else None
        )

        if not ours["exists"]:
            # 升级包新增：我们根本没有这个文件，覆盖升级只是往仓库里加东西。
            new_total += 1
            top = relative.parts[0] if relative.parts else "?"
            new_by_top[top] = new_by_top.get(top, 0) + 1
            if top in ("mathbank", "scripts"):
                new_detail.setdefault(top, []).append(relative.as_posix())
            if package_path is not None and _stat(package_path) is None:
                missing_in_package.append(relative.as_posix())
            continue
        if not ours["regular"]:
            irregular.append(
                {"path": relative.as_posix(), "reason": "我方路径不是普通文件，无法比对"}
            )
            continue

        if package_path is not None:
            package = _profile(package_path)
            if not package["exists"] or not package["regular"]:
                missing_in_package.append(relative.as_posix())
                package = _declared_profile(entry["sha256"], entry["size"])
            elif entry["sha256"] != package["sha256"]:
                # 清单与包内实际字节不一致：正式升级的校验会直接失败，必须提前暴露。
                mismatch.append(
                    {"path": relative.as_posix(), "declared": entry["sha256"],
                     "actual": package["sha256"]}
                )
        else:
            package = _declared_profile(entry["sha256"], entry["size"])

        record = {
            "path": relative.as_posix(),
            "ours_sha256": ours["sha256"],
            "package_sha256": package["sha256"],
            "package_hash_source": "computed" if release_root else "declared",
            "ours_size": ours["size"],
            "package_size": package["size"],
            "ours_lines": ours["lines"],
            "package_lines": package["lines"],
            "ours_kind": ours["kind"],
            "package_kind": package["kind"],
            "baseline_sha256": None,
            "note": None,
        }

        if ours["sha256"] == package["sha256"]:
            safe += 1  # A 类：字节相同，覆盖等于没覆盖。
            continue

        baseline_sha = baseline_index.get(entry["key"])
        record["baseline_sha256"] = baseline_sha
        if not baseline["available"] or baseline_sha is None:
            # 拿不到基线就不硬判 B/C：全部标为待确认。
            record["note"] = "基线缺失" if not baseline["available"] else "基线未收录该路径"
            pending.append(record)
        elif baseline_sha == package["sha256"]:
            ours_only.append(record)
        else:
            if baseline_sha == ours["sha256"]:
                record["note"] = "我方与基线一致，疑似只有上游改过（仍按 B 类上报，不替你判断）"
            manual_merge.append(record)

    manual_merge.sort(key=lambda item: item["path"])
    ours_only.sort(key=lambda item: item["path"])
    pending.sort(key=lambda item: item["path"])

    degraded = not baseline["available"]
    attention = len(manual_merge) + len(pending)
    blockers = attention or irregular or mismatch or missing_in_package

    return {
        "tool": "upgrade_guard",
        "read_only": True,
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "repo": str(repo),
        "repo_git_rev": _git_short_head(repo),
        "repo_version": _repo_version(repo),
        "release": {
            "app_version": manifest["app_version"],
            "platform": manifest["platform"],
            "schema_version": manifest["schema_version"],
            "manifest_path": manifest["path"],
            "manifest_sha256": manifest["sha256"],
            "manifest_file_count": manifest["file_count"],
            "release_root": str(release_root) if release_root else None,
            "hash_mode": "包内 sha256 实算" if release_root else "仅用清单声明哈希",
            "managed_roots": [item.as_posix() for item in managed],
            "protected_roots": [item.as_posix() for item in protected],
        },
        "baseline": {
            "available": baseline["available"],
            "path": baseline["path"],
            "sha256": baseline["sha256"],
            "entry_count": baseline["entry_count"],
            "reason": baseline["reason"],
            "degraded_two_way": degraded,
        },
        "counts": {
            "manual_merge_b": len(manual_merge),
            "ours_only_c": len(ours_only),
            "pending_confirmation": len(pending),
            "new_in_release": new_total,
            "safe_a": safe,
            "skipped_protected": skipped_protected,
            "skipped_unmanaged": skipped_unmanaged,
            "needs_manual_attention": attention,
        },
        "manual_merge": manual_merge,
        "ours_only": ours_only,
        "pending_confirmation": pending,
        "new_in_release": {
            "total": new_total,
            "by_top_dir": dict(
                sorted(new_by_top.items(), key=lambda item: (-item[1], item[0]))
            ),
            "detail": {key: sorted(value) for key, value in sorted(new_detail.items())},
        },
        "anomalies": {
            "irregular_ours": sorted(irregular, key=lambda item: item["path"]),
            "manifest_mismatch": sorted(mismatch, key=lambda item: item["path"]),
            "missing_in_package": sorted(missing_in_package),
        },
        "exit_code": 2 if blockers else 0,
    }


def _format_side(entry, side):
    """把一侧的体积／行数压成一段可扫的文字。"""
    lines = entry["%s_lines" % side]
    size = entry["%s_size" % side]
    kind = entry["%s_kind" % side]
    if lines is not None:
        return "%d 行" % lines
    if kind == "large":
        return "超大文件 %s（只算哈希）" % _human_size(size)
    if kind == "binary":
        return "二进制 %s" % _human_size(size)
    if kind == "declared":
        return "声明体积 %s（未给包目录，行数未知）" % _human_size(size)
    if kind in ("absent", "unreadable"):
        return "不可读"
    return _human_size(size)


def _comparison_line(entry):
    return "我方=%s 包内=%s 行数：我方 %s ／ 包内 %s" % (
        _short(entry["ours_sha256"]), _short(entry["package_sha256"]),
        _format_side(entry, "ours"), _format_side(entry, "package"),
    )


def render_lines(report, quiet=False):
    """人可读报告。quiet 只给冲突清单与结论行。"""
    counts = report["counts"]
    degraded = report["baseline"]["degraded_two_way"]
    lines = []
    push = lines.append

    if not quiet:
        push("MathBank 覆盖升级预检（纯只读：不写文件、不合并、不联网）")
        push("=" * 78)
        push("**报告头**")
        push("  我方仓库      : %s" % report["repo"])
        push("  我方 git HEAD : %s"
             % (report["repo_git_rev"] or "不可用（非 git 仓库或 git 不可用）"))
        push("  我方版本      : %s（mathbank/__init__.py 的 __version__）"
             % (report["repo_version"] or "不可用"))
        push("  升级包        : app_version=%s platform=%s schema_version=%s"
             % (report["release"]["app_version"], report["release"]["platform"],
                report["release"]["schema_version"]))
        push("  清单文件      : %s" % report["release"]["manifest_path"])
        push("  清单文件数    : %d 条" % report["release"]["manifest_file_count"])
        push("  清单 sha256   : %s" % report["release"]["manifest_sha256"])
        push("  包内来源      : %s（%s）"
             % (report["release"]["release_root"] or "未提供", report["release"]["hash_mode"]))
        if degraded:
            push("  基线          : 基线缺失，已降级为二方比对（原因：%s）"
                 % report["baseline"]["reason"])
            push("                路径 %s 不参与判定：差异一律标「待确认」，不硬判 B/C。"
                 % report["baseline"]["path"])
        else:
            push("  基线          : %s（%d 条，sha256=%s）"
                 % (report["baseline"]["path"], report["baseline"]["entry_count"],
                    _short(report["baseline"]["sha256"])))
        push("  比对时间      : %s" % report["generated_at"])
        push("  比对口径      : PurePosixPath + 大小写不敏感；受管根 %d 个／保护路径 %d 个；"
             "跳过保护路径 %d 条、非受管 %d 条"
             % (len(report["release"]["managed_roots"]),
                len(report["release"]["protected_roots"]),
                counts["skipped_protected"], counts["skipped_unmanaged"]))
        push("")

    if degraded:
        push("🔴 待确认（基线缺失，无法判定是否双方都改过）：%d 个"
             % counts["pending_confirmation"])
        push("  这些同名文件的内容与升级包不一致，直接覆盖就会动到我们的代码；但缺少"
             " installed-release-manifest.json，无法区分「双方都改」与「只有我们改」。")
        for entry in report["pending_confirmation"]:
            push("  - %s" % entry["path"])
            push("      %s（%s）" % (_comparison_line(entry), entry["note"]))
    else:
        push("🔴 B 类：双方都改过 —— 必须人工合并（%d 个）" % counts["manual_merge_b"])
        push("  这是我们深度二开过的文件，直接覆盖会丢改动。")
        for entry in report["manual_merge"]:
            push("  - %s" % entry["path"])
            push("      %s 基线=%s"
                 % (_comparison_line(entry), _short(entry["baseline_sha256"])))
            if entry["note"]:
                push("      备注：%s" % entry["note"])
        push("")
        push("🟡 C 类：仅我方改过（%d 个）" % counts["ours_only_c"])
        for entry in report["ours_only"]:
            push("  - %s" % entry["path"])
            push("      %s" % _comparison_line(entry))
            push("      → 可备份后由升级包替换，或保留我方版本。")

    if not quiet:
        new_info = report["new_in_release"]
        push("")
        push("🟢 升级包新增（我方没有）：%d 个" % new_info["total"])
        for top, amount in new_info["by_top_dir"].items():
            push("  - %-12s %5d 个" % (top, amount))
            if top in new_info["detail"]:
                push("      新模块往往意味着新能力，逐条列出：")
                for name in new_info["detail"][top]:
                    push("        %s" % name)
        push("")
        push("🟢 A 类安全放行（我方与包内字节相同）：%d 个（不逐条列）" % counts["safe_a"])

        anomalies = report["anomalies"]
        if any(anomalies.values()):
            push("")
            push("⚠️ 异常（正式升级校验会失败，先修再升）")
            for item in anomalies["manifest_mismatch"]:
                push("  - 清单与包内实际字节不一致：%s 声明=%s 实算=%s"
                     % (item["path"], _short(item["declared"]), _short(item["actual"])))
            for name in anomalies["missing_in_package"]:
                push("  - 清单声明但包内缺失：%s" % name)
            for item in anomalies["irregular_ours"]:
                push("  - 我方路径无法比对：%s（%s）" % (item["path"], item["reason"]))

    push("")
    push("结论：需人工合并 %d 个 / 仅我方改动 %d 个 / 升级包新增 %d 个 / 安全 %d 个"
         % (counts["needs_manual_attention"] if degraded else counts["manual_merge_b"],
            counts["ours_only_c"], counts["new_in_release"], counts["safe_a"]))
    if degraded:
        push("  ↑ 降级口径：基线缺失，%d 个差异文件全部计入「需人工合并（待确认）」，"
             "未判定是否双方都改过。" % counts["pending_confirmation"])
    return lines


class _GuardArgumentParser(argparse.ArgumentParser):
    """参数错误也必须退 1。

    argparse 默认对坏参数退 2，而本工具的 2 专门表示「有 B 类要人工合并」；两者混用
    会让接 CI 的脚本把「命令行打错了」误读成「升级被拦下」。
    """

    def error(self, message):
        self.print_usage(sys.stderr)
        raise GuardError(message)


def _parse_argv(argv):
    parser = _GuardArgumentParser(
        prog="python -m scripts.upgrade_guard",
        description="覆盖升级预检（纯只读）：报告升级包会冲掉哪些二开文件。",
    )
    parser.add_argument("release_dir", nargs="?", help="升级包目录（内含 %s）" % MANIFEST_NAME)
    parser.add_argument("--repo", default=str(PROJECT_ROOT),
                        help="被保护的仓库根（默认脚本上级目录）")
    parser.add_argument("--manifest", help="直接指定 %s 路径" % MANIFEST_NAME)
    parser.add_argument("--release-root", help="升级包文件所在目录（配合 --manifest 做 sha256 实算）")
    parser.add_argument("--baseline",
                        help="覆盖基线清单路径（默认读 .system_generated/%s）" % INSTALLED_MANIFEST_NAME)
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="额外输出机器可读 JSON")
    parser.add_argument("--quiet", action="store_true", help="只输出结论行与冲突清单")
    return parser.parse_args(argv)


def _configure_streams():
    """把输出流钉成 UTF-8。

    报告里有中文与 emoji：重定向到管道时 Python 按本地代码页（CP936）编码，轻则把
    emoji 变成问号，重则 UnicodeEncodeError 让预检崩在半路。兜底本身不许再抛异常。
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def main(argv=None):
    _configure_streams()
    try:
        args = _parse_argv(argv)
        repo = Path(args.repo)
        if not repo.is_dir():
            raise GuardError("仓库目录不存在：%s" % repo)
        repo = repo.resolve()

        release_root = None
        if args.release_dir and args.manifest:
            raise GuardError("位置参数（升级包目录）与 --manifest 只能二选一")
        if args.release_dir:
            candidate = Path(args.release_dir)
            if not candidate.is_dir():
                raise GuardError("升级包目录不存在：%s" % candidate)
            candidate = candidate.resolve()
            manifest_path = candidate / MANIFEST_NAME
            if not manifest_path.is_file():
                raise GuardError("该目录不是 MathBank 便携包：缺 %s" % MANIFEST_NAME)
            release_root = candidate
        elif args.manifest:
            manifest_path = Path(args.manifest)
            if not manifest_path.is_file():
                raise GuardError("清单文件不存在：%s" % manifest_path)
            if args.release_root:
                release_root = Path(args.release_root)
                if not release_root.is_dir():
                    raise GuardError("升级包目录不存在：%s" % release_root)
                release_root = release_root.resolve()
        else:
            raise GuardError("必须给出升级包目录，或用 --manifest 指定 %s" % MANIFEST_NAME)

        manifest = load_manifest(manifest_path)
        baseline = load_baseline(repo, args.baseline)
        report = build_report(repo, manifest, release_root, baseline)
    except GuardError as exc:
        print("预检失败：%s" % exc, file=sys.stderr)
        return 1

    for line in render_lines(report, quiet=args.quiet):
        print(line)
    if args.as_json:
        print("")
        print("===== 机器可读 JSON =====")
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
