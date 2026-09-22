"""
thumbnail_collage.py - Thumbnail kolase 3x2 dari frame video, ffmpeg saja.

Alur:
    1. Baca durasi video via ffprobe.
    2. Ambil 6 frame tersebar merata (lewati 5% awal/akhir yang biasanya
       layar hitam / loading) — tiap frame di-ekstrak TERPISAH ke JPEG dulu
       (retry bila seek gagal), lalu digabung via xstack.
    3. Gabung 6 frame jadi 1 gambar 1280x720 (grid 3x2, sel 426x360).
    4. Bila kurang dari 6 frame berhasil diambil → fallback SATU frame
       cover-crop 1280x720 (tetap tanpa pilar hitam), bukan menyerah dan
       membiarkan YouTube memakai thumbnail otomatis (pilar hitam).

Tiap frame diskalakan model "cover" (scale + crop tengah) sehingga tiap sel
terisi PENUH tanpa pilar hitam / letterbox. Penting karena sumber live
IDN/Showroom umumnya vertikal (9:16) sedangkan kartu web 16:9.

Mengapa ekstrak per-frame (bukan 6 `-ss` dalam satu perintah ffmpeg):
    - Merge live = banyak segmen; seek di tengah stream bisa gagal / kosong
      untuk satu titik tanpa harus menggagalkan seluruh kolase.
    - Pixel format bisa berbeda antar segmen → wajib `format=yuv420p`
      sebelum xstack (kalau tidak, xstack error dan thumbnail gagal total).

Dipakai untuk video BARU ke depan (jalur upload YouTube di bot/main.py dan
bot/tiktok_monitor.py): setelah upload sukses, kolase dibuat dari file lokal
lalu dipasang via YouTube API `thumbnails.set` (~50 unit kuota). Video lama
tak disentuh.
"""
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Sequence, Union

logger = logging.getLogger(__name__)

THUMB_WIDTH = 1280
THUMB_HEIGHT = 720
COLLAGE_COLS = 3
COLLAGE_ROWS = 2
COLLAGE_COUNT = COLLAGE_COLS * COLLAGE_ROWS
# 1280 tidak habis dibagi 3 (426 sisa 2). Sisa dibagikan ke kolom kanan agar
# kolase tetap PERSIS 1280x720 tanpa pilar hitam (syarat thumbnail YouTube).
_CELL_BASE_W = (THUMB_WIDTH // COLLAGE_COLS // 2) * 2
_CELL_EXTRA = THUMB_WIDTH - _CELL_BASE_W * COLLAGE_COLS
CELL_WIDTH = _CELL_BASE_W
CELL_HEIGHT = (THUMB_HEIGHT // COLLAGE_ROWS // 2) * 2


def column_width(col: int) -> int:
    """Lebar sel kolom ke-`col` (0..2). Kolom kanan menyerap sisa 2 px."""
    return _CELL_BASE_W + (_CELL_EXTRA if col == COLLAGE_COLS - 1 else 0)


COLUMN_WIDTHS = [column_width(c) for c in range(COLLAGE_COLS)]
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
    """
    Filter ffmpeg untuk 6 input -> 1 gambar PERSIS 1280x720.

    Tiap input: scale+crop "cover" (isi penuh, tanpa pilar hitam) ke ukuran
    sel-nya (kolom kanan 2 px lebih lebar), paksa yuv420p (segmen live bisa
    beda pixel format), lalu xstack grid 3x2:
        [0][1][2]
        [3][4][5]
    """
    parts = []
    for i in range(COLLAGE_COUNT):
        w = column_width(i % COLLAGE_COLS)
        parts.append(
            f"[{i}:v]scale={w}:{CELL_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={w}:{CELL_HEIGHT},setsar=1,format=yuv420p[v{i}];"
        )
    layout = "|".join(
        f"{sum(COLUMN_WIDTHS[:i % COLLAGE_COLS])}_{(i // COLLAGE_COLS) * CELL_HEIGHT}"
        for i in range(COLLAGE_COUNT)
    )
    inputs = "".join(f"[v{i}]" for i in range(COLLAGE_COUNT))
    return f"{''.join(parts)}{inputs}xstack=inputs={COLLAGE_COUNT}:layout={layout}"


def _is_nonempty_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _extract_frame_at(video: Path, t: float, dest: Path, timeout: int = 30) -> bool:
    """Ekstrak SATU frame ke `dest`. True bila file JPEG terbentuk & tidak kosong."""
    dest.unlink(missing_ok=True)
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", f"{t:.2f}", "-i", str(video),
        "-frames:v", "1", "-q:v", "2",
        str(dest),
    ]
    try:
        proc = _run(cmd, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0 and _is_nonempty_file(dest)


def _candidate_times(t: float, duration: float) -> List[float]:
    """Titik cadangan bila seek utama gagal (mundur, lalu maju sedikit)."""
    out = [t]
    for delta in (0.75, 1.5, 3.0):
        back = round(t - delta, 2)
        if back >= 0.5:
            out.append(back)
        fwd = round(t + delta, 2)
        if duration > 0 and fwd < duration - 0.2:
            out.append(fwd)
        elif duration <= 0 and fwd < 3600:
            out.append(fwd)
    # Unik, urut pertama tetap t asli.
    seen = set()
    uniq = []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def _extract_frames(video: Path, times: Sequence[float], duration: float, folder: Path) -> List[Path]:
    """
    Ekstrak frame satu per satu. Gagal di satu titik TIDAK membatalkan
    seluruh kolase — titik itu dicoba ulang dengan offset, lalu dilewati.
    """
    frames: List[Path] = []
    for i, t in enumerate(times):
        dest = folder / f"frame_{i}.jpg"
        ok = False
        for cand in _candidate_times(t, duration):
            if _extract_frame_at(video, cand, dest):
                ok = True
                break
        if ok:
            frames.append(dest)
        else:
            logger.warning("Thumbnail: gagal ekstrak frame di %.2fs (dilewati).", t)
    return frames


def _combine_frames(frames: Sequence[Path], dest: Path, timeout: int) -> bool:
    """Gabung frame JPEG jadi kolase 3x2 1280x720. Frames harus ≥ 6 (pakai 6 pertama)."""
    if len(frames) < COLLAGE_COUNT:
        return False
    dest.unlink(missing_ok=True)
    cmd: list = ["ffmpeg", "-y", "-v", "error"]
    for f in frames[:COLLAGE_COUNT]:
        cmd += ["-i", str(f)]
    cmd += [
        "-filter_complex", build_xstack_filter(),
        "-frames:v", "1",
        "-q:v", "3",
        str(dest),
    ]
    try:
        proc = _run(cmd, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return False
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", errors="replace")[-400:]
        logger.warning("Thumbnail kolase xstack gagal (exit %s): %s", proc.returncode, tail)
        return False
    return _is_nonempty_file(dest)


def _cover_single_frame(video: Path, times: Sequence[float], duration: float, dest: Path) -> bool:
    """
    Fallback: SATU frame cover-crop penuh 1280x720 (tanpa pilar hitam).
    Dipakai bila kolase 3x2 gagal — lebih baik daripada thumbnail otomatis
    YouTube yang menampilkan video vertikal dengan pilar hitam di kiri/kanan.
    """
    dest.unlink(missing_ok=True)
    candidates: List[float] = []
    for t in list(times) + [1.0, 0.1, 5.0]:
        if t not in candidates:
            candidates.append(t)
    vf = (
        f"scale={THUMB_WIDTH}:{THUMB_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={THUMB_WIDTH}:{THUMB_HEIGHT},setsar=1,format=yuv420p"
    )
    for t in candidates:
        if duration > 0 and t >= duration:
            continue
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-ss", f"{t:.2f}", "-i", str(video),
            "-frames:v", "1",
            "-vf", vf,
            "-q:v", "3",
            str(dest),
        ]
        try:
            proc = _run(cmd, timeout=30)
        except (subprocess.TimeoutExpired, OSError):
            continue
        if proc.returncode == 0 and _is_nonempty_file(dest):
            logger.warning(
                "Thumbnail kolase 3x2 tidak terbentuk — memakai fallback 1 frame @%.2fs.",
                t,
            )
            return True
    return False


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

    # Folder kerja terpisah supaya frame parsial tidak bocor ke folder download
    # dan aman bila dua upload jalan bersamaan (stem file bisa sama).
    try:
        work = Path(tempfile.mkdtemp(prefix="thumb_", dir=str(out.parent)))
    except OSError as exc:
        logger.warning("Thumbnail kolase dilewati: tak bisa buat folder kerja (%s)", exc)
        work = None

    try:
        frames = _extract_frames(src, times, duration, work) if work else []
        if len(frames) >= COLLAGE_COUNT:
            if _combine_frames(frames, out, timeout=timeout_per_frame * COLLAGE_COUNT):
                logger.info(
                    "Thumbnail kolase 3x2 dibuat: %s (video %.1fs)",
                    out, duration,
                )
                return out
            logger.warning("Thumbnail kolase: xstack gagal — mencoba fallback 1 frame.")

        # Fallback frame tunggal cover 1280x720 (tanpa pilar hitam).
        if _cover_single_frame(src, times, duration, out):
            logger.info("Thumbnail fallback 1-frame dibuat: %s (video %.1fs)", out, duration)
            return out

        logger.warning("Thumbnail kolase gagal: tidak ada frame yang bisa diekstrak.")
        return None
    finally:
        if work is not None:
            shutil.rmtree(work, ignore_errors=True)
