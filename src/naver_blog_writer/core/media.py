"""사진과 동영상 처리: 폴더 스캔, EXIF 읽기, 미리보기 축소, HEIC 변환, 영상 프레임 추출."""

from __future__ import annotations

import hashlib
import io
import logging
import re
import subprocess
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import ExifTags, Image, ImageOps

from .. import config

log = logging.getLogger(__name__)

try:  # 아이폰 HEIC 사진 지원
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIF_OK = True
except Exception:  # pragma: no cover
    HEIF_OK = False

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".heif"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
# 네이버 에디터에 그대로 올려도 되는 형식. 나머지는 JPEG로 변환합니다.
UPLOAD_NATIVE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp"}
UPLOAD_MAX_BYTES = 10 * 1024 * 1024


class MediaAccessError(PermissionError):
    pass


# ---------------------------------------------------------------- 경로 보안

def check_allowed(path: str | Path) -> Path:
    """MEDIA_ROOTS(또는 서버 캐시 폴더) 안의 파일만 허용합니다.

    프롬프트 인젝션 등으로 Claude가 엉뚱한 파일(예: 문서, 키 파일)을
    블로그에 올리는 사고를 막기 위한 장치입니다.
    """
    p = Path(path).expanduser()
    try:
        p = p.resolve(strict=True)
    except FileNotFoundError:
        raise FileNotFoundError(f"파일이나 폴더가 없습니다: {path}") from None
    roots = list(config.MEDIA_ROOTS) + [config.CACHE_DIR.resolve()]
    if not config.MEDIA_ROOTS:
        raise MediaAccessError(
            "MEDIA_ROOTS 환경 변수가 비어 있습니다. Claude Desktop 설정의 env에 사진 폴더 경로를 지정하세요."
        )
    # macOS는 한글 경로를 NFD(자모 분리)로 돌려주기도 해서 문자열 비교 전에 NFC로 맞춥니다.
    target = _nfc_parts(p)
    for root in roots:
        rp = _nfc_parts(root)
        if target[: len(rp)] == rp:
            return p
    raise MediaAccessError(f"허용된 폴더(MEDIA_ROOTS) 밖의 경로입니다: {p}")


def _nfc_parts(p: Path) -> tuple[str, ...]:
    parts = tuple(unicodedata.normalize("NFC", s) for s in p.parts)
    if sys.platform in ("win32", "darwin"):  # 두 OS 모두 기본 파일시스템이 대소문자를 구분하지 않음
        parts = tuple(s.casefold() for s in parts)
    return parts


# ---------------------------------------------------------------- EXIF

def _gps_to_deg(values: Any, ref: str | None) -> float | None:
    try:
        d, m, s = (float(v) for v in values)
        deg = d + m / 60 + s / 3600
        if ref in ("S", "W"):
            deg = -deg
        return round(deg, 6)
    except Exception:
        return None


def read_exif(path: Path) -> dict[str, Any]:
    info: dict[str, Any] = {}
    try:
        with Image.open(path) as im:
            info["width"], info["height"] = im.size
            exif = im.getexif()
            if not exif:
                return info
            sub = exif.get_ifd(0x8769)  # Exif IFD
            raw_dt = sub.get(36867) or sub.get(36868) or exif.get(306)  # DateTimeOriginal, Digitized, DateTime
            if raw_dt:
                try:
                    info["taken_at"] = datetime.strptime(str(raw_dt).strip(), "%Y:%m:%d %H:%M:%S").isoformat()
                except ValueError:
                    pass
            model = exif.get(272)
            if model:
                info["camera"] = str(model).strip()
            gps = exif.get_ifd(0x8825)
            if gps:
                tags = {ExifTags.GPSTAGS.get(k, k): v for k, v in gps.items()}
                lat = _gps_to_deg(tags.get("GPSLatitude"), tags.get("GPSLatitudeRef"))
                lon = _gps_to_deg(tags.get("GPSLongitude"), tags.get("GPSLongitudeRef"))
                if lat is not None and lon is not None:
                    info["gps"] = {"lat": lat, "lon": lon}
    except Exception as e:
        info["error"] = f"EXIF 읽기 실패: {e}"
    return info


# ---------------------------------------------------------------- ffmpeg

def ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def video_info(path: Path) -> tuple[float | None, str | None]:
    """영상 길이(초)와 촬영 시각을 ffmpeg 한 번 실행으로 읽습니다."""
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    dur: float | None = None
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr)
    if m:
        h, mi, sec = m.groups()
        dur = int(h) * 3600 + int(mi) * 60 + float(sec)
    created: str | None = None
    m = re.search(r"creation_time\s*:\s*(\S+)", proc.stderr)
    if m:
        raw = m.group(1)
        try:  # 영상 메타데이터는 UTC라서 사진 EXIF와 같은 현지 시각으로 맞춥니다.
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            created = dt.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
        except ValueError:
            created = raw
    return dur, created


def video_duration(path: Path) -> float | None:
    return video_info(path)[0]


def extract_frames(video: Path, count: int = 6, max_width: int = 1280) -> list[Path]:
    """영상 전체 길이에서 균등한 간격으로 프레임을 뽑아 JPEG로 저장합니다."""
    count = max(1, min(count, 20))
    dur = video_duration(video)
    if not dur or dur <= 0:
        raise RuntimeError(f"영상 길이를 읽지 못했습니다: {video}")
    key = hashlib.sha1(f"{video}:{video.stat().st_mtime}".encode()).hexdigest()[:10]
    out_dir = config.CACHE_DIR / "frames" / f"{video.stem}_{key}"
    out_dir.mkdir(parents=True, exist_ok=True)
    frames: list[Path] = []
    for i in range(count):
        t = (i + 0.5) * dur / count
        out = out_dir / f"frame_{i + 1:02d}_{t:07.2f}s.jpg"
        if not out.exists():
            proc = subprocess.run(
                [
                    ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", f"{t:.2f}", "-i", str(video),
                    "-frames:v", "1", "-q:v", "3",
                    "-vf", f"scale='min({max_width},iw)':-2",
                    str(out),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if proc.returncode != 0 or not out.exists():
                log.warning("프레임 추출 실패 t=%.2f: %s", t, proc.stderr.strip())
                continue
        frames.append(out)
    if not frames:
        raise RuntimeError("프레임을 하나도 추출하지 못했습니다.")
    return frames


# 폴더 찾기(이름 해석, 날짜와 오타 비교)는 core/folders.py에 있습니다.


# ---------------------------------------------------------------- 스캔과 미리보기

def scan_folder(folder: Path, recursive: bool = False) -> list[dict[str, Any]]:
    pattern = "**/*" if recursive else "*"
    items: list[dict[str, Any]] = []
    for p in sorted(folder.glob(pattern)):
        if not p.is_file() or p.name.startswith("."):
            continue
        ext = p.suffix.lower()
        if ext in IMAGE_EXT:
            item = {"path": str(p), "kind": "image", "size_kb": round(p.stat().st_size / 1024)}
            item.update(read_exif(p))
        elif ext in VIDEO_EXT:
            item = {"path": str(p), "kind": "video", "size_kb": round(p.stat().st_size / 1024)}
            try:
                d, ct = video_info(p)
                if d:
                    item["duration_sec"] = round(d, 1)
                if ct:
                    item["taken_at"] = ct
            except Exception as e:
                item["error"] = f"영상 정보 읽기 실패: {e}"
        else:
            continue
        items.append(item)
    # 촬영 시각이 있으면 시간순, 없으면 파일명순
    items.sort(key=lambda x: (x.get("taken_at") or "9999", x["path"]))
    return items


def preview_jpeg(path: Path, max_side: int = 1024, quality: int = 82) -> bytes:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=quality)
        return buf.getvalue()


def has_gps(path: Path) -> bool:
    try:
        with Image.open(path) as im:
            gps = im.getexif().get_ifd(0x8825)
            return bool(gps)
    except Exception:
        return False


def folder_gps(folder: Path) -> dict[str, float] | None:
    """폴더 사진 중 GPS가 있는 첫 사진의 좌표(장소 검색 기준점). 파일을 최대 5개만 엽니다."""
    files = sorted(p for p in folder.rglob("*") if p.suffix.lower() in IMAGE_EXT and not p.name.startswith("."))
    for p in files[:5]:
        gps = read_exif(p).get("gps")
        if gps:
            return gps
    return None


def prepare_for_upload(path: Path) -> Path:
    """업로드용 파일을 준비합니다.

    - HEIC, WEBP처럼 에디터가 받기 어려운 형식이나 10MB를 넘는 파일은 JPEG로 변환합니다.
    - STRIP_GPS=true(기본)이고 사진에 GPS 위치 정보가 있으면 위치를 지운 JPEG 사본을 올립니다.
      사진 방향(회전)은 픽셀에 반영하므로 EXIF를 지워도 돌아가지 않습니다.
    """
    ext = path.suffix.lower()
    strip = config.STRIP_GPS and ext in {".jpg", ".jpeg", ".heic", ".heif", ".webp", ".png"} and has_gps(path)
    if ext in UPLOAD_NATIVE_EXT and path.stat().st_size <= UPLOAD_MAX_BYTES and not strip:
        return path
    if ext in {".heic", ".heif"} and not HEIF_OK:
        raise RuntimeError("HEIC 변환에 pillow-heif가 필요합니다.")
    key = hashlib.sha1(f"{path}:{path.stat().st_mtime}:{int(strip)}".encode()).hexdigest()[:10]
    out = config.CACHE_DIR / "upload" / f"{path.stem}_{key}.jpg"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        im.thumbnail((4000, 4000))
        im.save(out, format="JPEG", quality=92)  # exif 인자를 주지 않으므로 GPS를 포함한 메타데이터가 빠집니다.
    log.info("업로드용 변환%s: %s -> %s", " (GPS 제거)" if strip else "", path, out)
    return out


def previews_within_budget(paths: list[Path], max_side: int, budget_bytes: int) -> tuple[list[bytes], int]:
    """사진 미리보기를 만들되, 전체 크기(base64 기준)가 예산을 넘으면 크기와 화질을 줄여 다시 만듭니다."""
    side, quality = max_side, 82
    while True:
        datas = [preview_jpeg(p, max_side=side, quality=quality) for p in paths]
        total_b64 = sum((len(d) + 2) // 3 * 4 for d in datas)
        if total_b64 <= budget_bytes or side <= 384:
            return datas, side
        side = max(384, int(side * 0.8))
        quality = max(65, quality - 5)
