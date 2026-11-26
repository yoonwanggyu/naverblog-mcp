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
            offset = sub.get(36881) or sub.get(36882) or sub.get(36880)  # OffsetTimeOriginal, Digitized, OffsetTime
            if raw_dt:
                try:
                    dt = datetime.strptime(str(raw_dt).strip(), "%Y:%m:%d %H:%M:%S")
                    if offset and re.match(r"^[+-]\d{2}:\d{2}$", str(offset).strip()):
                        # 기기마다 시간대가 다를 수 있어 이 컴퓨터의 현지 시각으로 맞춥니다(두 사람 사진을 섞어 정렬).
                        dt = datetime.fromisoformat(dt.isoformat() + str(offset).strip()).astimezone().replace(tzinfo=None)
                    info["taken_at"] = dt.isoformat(timespec="seconds")
                    info["time_source"] = "exif"
                except ValueError:
                    pass
            model = exif.get(272)
            if model:
                info["camera"] = str(model).strip()
            make = exif.get(271)
            if make:
                info["camera_make"] = str(make).strip()
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
    # 아이폰 영상의 실제 촬영 시각은 com.apple.quicktime.creationdate(시간대 포함)에 있습니다. creation_time은 Drive 업로드나
    # 내보내기 때 바뀌는 경우가 많아(실제 파일: 촬영 2025-11-03, creation_time 2026-11-21) 그다음으로만 씁니다.
    m = re.search(r"com\.apple\.quicktime\.creationdate\s*:\s*(\S+)", proc.stderr) or \
        re.search(r"creation_time\s*:\s*(\S+)", proc.stderr)
    if m:
        raw = m.group(1)
        try:  # UTC나 시간대가 붙은 값을 사진 EXIF와 같은 현지 시각으로 맞춥니다.
            raw2 = raw.replace("Z", "+00:00")
            raw2 = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", raw2)  # +0900 -> +09:00
            dt = datetime.fromisoformat(raw2)
            created = (dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt).isoformat(timespec="seconds")
        except ValueError:
            created = raw
    return dur, created


def video_model(path: Path) -> str | None:
    """영상을 찍은 기기 이름(아이폰 영상의 com.apple.quicktime.model). 기기별 사진 수를 셀 때 씁니다."""
    proc = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    m = re.search(r"com\.apple\.quicktime\.model\s*:\s*(.+)", proc.stderr) or re.search(r"\bmodel\s*:\s*(.+)", proc.stderr)
    return m.group(1).strip() if m else None


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


# ---------------------------------------------------------------- 촬영 시각과 정렬

# 파일 이름 속 날짜와 시각: KakaoTalk_20261114_123456789, IMG_20261114_123456, 20261114_123456, Screenshot_2026-11-14-12-34-56
_NAME_DT = re.compile(r"(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})[-_ T]?(\d{2})[-_.:]?(\d{2})[-_.:]?(\d{2})")


def time_from_name(name: str) -> str | None:
    m = _NAME_DT.search(name)
    if not m:
        return None
    try:
        return datetime(*map(int, m.groups())).isoformat(timespec="seconds")
    except ValueError:
        return None


def capture_time(path: Path, meta: dict[str, Any] | None = None) -> tuple[str, str]:
    """사진이나 영상이 만들어진(찍힌) 시각과 그 출처.

    두 사람이 같은 Drive 폴더에 각자 사진을 올리므로 파일 이름이나 업로드 순서가 아니라 촬영 시각으로 섞어 정렬합니다.
    1) EXIF 원본 촬영 시각(시간대가 있으면 현지 시각으로 보정) 또는 영상 메타데이터의 촬영 시각
    2) 파일 이름 속 날짜(메신저로 받아 EXIF가 지워진 사진, 스크린샷)
    3) 파일 수정 시각(마지막 수단)
    """
    if meta is None:
        if path.suffix.lower() in IMAGE_EXT:
            meta = read_exif(path)
        elif path.suffix.lower() in VIDEO_EXT:
            try:
                ct = video_info(path)[1]
            except Exception:
                ct = None
            meta = {"taken_at": ct, "time_source": "video_meta"} if ct else {}
        else:
            meta = {}
    if meta.get("taken_at"):
        return meta["taken_at"], meta.get("time_source") or "exif"
    by_name = time_from_name(path.name)
    if by_name:
        return by_name, "file_name"
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"), "file_modified"


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
                model = video_model(p)
                if model:
                    item["camera"] = model
                if d:
                    item["duration_sec"] = round(d, 1)
                if ct:
                    item["taken_at"] = ct
                    item["time_source"] = "video_meta"
            except Exception as e:
                item["error"] = f"영상 정보 읽기 실패: {e}"
        else:
            continue
        item["taken_at"], item["time_source"] = capture_time(p, item)
        items.append(item)
    # 촬영 시각순(두 사람이 올린 사진도 섞어서). 같은 시각이면 파일 이름의 숫자순(필름카메라 2 → 10, 글자순이면 10이 먼저).
    items.sort(key=lambda x: (x["taken_at"], natural_key(x["path"])))
    for i, it in enumerate(items, 1):
        it["order"] = i
    return items


# 사진 묶음: '만복국수집1.jpeg', '만복국수집2.jpeg'처럼 이름이 같고 끝 번호만 다른 사진. 카메라 기본 이름은 묶지 않습니다.
GROUP_MAX = 10   # 네이버 그룹사진(콜라주, 슬라이드) 한 묶음 최대 장수. 실제 에디터 안내문 '그룹사진은 최대 10장의 사진으로 만들 수 있습니다'
STRIP_MAX = 3    # 나란히(se-imageStrip) 한 줄 최대 장수. 공개 리뷰 40편에서 2장, 3장 줄만 확인했고 실제 에디터에서 3장까지 만들었습니다
_TRAILING_NO = re.compile(r"[\s_\-.()]*\(?(\d{1,3})\)?$")
_CAMERA_NAME = re.compile(r"^(img|dsc|dscn|dscf|pxl|mvimg|vid|mov|photo|image|kakaotalk|screenshot|screen shot|스크린샷|사진|"
                          r"제목 없음|untitled|p)[\s_\-]*$|^[\d\s_\-.]*$", re.I)


def name_stem(path: str | Path) -> tuple[str, int] | None:
    """파일 이름에서 끝 번호를 뗀 이름과 번호. 번호가 없으면 1번으로 봅니다. 카메라 기본 이름(IMG_1234 등)은 None."""
    stem = unicodedata.normalize("NFC", Path(path).stem).strip()
    m = _TRAILING_NO.search(stem)
    base, no = (stem[:m.start()], int(m.group(1))) if m and m.start() > 0 else (stem, 1)
    base = re.sub(r"[\s_\-.]+$", "", base)
    if not base or _CAMERA_NAME.match(base) or re.match(r"^(IMG|DSC|PXL|KakaoTalk)[_\-]?\d|^(스크린샷|screenshot|screen shot)", stem, re.I):
        return None
    return base, no


def photo_groups(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """scan 결과(order 순)에서 이름이 같고 번호만 다른 사진 묶음과 글 유형 힌트를 만듭니다.

    묶음은 2장 이상만, 안의 순서는 파일 번호순입니다. 10장(그룹사진 한도)을 넘으면 10장씩 나눕니다.
    여러 장소의 이름이 붙은 사진이 많으면 '모음집' 힌트를 줍니다. 판단은 Claude가 사용자 요청과 함께 합니다.
    """
    named: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    unnamed = 0
    for it in items:
        if it.get("kind") != "image" or it.get("camera_kind"):   # 카메라 사진은 camera_groups로 따로 묶습니다
            continue
        key = name_stem(it["path"])
        if key is None:
            unnamed += 1
            continue
        named.setdefault(key[0], []).append((key[1], it))
    groups: list[dict[str, Any]] = []
    for base, members in named.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda m: (m[0], m[1]["path"]))
        for start in range(0, len(members), GROUP_MAX):
            part = members[start:start + GROUP_MAX]
            groups.append({"name": base, "paths": [m[1]["path"] for m in part], "orders": [m[1]["order"] for m in part]})
    groups.sort(key=lambda g: min(g["orders"]))
    images = sum(1 for it in items if it.get("kind") == "image" and not it.get("camera_kind"))
    is_collection = len(named) >= 5 and len(named) * 2 >= images - unnamed and unnamed * 2 <= images
    hint = {
        "post_kind_hint": "collection" if is_collection else "single",
        "named_places": len(named),
        "reason": (f"사진 {images}장 중 {images - unnamed}장이 서로 다른 이름 {len(named)}개로 붙어 있습니다(여러 장소 모음으로 보임)."
                   if is_collection else "파일 이름으로는 여러 장소 모음이 아닙니다(장소 한 곳 또는 하루 코스로 보임)."),
    }
    return groups, hint


# 핸드폰이 아닌 카메라 사진: 파일 이름 낱말을 먼저 보고, 없으면 EXIF 제조사·모델을 봅니다.
_CAMERA_WORDS = [("필름카메라", re.compile(r"필름|필카|film", re.I)),
                 ("DSLR", re.compile(r"dslr|dsl|디에스엘알", re.I)),
                 ("미러리스", re.compile(r"미러리스|mirrorless", re.I)),
                 ("디지털카메라", re.compile(r"디카|디지털\s?카메라|똑딱이|compact\s?cam", re.I))]
_PHONE = re.compile(r"iphone|ipad|galaxy|^sm-|pixel|xiaomi|redmi|^lg-|oneplus|huawei|oppo|vivo|nothing|motorola|samsung", re.I)
_SCANNER = re.compile(r"noritsu|frontier|fuji\s?film\s?sp|scanner|scan|epson|plustek|pakon|coolscan", re.I)
_CAMERA_MAKERS = re.compile(r"canon|nikon|sony|fujifilm|fuji|olympus|om digital|panasonic|leica|pentax|ricoh|sigma|hasselblad|kodak|gopro|dji", re.I)


def camera_kind(path: str | Path, item: dict[str, Any] | None = None) -> dict[str, str] | None:
    """핸드폰이 아닌 카메라로 찍은 사진이면 {'label': 종류, 'device': 기기, 'source': 'file_name'|'exif'}. 핸드폰이나 알 수 없으면 None.

    1) 파일 이름에 '필름', 'film', 'DSLR', '미러리스', '디카' 같은 말이 있으면 그 종류.
    2) EXIF 기기가 필름 스캐너면 필름카메라, 카메라 제조사(Canon, Sony, Fujifilm 등)면 카메라. 핸드폰 기기면 None.
    """
    stem = unicodedata.normalize("NFC", Path(path).stem)
    for label, pat in _CAMERA_WORDS:
        if pat.search(stem):
            return {"label": label, "device": "", "source": "file_name"}
    if item is None:
        try:
            item = read_exif(Path(path)) if Path(path).suffix.lower() in IMAGE_EXT else {}
        except Exception:
            item = {}
    model, make = (item.get("camera") or "").strip(), (item.get("camera_make") or "").strip()
    device = model if make.lower() in model.lower() else f"{make} {model}".strip()
    if not device or _PHONE.search(model) or _PHONE.search(make):
        return None
    if _SCANNER.search(device):
        return {"label": "필름카메라", "device": device, "source": "exif"}
    if _CAMERA_MAKERS.search(device):
        return {"label": _camera_type(device), "device": device, "source": "exif"}
    return None


# 기종 이름으로 DSLR과 미러리스를 나눕니다. 모르면 '카메라'. 예: Canon EOS 5D Mark II → DSLR, Canon EOS R6 → 미러리스
_DSLR_MODEL = re.compile(r"canon eos(?! ?[rm]\d?\b)(?! ?r\d| ?m\d)|nikon d\d|pentax k|eos \d+d|eos kiss|rebel", re.I)
_MIRRORLESS_MODEL = re.compile(r"eos ?r\d*\b|eos ?m\d*\b|nikon z|ilce|nex-|sony a\d|x-?t\d|x-?e\d|x-?s\d|x-?pro|x-?h\d|gfx|"
                               r"om-\d|e-m\d|e-p\d|dc-g|dc-s|dmc-g|lumix|leica (sl|cl|q|m)", re.I)


def _camera_type(device: str) -> str:
    if _MIRRORLESS_MODEL.search(device):
        return "미러리스"
    if _DSLR_MODEL.search(device):
        return "DSLR"
    return "카메라"


def split_even(paths: list[Any], most: int) -> list[list[Any]]:
    """한 블록 한도(most)에 맞게 고르게 나눕니다. 11장/10 → 6+5, 29장/10 → 10+10+9, 4장/3 → 2+2, 7장/3 → 3+2+2."""
    n = -(-len(paths) // most)
    size, extra = divmod(len(paths), n)
    out, i = [], 0
    for k in range(n):
        step = size + (1 if k < extra else 0)
        out.append(paths[i:i + step])
        i += step
    return out


def natural_key(path: str | Path) -> list:
    """파일 이름을 숫자는 숫자로 비교하는 정렬 키('필름카메라 2' < '필름카메라 10')."""
    name = unicodedata.normalize("NFC", Path(path).name).lower()
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


def _film_key(path: str | Path) -> tuple:
    """필름 스캔 정렬 키: 끝 번호가 없는 첫 장('…필름카메라.JPG')은 1번, 그다음 '…필름카메라 2', '…필름카메라 3' 순."""
    stem = unicodedata.normalize("NFC", Path(path).stem)
    m = _TRAILING_NO.search(stem)
    return (stem[:m.start()], int(m.group(1))) if m and m.start() > 0 else (stem, 1)


def camera_groups(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """핸드폰이 아닌 카메라 사진을 종류(label)별로 묶습니다. 글에서는 마무리 상자 위의 '○○ 컷' 구역으로 씁니다.

    묶음 안 순서: 디지털 카메라는 촬영 순서(order). 필름카메라는 사진의 시각 정보가 촬영이 아니라 스캔 시각이라
    파일 이름의 숫자순(필름카메라 2 → 3 → … → 29)으로 둡니다(후쿠오카 폴더에서 스캔 시각순이 22 → 26 → 11로 섞임, 2026-11-26).
    """
    groups: dict[str, dict[str, Any]] = {}
    for it in items:
        kind = it.get("camera_kind")
        if it.get("kind") != "image" or not kind:
            continue
        g = groups.setdefault(kind["label"], {"label": kind["label"], "devices": [], "paths": [], "orders": []})
        if kind["device"] and kind["device"] not in g["devices"]:
            g["devices"].append(kind["device"])
        g["paths"].append(it["path"])
        g["orders"].append(it["order"])
    for g in groups.values():
        if g["label"] == "필름카메라":
            pairs = sorted(zip(g["paths"], g["orders"], strict=True), key=lambda x: _film_key(x[0]))
            g["paths"], g["orders"] = [p for p, _ in pairs], [o for _, o in pairs]
    return sorted(groups.values(), key=lambda g: min(g["orders"]))


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


def prepare_video_for_upload(path: Path) -> Path:
    """영상의 위치 정보(com.apple.quicktime.location 등)를 지운 사본을 만듭니다. 다시 인코딩하지 않아 빠르고 화질이 같습니다.

    STRIP_GPS=false면 원본을 그대로 씁니다. 영상과 소리 트랙만 남기고 메타데이터 트랙(아이폰 mebx)은 뺍니다.
    """
    if not config.STRIP_GPS:
        return path
    key = hashlib.sha1(f"{path}:{path.stat().st_mtime}".encode()).hexdigest()[:10]
    out = config.CACHE_DIR / "upload" / f"{path.stem}_{key}{path.suffix.lower()}"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(path), "-map", "0:v", "-map", "0:a?",
                           "-map_metadata", "-1", "-c", "copy", str(out)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"영상 위치 정보 제거 실패: {proc.stderr.strip()[:200]}")
    log.info("업로드용 영상 (위치 정보 제거): %s -> %s", path, out)
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
