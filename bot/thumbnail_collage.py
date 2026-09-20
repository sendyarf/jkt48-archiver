"""
thumbnail_collage.py - Thumbnail kolase 3x2 dari frame video, ffmpeg saja.

Alur:
    1. Baca durasi video via ffprobe.
    2. Ambil 6 frame tersebar merata (lewati 5% awal/akhir yang biasanya
       layar hitam / loading) via `ffmpeg -ss <t> -frames:v 1`.
    3. Gabung 6 frame jadi 1 gambar 1280x720 (grid 3x2, sel 426x360) via
       filter_complex xstack. Tanpa dependensi baru (Pillow TIDAK dipakai).

Tiap frame diskalakan model "cover" (scale + crop tengah) sehingga tiap sel
terisi PENUH tanpa pilar hitam / letterbox. Penting karena sumber live
IDN/Showroom umumnya vertikal (9:16) sedangkan kartu web 16:9.

Dipakai untuk video BARU ke depan (jalur upload YouTube di bot/main.py):
setelah upload sukses, kolase dibuat dari file lokal lalu dipasang via
YouTube API `thumbnails.set` (~50 unit kuota). Video lama tak disentuh.
"""
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Sequence, Union

logger = logging.getLogger(__name__)

THUMB_WIDTH = 1280
THUMB_HEIGHT = 720
COLLAGE_COLS = 3
COLLAGE_ROWS = 2
COLLAGE_COUNT = COLLAGE_COLS * COLLAGE_ROWS
CELL_WIDTH = (THUMB_WIDTH // COLLAGE_COLS // 2) * 2
CELL_HEIGHT = (THUMB_HEIGHT // COLLAGE_ROWS // 2) * 2
EDGE_MARGIN_RATIO = 0.05


def _run(cmd: Sequence[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(c) for c in cmd],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def get_duration_seconds(video_path: Union[str, Path]) -> float:
    if shutil.which("ffprobe") is None:
        return 0.0
    proc = _run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ])
    if proc.returncode != 0:
        return 0.0
    try:
        return max(0.0, float(proc.stdout.decode("utf-8", errors="replace").strip()))
    except (ValueError, TypeError):
        return 0.0


def pick_sample_times(duration: float, count: int = COLLAGE_COUNT) -> list:
    """Titik waktu tersebar merata antara margin awal & akhir video."""
    if duration <= 0:
        duration = 60.0
    margin = duration * EDGE_MARGIN_RATIO
    span = max(duration - 2 * margin, 1.0)
    base = margin + span / (2 * count)
    step = span / count
    # Mundur dari tengah: titik ke-i = base + i * step (cukup unik selama
    # step >= 0.01 dtk; untuk video super pendek step ~0.15 dtk tetap unik).
    times = [round(base + i * step, 2) for i in range(count)]
    # Jaga batas bawah & keunikan absolut (pembulatan bisa bikin kembar).
    seen: set = set()
    out: list = []
    for t in times:
        t = max(0.5, t)
        while t in seen:
            t = round(t + 0.01, 2)
        seen.add(t)
        out.append(t)
    return out


def build_xstack_filter() -> str:
    scale_crop = (
        f"scale={CELL_WIDTH}:{CELL_HEIGHT}:"
        f"force_original_aspect_ratio=increase,"
        f"crop={CELL_WIDTH}:{CELL_HEIGHT},setsar=1"
    )
    parts = "".join(f"[{i}:v]{scale_crop}[v{i}];" for i in range(COLLAGE_COUNT))
    layout = "|".join(
        f"{(i % COLLAGE_COLS) * CELL_WIDTH}_{(i // COLLAGE_COLS) * CELL_HEIGHT}"
        for i in range(COLLAGE_COUNT)
    )
    inputs = "".join(f"[v{i}]" for i in range(COLLAGE_COUNT))
    return f"{parts}{inputs}xstack=inputs={COLLAGE_COUNT}:layout={layout}"


def build_collage(
    video_path: Union[str, Path],
    output_path: Union[str, Path, None] = None,
    *,
    timeout_per_frame: int = 120,
) -> Optional[Path]:
    src = Path(video_path)
    if not src.exists():
        logger.warning("Thumbnail kolase dilewati: file tidak ada: %s", src)
        return None
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        logger.warning("Thumbnail kolase dilewati: ffmpeg/ffprobe tak tersedia.")
        return None
    out = Path(output_path) if output_path else src.with_name(f"{src.stem}_thumb.jpg")
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("Thumbnail kolase dilewati: folder %s (%s)", out.parent, exc)
        return None
    duration = get_duration_seconds(src)
    times = pick_sample_times(duration)
    cmd: list = ["ffmpeg", "-y", "-v", "error"]
    for t in times:
        cmd += ["-ss", f"{t:.2f}", "-i", str(src)]
    cmd += [
        "-filter_complex", build_xstack_filter(),
        "-frames:v", "1",
        "-q:v", "3",
        str(out),
    ]
    try:
        proc = _run(cmd, timeout=timeout_per_frame * COLLAGE_COUNT)
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Thumbnail kolase gagal (ffmpeg error): %s", exc)
        return None
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", errors="replace")[-500:]
        logger.warning("Thumbnail kolase gagal (exit %s): %s", proc.returncode, tail)
        return None
    if not out.exists() or out.stat().st_size == 0:
        logger.warning("Thumbnail kolase gagal: output tak terbentuk.")
        return None
    logger.info("Thumbnail kolase 3x2 dibuat: %s (video %.1fs)", out, duration)
    return out
