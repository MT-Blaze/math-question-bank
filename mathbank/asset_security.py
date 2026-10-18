"""Security helpers for uploaded assets and private local files.

The browser is allowed to refer to uploaded images by URL, but those references
must never become arbitrary filesystem paths.  This module is the single place
that translates an upload URL into a local file and validates its containment,
type and symlink status.
"""

from __future__ import annotations

from dataclasses import dataclass
import datetime
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import time
from typing import Iterable

from PIL import Image, ImageOps, UnidentifiedImageError


MAX_SINGLE_IMAGE_BYTES = 10 * 1024 * 1024
MAX_OCR_IMAGE_BYTES = 10 * 1024 * 1024
MAX_PDF_BYTES = 30 * 1024 * 1024
MAX_IMAGE_PIXELS = 30_000_000
MAX_NORMALIZED_IMAGE_BYTES = 20 * 1024 * 1024

SAFE_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
_SAFE_ASSET_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_TIKZ_ASSET_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class AssetSecurityError(ValueError):
    """Raised when a client-supplied asset reference leaves the upload root."""


class UploadTooLargeError(ValueError):
    """Raised as soon as a streamed upload exceeds its endpoint limit."""


class InvalidImageError(ValueError):
    """Raised when uploaded bytes are not a safe supported raster image."""


@dataclass(frozen=True)
class NormalizedImage:
    data: bytes
    extension: str
    media_type: str
    width: int
    height: int


def _reference_parts(reference: str, url_prefix: str) -> tuple[str, ...]:
    if not isinstance(reference, str) or not reference.strip():
        raise AssetSecurityError("插图路径不能为空。")

    normalized = reference.strip().replace("\\", "/")
    if "\x00" in normalized or "?" in normalized or "#" in normalized:
        raise AssetSecurityError("插图路径包含不允许的字符。")
    if _URI_SCHEME.match(normalized):
        raise AssetSecurityError("插图路径必须是本地上传资源，不能是外部 URI。")

    normalized = normalized.lstrip("/")
    prefix = str(PurePosixPath(str(url_prefix).strip().replace("\\", "/").strip("/")))
    if normalized == prefix:
        relative = ""
    elif normalized.startswith(prefix + "/"):
        relative = normalized[len(prefix) + 1 :]
    elif "/" not in normalized:
        # Internal export helpers may pass a generated basename directly.
        relative = normalized
    else:
        raise AssetSecurityError("插图路径不属于当前上传目录。")

    pure_path = PurePosixPath(relative)
    parts = pure_path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise AssetSecurityError("插图路径包含目录穿越片段。")
    if any(not _SAFE_ASSET_COMPONENT.fullmatch(part) for part in parts):
        raise AssetSecurityError("插图路径包含不安全的文件名。")
    return parts


def resolve_upload_asset(
    reference: str,
    *,
    uploads_dir: str | os.PathLike,
    url_prefix: str,
    require_file: bool = True,
    allowed_extensions: Iterable[str] = SAFE_IMAGE_EXTENSIONS,
) -> Path:
    """Resolve a browser upload URL to a contained, non-symlink local path."""

    root = Path(uploads_dir).resolve()
    parts = _reference_parts(reference, url_prefix)
    candidate = root.joinpath(*parts)

    # Reject a symlink at any level below the trusted upload root.  Merely
    # checking the final resolved path would otherwise accept an in-root link.
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise AssetSecurityError("插图路径不能经过符号链接。")

    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise AssetSecurityError("插图路径越出了上传目录。") from exc

    extension = resolved.suffix.lower()
    normalized_extensions = {str(value).lower() for value in allowed_extensions}
    if extension not in normalized_extensions:
        raise AssetSecurityError("插图文件类型不在安全白名单中。")

    if resolved.exists() and not resolved.is_file():
        raise AssetSecurityError("插图路径不是普通文件。")
    if require_file and (not resolved.exists() or not resolved.is_file()):
        raise AssetSecurityError("插图文件不存在或不是普通文件。")
    return resolved


def normalize_upload_asset_reference(
    reference: str,
    *,
    uploads_dir: str | os.PathLike,
    url_prefix: str,
    require_file: bool = True,
) -> str:
    """Return the canonical browser URL for one validated upload asset."""

    root = Path(uploads_dir).resolve()
    resolved = resolve_upload_asset(
        reference,
        uploads_dir=root,
        url_prefix=url_prefix,
        require_file=require_file,
    )
    relative = resolved.relative_to(root).as_posix()
    return f"/{str(PurePosixPath(str(url_prefix).strip('/')))}/{relative}"


def normalize_upload_asset_references(
    references,
    *,
    uploads_dir: str | os.PathLike,
    url_prefix: str,
    require_file: bool = True,
    max_items: int = 50,
) -> list[str]:
    """Validate and deduplicate the image-path array stored on a question."""

    if not isinstance(references, list):
        raise AssetSecurityError("image_paths 必须是插图路径数组。")
    if len(references) > max_items:
        raise AssetSecurityError(f"单题插图数量不能超过 {max_items} 张。")

    normalized: list[str] = []
    seen: set[str] = set()
    for reference in references:
        value = normalize_upload_asset_reference(
            reference,
            uploads_dir=uploads_dir,
            url_prefix=url_prefix,
            require_file=require_file,
        )
        if value not in seen:
            seen.add(value)
            normalized.append(value)
    return normalized


def normalize_optional_upload_asset_reference(
    reference: str,
    *,
    allowed_image_paths: Iterable[str],
    uploads_dir: str | os.PathLike,
    url_prefix: str,
) -> str:
    """Validate an optional hidden asset and require lifecycle registration."""

    value = str(reference or "").strip()
    if not value:
        return ""
    normalized = normalize_upload_asset_reference(
        value,
        uploads_dir=uploads_dir,
        url_prefix=url_prefix,
    )
    if normalized not in set(allowed_image_paths):
        raise AssetSecurityError("参考图路径必须同时存在于题目 image_paths 中。")
    return normalized


def _normalize_tikz_assets(
    raw_assets,
    *,
    allowed_image_paths: Iterable[str],
    uploads_dir: str | os.PathLike,
    url_prefix: str,
    field_name: str,
    location_label: str,
    max_items: int = 20,
) -> list[dict[str, str]]:
    """Validate editable TikZ sources associated with rendered images."""

    if isinstance(raw_assets, str):
        try:
            raw_assets = json.loads(raw_assets or "[]")
        except json.JSONDecodeError as exc:
            raise AssetSecurityError(f"{field_name} 必须是有效的 JSON 数组。") from exc
    if not isinstance(raw_assets, list):
        raise AssetSecurityError(f"{field_name} 必须是数组。")
    if len(raw_assets) > max_items:
        raise AssetSecurityError(
            f"单题{location_label} TikZ 插图不能超过 {max_items} 幅。"
        )

    allowed = set(allowed_image_paths)
    normalized_assets: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for index, asset in enumerate(raw_assets, start=1):
        if not isinstance(asset, dict):
            raise AssetSecurityError(f"第 {index} 个 TikZ 插图记录不是对象。")
        asset_id = str(asset.get("id") or "").strip()
        if not _TIKZ_ASSET_ID.fullmatch(asset_id) or asset_id in seen_ids:
            raise AssetSecurityError(f"第 {index} 个 TikZ 插图 ID 无效或重复。")
        seen_ids.add(asset_id)

        image_path = normalize_upload_asset_reference(
            str(asset.get("image_path") or ""),
            uploads_dir=uploads_dir,
            url_prefix=url_prefix,
        )
        if image_path not in allowed:
            raise AssetSecurityError("TikZ 插图路径必须同时存在于题目 image_paths 中。")

        tikz_code = str(asset.get("tikz_code") or "").strip()
        instruction = str(asset.get("instruction") or "").strip()
        if not tikz_code or len(tikz_code) > 200000:
            raise AssetSecurityError("TikZ 插图源码为空或超过 200000 个字符。")
        if not re.search(r"\\begin\s*\{\s*tikzpicture\s*\}", tikz_code) or not re.search(
            r"\\end\s*\{\s*tikzpicture\s*\}", tikz_code
        ):
            raise AssetSecurityError("TikZ 插图源码必须包含完整的 tikzpicture 环境。")
        if len(instruction) > 4000:
            raise AssetSecurityError("TikZ 绘图要求不能超过 4000 个字符。")
        reference_image_path = str(asset.get("reference_image_path") or "").strip()
        normalized_asset = {
            "id": asset_id,
            "image_path": image_path,
            "tikz_code": tikz_code,
            "instruction": instruction,
        }
        if reference_image_path:
            reference_image_path = normalize_upload_asset_reference(
                reference_image_path,
                uploads_dir=uploads_dir,
                url_prefix=url_prefix,
            )
            if reference_image_path not in allowed:
                raise AssetSecurityError(
                    "TikZ 参考图路径必须同时存在于题目 image_paths 中。"
                )
            normalized_asset["reference_image_path"] = reference_image_path
        normalized_assets.append(normalized_asset)
    return normalized_assets


def normalize_content_tikz_assets(
    raw_assets,
    *,
    allowed_image_paths: Iterable[str],
    uploads_dir: str | os.PathLike,
    url_prefix: str,
    max_items: int = 20,
) -> list[dict[str, str]]:
    """Validate the editable TikZ sources associated with question content."""

    return _normalize_tikz_assets(
        raw_assets,
        allowed_image_paths=allowed_image_paths,
        uploads_dir=uploads_dir,
        url_prefix=url_prefix,
        field_name="content_tikz_assets",
        location_label="题干",
        max_items=max_items,
    )


def normalize_answer_tikz_assets(
    raw_assets,
    *,
    allowed_image_paths: Iterable[str],
    uploads_dir: str | os.PathLike,
    url_prefix: str,
    max_items: int = 20,
) -> list[dict[str, str]]:
    """Validate the editable TikZ sources associated with answer images."""

    return _normalize_tikz_assets(
        raw_assets,
        allowed_image_paths=allowed_image_paths,
        uploads_dir=uploads_dir,
        url_prefix=url_prefix,
        field_name="answer_tikz_assets",
        location_label="解答",
        max_items=max_items,
    )


def read_stream_limited(stream, max_bytes: int, *, chunk_size: int = 1024 * 1024) -> bytes:
    """Read a synchronous file-like object without ever buffering past the cap."""

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.read(min(chunk_size, max_bytes - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise UploadTooLargeError(f"上传文件超过 {max_bytes} 字节安全上限。")
        chunks.append(chunk)
    return b"".join(chunks)


def normalize_raster_image(
    raw: bytes,
    *,
    max_pixels: int = MAX_IMAGE_PIXELS,
    max_output_bytes: int = MAX_NORMALIZED_IMAGE_BYTES,
) -> NormalizedImage:
    """Fully decode and re-encode an uploaded raster into passive image bytes."""

    if not raw:
        raise InvalidImageError("图片内容为空。")
    try:
        with Image.open(io.BytesIO(raw)) as source:
            width, height = source.size
            if width <= 0 or height <= 0 or width * height > max_pixels:
                raise InvalidImageError("图片像素尺寸超过安全上限。")
            if (source.format or "").upper() not in {"PNG", "JPEG", "GIF", "WEBP"}:
                raise InvalidImageError("仅支持 PNG、JPEG、GIF 或 WebP 图片。")

            # Decode the complete first frame.  Re-encoding removes metadata,
            # trailing polyglot payloads and active content disguised by suffix.
            source.seek(0)
            source.load()
            transposed = ImageOps.exif_transpose(source)
            has_alpha = transposed.mode in {"RGBA", "LA"} or (
                transposed.mode == "P" and "transparency" in transposed.info
            )
            safe_image = transposed.convert("RGBA" if has_alpha else "RGB")

            output = io.BytesIO()
            if (source.format or "").upper() == "JPEG" and not has_alpha:
                safe_image.save(output, format="JPEG", quality=95, optimize=True)
                extension = ".jpg"
                media_type = "image/jpeg"
            else:
                safe_image.save(output, format="PNG", optimize=True)
                extension = ".png"
                media_type = "image/png"
            normalized = output.getvalue()
    except InvalidImageError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise InvalidImageError("文件内容不是有效且安全的栅格图片。") from exc

    if len(normalized) > max_output_bytes:
        raise InvalidImageError("图片安全转码后的体积超过上限。")
    return NormalizedImage(
        data=normalized,
        extension=extension,
        media_type=media_type,
        width=width,
        height=height,
    )


def harden_private_path(path: str | os.PathLike, *, directory: bool = False) -> None:
    """Best-effort owner-only permissions for local secrets."""

    try:
        os.chmod(path, 0o700 if directory else 0o600)
    except OSError:
        # Windows and some packaged filesystems do not implement POSIX modes.
        pass


# WinError 32（文件被另一进程/线程占用）与 WinError 5（共享违规下的拒绝访问）
# 都可能是并发读者短暂打开目标文件造成的瞬时状态，重试即可自愈。
_REPLACE_RETRYABLE_WINERRORS = (5, 32)
_REPLACE_ATTEMPTS = 8
_REPLACE_MAX_SLEEP_SECONDS = 0.3


def _replace_backoff_seconds(attempt: int) -> float:
    """指数退避：0.02s 起步、逐次翻倍、封顶 0.3s（总预算约 1.9s）。"""

    return min(0.02 * (2 ** attempt), _REPLACE_MAX_SLEEP_SECONDS)


def _replace_with_retry(source: str, target: Path) -> None:
    """``os.replace`` 的退避重试包装，只吞「对方还开着文件」这类瞬时错误。

    Windows 上任何读者把目标文件打开着（哪怕共享模式完全兼容），
    rename-over 都会被挡下 WinError 5；本项目里典型读者是保存后排队的
    后台导出与 `/api/categories` 的元数据热重载。失败时临时文件仍完整躺在
    同一目录里，所以重试是安全的：要么这次替换成功，要么下一次接着换，
    绝不会出现半写目标文件。非瞬时错误（权限真缺失、跨盘等）原样抛出，
    不掩盖真实故障。
    """

    last_exc: OSError | None = None
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(source, target)
            return
        except OSError as exc:
            if getattr(exc, "winerror", None) not in _REPLACE_RETRYABLE_WINERRORS:
                raise
            last_exc = exc
        if attempt < _REPLACE_ATTEMPTS - 1:
            time.sleep(_replace_backoff_seconds(attempt))
    assert last_exc is not None
    raise last_exc


def write_private_text_atomic(path: str | os.PathLike, text: str) -> None:
    """Atomically write UTF-8 text with owner-only permissions.

    Windows 上 ``os.replace`` 会被「目标文件正被并发读取」挡下（WinError 5）：
    后台导出/热重载只要在这一瞬打开着同一个文件，保存大纲就偶发 500。
    replace 失败时临时文件仍在原地，重试不会造成半写，只对这一小段做退避重试。
    """

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    try:
        fchmod = getattr(os, "fchmod", None)
        if fchmod is not None:
            try:
                fchmod(fd, 0o600)
            except (OSError, NotImplementedError):
                pass
        handle = os.fdopen(fd, "w", encoding="utf-8")
        fd = -1
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(temp_name, target)
        harden_private_path(target)
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            if os.path.exists(temp_name):
                os.remove(temp_name)
        except OSError:
            pass


def trash_upload_asset(src_path, upload_dir):
    """把上传资产软删除到回收站 {upload_dir}/.trash/，保留 30 天可恢复。

    物理删除改软删除：误删 P0 的教训——清理判据一旦出错就永久丢数据。
    upload_dir 必须是上传根目录（调用方传入，不要依赖全局常量，便于测试）。
    返回 True 表示已移入回收站；文件不存在或移动失败返回 False（调用方按
    原计数逻辑累加，不抛异常）。
    """
    try:
        if not os.path.isfile(src_path):
            return False
        trash_dir = os.path.join(upload_dir, ".trash")
        os.makedirs(trash_dir, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        base = os.path.basename(str(src_path))
        dest = os.path.join(trash_dir, f"{base}.{ts}")
        # 同一秒内同名文件已存在时加后缀，避免 shutil.move 在 Windows 上抛错或静默覆盖
        if os.path.exists(dest):
            i = 1
            while os.path.exists(os.path.join(trash_dir, f"{base}.{ts}_{i}")):
                i += 1
            dest = os.path.join(trash_dir, f"{base}.{ts}_{i}")
        shutil.move(str(src_path), dest)
        return True
    except Exception:
        return False
