"""
tiktok_media.py - Unduh & olah media TikTok (video, foto, story).

Tanggung jawab modul ini:
  1. Unduh VIDEO postingan/story (yt-dlp; fallback unduh langsung dari CDN).
  2. Unduh daftar FOTO sebuah postingan foto (slide) secara berurutan.
  3. Susun FOTO menjadi satu video slide show (ffmpeg) untuk arsip YouTube.
  4. Bagi daftar foto menjadi beberapa part supaya muat album Telegram
     (batas Telegram = 10 media per album) — persis kebutuhan pemilik proyek.

Format keluaran yang dipakai arsip:
  - VIDEO : mp4 dari yt-dlp (audio+video tergabung).
  - FOTO  : file gambar asli (webp/jpg) + `slide.mp4` (slide show) — file
            gambar tetap disimpan supaya user bisa mengunduh FOTONYA lewat bot
            Telegram, sedangkan `slide.mp4` yang diunggah ke YouTube.

Semua fungsi async memakai subprocess yang bisa dibatalkan (kill) dan tidak
pernah melempar exception tak tertangani ke loop utama bot — pemanggil
(`tiktok_monitor`) yang memutuskan status `pending_upload`/`failed`.
"""
import asyncio
import contextlib
import json
import logging
import mimetypes
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Union

import httpx

from bot.config import Config
from bot.tiktok_client import TikTokItem, fetch_embed_post_media, ytdlp_command
from bot.video_splitter import get_video_metadata

logger = logging.getLogger(__name__)

# Ukuran slide show: portrait 1080x1920 (rasio foto TikTok).
SLIDESHOW_WIDTH = 1080
SLIDESHOW_HEIGHT = 1920
SLIDESHOW_FPS = 30

# Header browser untuk mengambil media langsung dari CDN TikTok.
_MEDIA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.tiktok.com/",
    "Accept": "*/*",
}

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic"}


class MediaError(RuntimeError):
    """Kegagalan mengunduh/mengolah media TikTok.

    `permanent=True` menandai kegagalan yang TIDAK layak di-retry dari IP ini
    (mis. blokir IP/geo TikTok) — pemanggil (`tiktok_monitor`) bisa langsung
    menandai postingan 'failed' tanpa menunggu batas percobaan tercapai.
    """

    permanent: bool = False


class MediaPermanentError(MediaError):
    """Kegagalan PERMANEN: IP diblokir TikTok / postingan tidak tersedia."""

    permanent: bool = True


def is_permanent_media_error(exc: BaseException) -> bool:
    """True bila exception adalah MediaError yang tergolong permanen."""
    return isinstance(exc, MediaError) and bool(getattr(exc, "permanent", False))


# Pola pesan yt-dlp untuk postingan yang diblokir IP / tidak tersedia.
_IP_BLOCK_PATTERNS = (
    "ip address is blocked",
    "blocked from accessing",
    "this post is not available",
)


def _is_ip_block_output(text: str) -> bool:
    """Deteksi pesan blokir IP/geo di output yt-dlp (case-insensitive)."""
    lower = (text or "").lower()
    return any(pattern in lower for pattern in _IP_BLOCK_PATTERNS)


def _ytdlp_config_args() -> list[str]:
    """
    Argumen yt-dlp dari konfigurasi (cookies/browser/proxy/extra).

    Semuanya opsional — tanpa konfigurasi, hasilnya daftar kosong (no-op).
    """
    args: list[str] = []
    if Config.TIKTOK_YTDLP_COOKIES_FILE:
        args += ["--cookies", Config.TIKTOK_YTDLP_COOKIES_FILE]
    if Config.TIKTOK_YTDLP_COOKIES_FROM_BROWSER:
        args += ["--cookies-from-browser", Config.TIKTOK_YTDLP_COOKIES_FROM_BROWSER]
    if Config.TIKTOK_YTDLP_PROXY:
        args += ["--proxy", Config.TIKTOK_YTDLP_PROXY]
    if Config.TIKTOK_YTDLP_EXTRA_ARGS:
        args += shlex.split(Config.TIKTOK_YTDLP_EXTRA_ARGS)
    return args


async def _kill_process(process: "asyncio.subprocess.Process") -> None:
    """Paksa kill proses yt-dlp (best-effort) dan tunggu sampai benar-benar mati."""
    with contextlib.suppress(Exception):
        process.kill()
    with contextlib.suppress(Exception):
        await process.wait()


def tiktok_work_dir(subdir: str = "") -> Path:
    """Folder kerja arsip TikTok (dibuat bila belum ada)."""
    base = Path(Config.DOWNLOAD_DIR) / "tiktok"
    if subdir:
        base = base / subdir
    base.mkdir(parents=True, exist_ok=True)
    return base


def post_dir(item: TikTokItem) -> Path:
    """Folder khusus satu postingan: <DOWNLOAD_DIR>/tiktok/<akun>/<id>."""
    return tiktok_work_dir(str(item.unique_id or "unknown")) / str(item.id)


def split_image_paths(
    paths: list[Union[str, Path]], per_part: Optional[int] = None
) -> list[list[Path]]:
    """
    Bagi daftar foto menjadi beberapa part album Telegram.

    Contoh (per_part = 10): 23 foto → [10, 10, 3] → 3 album.
    Daftar kosong → [] (pemanggil memperlakukannya sebagai "tidak ada foto").
    """
    size = int(per_part or Config.TIKTOK_PHOTOS_PER_PART or 10)
    size = max(1, min(10, size))   # Telegram: maksimum 10 media per album
    files = [Path(p) for p in paths]
    return [files[i:i + size] for i in range(0, len(files), size)]


def _guess_suffix(url: str, fallback: str = ".jpg") -> str:
    """Ambil ekstensi dari URL (memperhatikan query string)."""
    path = url.split("?", 1)[0].split("#", 1)[0]
    suffix = Path(path).suffix.lower()
    if suffix in _IMAGE_SUFFIXES:
        return suffix
    guessed = mimetypes.guess_extension(mimetypes.guess_type(path)[0] or "")
    return guessed if guessed in _IMAGE_SUFFIXES else fallback


async def download_images(
    urls: Iterable[str],
    dest_dir: Union[str, Path],
    timeout_seconds: float = 60.0,
) -> list[Path]:
    """
    Unduh daftar foto berurutan (nomor urut dipertahankan pada nama file).

    Entri yang berupa PATH LOKAL (dipakai fixture/uji) disalin, bukan diunduh.
    Foto yang gagal diunduh dilewati dengan peringatan — sisanya tetap dipakai.
    """
    out_dir = Path(dest_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    local_files: list[Path] = []
    async with httpx.AsyncClient(
        timeout=timeout_seconds, headers=_MEDIA_HEADERS, follow_redirects=True
    ) as client:
        for index, url in enumerate(urls, start=1):
            source = str(url).strip()
            if not source:
                continue
            target = out_dir / f"{index:02d}{_guess_suffix(source)}"
            try:
                local_source = Path(source)
                if "://" not in source and local_source.exists():
                    shutil.copyfile(local_source, target)
                else:
                    response = await client.get(source)
                    response.raise_for_status()
                    target.write_bytes(response.content)
                if target.stat().st_size == 0:
                    raise MediaError("file kosong")
                local_files.append(target)
            except Exception as exc:  # noqa: BLE001 - satu foto gagal ≠ gagal total
                logger.warning("Unduhan foto #%d gagal (%s): %s", index, source[:80], exc)
    return local_files

async def download_video(
    item: TikTokItem,
    dest_dir: Optional[Union[str, Path]] = None,
    timeout_seconds: float = 600.0,
) -> Path:
    """
    Unduh video postingan/story TikTok.

    Jalur utama: yt-dlp (memilih kualitas + muxing audio).
    Jalur cadangan: unduh langsung URL CDN tanpa watermark (`video_url`) — dipakai
    bila yt-dlp gagal (mis. pada penyedia `fixture`/`tikwm`).

    Returns:
        Path file mp4 hasil unduhan.

    Raises:
        MediaError: kedua jalur gagal.
    """
    out_dir = Path(dest_dir) if dest_dir else post_dir(item)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{item.id}.mp4"

    # 1) yt-dlp (juga menangani story).
    #    Format dipaksa MENGECUALIKAN varian berwatermark (format_id
    #    download/download_addr, format_note ~watermark) — pilihan `best`
    #    bisa jatuh ke downloadAddr bila playAddr gagal diuji/404, dan itu
    #    membakar watermark TikTok ke file → beda dengan arsip Telegram.
    #    Bila tidak ada format bersih, yt-dlp gagal → lanjut rung embed.
    if "://" in item.page_url:
        clean_fmt = (
            "bestvideo[format_id!~=download][format_note!~=watermark]"
            "+bestaudio"
            "/best[format_id!~=download][format_note!~=watermark]"
        )
        cmd = [
            *ytdlp_command(),
            "--no-playlist",
            "--no-warnings",
            "--newline",
            "--format", clean_fmt,
            "--merge-output-format", "mp4",
            "--retries", "5",
            "--retry-sleep", "5",
            "--output", str(target),
            *_ytdlp_config_args(),
            item.page_url,
        ]
        process: Optional[asyncio.subprocess.Process] = None
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
            if process.returncode == 0 and target.exists() and target.stat().st_size > 0:
                logger.info("Video TikTok terunduh via yt-dlp: %s", target.name)
                return target
            output = (stdout or b"").decode("utf-8", errors="replace").strip()
            if _is_ip_block_output(output):
                # Blokir IP/geo: tidak layak di-retry dari IP ini — hentikan di
                # sini sebagai kegagalan PERMANEN (fallback CDN/embed pun hampir
                # pasti ikut terblokir pada post yang sama).
                logger.warning(
                    "yt-dlp: %s diblokir dari IP ini (permanen): %s",
                    item.id, output[-300:],
                )
                raise MediaPermanentError(
                    f"Video TikTok {item.id} diblokir dari IP ini: {output[-200:]}"
                )
            logger.warning("yt-dlp gagal untuk %s (kode %s): %s", item.id, process.returncode, output[-300:])
        except asyncio.TimeoutError:
            # Timeout: hentikan proses yt-dlp agar tidak bocor (orphan) dan
            # terus memakai bandwidth; jalur fallback di bawah tetap dicoba.
            logger.warning("yt-dlp timeout saat mengunduh %s", item.id)
            if process is not None:
                await _kill_process(process)
        except asyncio.CancelledError:
            # Task dibatalkan (hard shutdown): pastikan yt-dlp benar-benar mati.
            if process is not None:
                await _kill_process(process)
            raise
        except FileNotFoundError as exc:
            logger.warning("yt-dlp tidak tersedia: %s", exc)

    # 2) Unduh langsung dari CDN tanpa watermark.
    #    JANGAN pakai `wmplay` (varian berwatermark) — bila `video_url` kosong
    #    (tikwm hanya punya wmplay), lanjut ke rung embed di bawah.
    cdn_url = item.video_url or ""
    if cdn_url:
        try:
            async with httpx.AsyncClient(
                timeout=timeout_seconds, headers=_MEDIA_HEADERS, follow_redirects=True
            ) as client:
                async with client.stream("GET", cdn_url) as response:
                    response.raise_for_status()
                    with open(target, "wb") as handle:
                        async for chunk in response.aiter_bytes(1024 * 256):
                            handle.write(chunk)
            if target.exists() and target.stat().st_size > 0:
                logger.info("Video TikTok terunduh via CDN: %s", target.name)
                return target
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unduhan CDN gagal untuk %s: %s", item.id, exc)

    # 3) URL segar dari halaman embed TikTok.
    #    URL CDN (tikwm/listing) bertanda tangan dan cepat kedaluwarsa → 403.
    #    Halaman /embed/v2/<id> selalu membalas URL yang masih berlaku.
    try:
        fresh = await fetch_embed_post_media(item.unique_id, item.id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("embed media %s gagal: %s", item.id, exc)
        fresh = {}
    fresh_url = (fresh or {}).get("video_url") or ""
    if fresh_url and fresh_url != cdn_url:
        try:
            async with httpx.AsyncClient(
                timeout=timeout_seconds, headers=_MEDIA_HEADERS, follow_redirects=True
            ) as client:
                response = await client.get(fresh_url)
                response.raise_for_status()
                target.write_bytes(response.content)
            if target.exists() and target.stat().st_size > 0:
                logger.info("Video TikTok terunduh via URL embed: %s", target.name)
                return target
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unduhan via embed gagal untuk %s: %s", item.id, exc)

    raise MediaError(f"Video TikTok {item.id} tidak bisa diunduh")


def copy_fixture_video(item: TikTokItem, dest_dir: Optional[Union[str, Path]] = None) -> Path:
    """
    Salin video contoh dari disk (dipakai fixture/uji).

    Fixture menaruh path lokal di `item.raw['fixture_media']['video']`; berkas itu
    disalin menjadi `<id>.mp4` supaya alur lanjutan (split/upload) sama persis
    dengan video hasil unduhan.
    """
    source = str(((item.raw.get("fixture_media") or {}).get("video") or "")).strip()
    if not source or not Path(source).exists():
        raise MediaError(f"fixture_media.video tidak ada untuk {item.id}")
    out_dir = Path(dest_dir) if dest_dir else post_dir(item)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{item.id}.mp4"
    shutil.copyfile(source, target)
    return target



def _ffconcat_escape(path: Path) -> str:
    """
    Path untuk berkas daftar `ffconcat`.

    Bentuk absolut + garis miring depan (as_posix) supaya bisa dibaca ffmpeg di
    Windows maupun Linux; tanda kutip tunggal di nama berkas di-escape.
    """
    return path.resolve().as_posix().replace("'", "'\\''")


async def build_slideshow(
    images: Iterable[Union[str, Path]],
    out_path: Union[str, Path],
    seconds_per_photo: Optional[float] = None,
    music_path: Optional[Union[str, Path]] = None,
) -> Path:
    """
    Susun daftar FOTO menjadi satu video slide show (mp4 H.264, portrait).

    Implementasi memakai demuxer `concat` ffmpeg: setiap foto diberi
    `duration`, lalu frame di-scale + padding ke SLIDESHOW_WIDTH x
    SLIDESHOW_HEIGHT (rasio dipertahankan, sisa diberi warna hitam).

    Foto terakhir ditulis DUA KALI: demuxer concat mengabaikan `duration` pada
    entri terakhir, sehingga tanpa pengulangan foto terakhir berkedip
    sekejap/terpotong.

    Returns:
        Path video slide show.

    Raises:
        MediaError: tidak ada foto, atau ffmpeg gagal.
    """
    files = [Path(p) for p in images if str(p).strip()]
    if not files:
        raise MediaError("Slide show butuh minimal satu foto")
    # WAJIB absolut: demuxer `concat` ffmpeg menyelesaikan path relatif terhadap
    # lokasi berkas daftar (bukan CWD), sehingga DOWNLOAD_DIR relatif (mis.
    # "tmp/...") membuat ffmpeg gagal "No such file or directory" (insiden
    # 21 Sep 2026 saat menguji unduhan postingan foto).
    files = [path.resolve() for path in files]

    seconds = float(
        seconds_per_photo
        or Config.TIKTOK_SLIDESHOW_SECONDS_PER_PHOTO
        or 3
    )
    seconds = max(1.0, seconds)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    list_file = out.parent / f"{out.stem}_concat.txt"
    lines: list[str] = []
    for path in files:
        lines.append(f"file '{_ffconcat_escape(path)}'")
        lines.append(f"duration {seconds:.3f}")
    lines.append(f"file '{_ffconcat_escape(files[-1])}'")
    lines.append(f"duration {seconds:.3f}")
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    filters = (
        f"scale={SLIDESHOW_WIDTH}:{SLIDESHOW_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={SLIDESHOW_WIDTH}:{SLIDESHOW_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black,"
        "format=yuv420p"
    )
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
    ]
    if music_path and Path(music_path).exists():
        cmd += ["-i", str(music_path)]
    cmd += [
        "-vf", filters,
        "-r", str(SLIDESHOW_FPS),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "22",
        "-movflags", "+faststart",
    ]
    if music_path and Path(music_path).exists():
        cmd += ["-c:a", "aac", "-b:a", "128k", "-shortest"]
    cmd.append(str(out))

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
    except FileNotFoundError as exc:
        raise MediaError(f"ffmpeg tidak ditemukan: {exc}") from exc
    finally:
        list_file.unlink(missing_ok=True)

    if process.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        message = (stderr or b"").decode("utf-8", errors="replace")[-400:]
        raise MediaError(f"ffmpeg gagal membuat slide show: {message}")

    logger.info(
        "Slide show dibuat dari %d foto: %s (%.1f detik)",
        len(files), out.name, len(files) * seconds,
    )
    return out


@dataclass
class PreparedMedia:
    """
    Hasil penyiapan media satu postingan TikTok.

    - `video_path`   : file video yang cocok diunggah ke YouTube. Untuk
                       postingan FOTO ini adalah slide show hasil ffmpeg.
    - `archive_video_path`: file video asli yang dikirim ke channel Telegram
                       (None untuk postingan foto — yang dikirim foto-fotonya).
    - `image_parts`  : foto yang sudah dibagi per album Telegram (maks 10/part).
    """

    kind: str
    video_path: Optional[Path] = None
    archive_video_path: Optional[Path] = None
    images: list[Path] = field(default_factory=list)
    image_parts: list[list[Path]] = field(default_factory=list)
    duration_seconds: float = 0.0
    size_bytes: int = 0


async def prepare_post_media(
    item: TikTokItem,
    build_slideshow_video: bool = True,
) -> PreparedMedia:
    """
    Siapkan seluruh media satu postingan (unduh + (bila foto) buat slide show).

    Postingan FOTO:
      - setiap foto diunduh berurutan → dibagi menjadi part album Telegram;
      - bila `build_slideshow_video`, semua foto dirangkai jadi `slide.mp4`
        untuk diunggah ke YouTube.

    Postingan VIDEO/STORY:
      - satu file mp4 (yt-dlp, atau salin fixture saat pengujian).

    Raises:
        MediaError: tidak ada media yang berhasil disiapkan.
    """
    work = post_dir(item)
    if item.kind == "photo" and item.image_count > 0:
        images = await download_images(item.images, work)
        if not images:
            # Daftar foto bisa kosong bila listing embed menandai postingan foto
            # tanpa URL gambar. Ambil daftar segar dari halaman embed per-post.
            try:
                fresh = await fetch_embed_post_media(item.unique_id, item.id)
            except Exception as exc:  # noqa: BLE001
                logger.debug("embed foto %s gagal: %s", item.id, exc)
                fresh = {}
            fallback_urls = (fresh or {}).get("images") or []
            if fallback_urls:
                logger.info(
                    "Daftar %d foto %s diambil dari halaman embed.",
                    len(fallback_urls), item.id,
                )
                item.images = list(fallback_urls)
                images = await download_images(item.images, work)
        if not images:
            raise MediaError(f"Tidak ada foto yang berhasil diunduh untuk {item.id}")
        parts = split_image_paths(images)
        slide: Optional[Path] = None
        if build_slideshow_video:
            slide = await build_slideshow(images, work / "slide.mp4")
        total_seconds = len(images) * float(
            Config.TIKTOK_SLIDESHOW_SECONDS_PER_PHOTO or 3
        )
        size = sum(p.stat().st_size for p in images) + (
            slide.stat().st_size if slide is not None else 0
        )
        return PreparedMedia(
            kind="photo",
            video_path=slide,
            archive_video_path=None,
            images=images,
            image_parts=parts,
            duration_seconds=total_seconds,
            size_bytes=size,
        )

    # Video/story
    fixture_video = str(((item.raw.get("fixture_media") or {}).get("video") or "")).strip()
    video = (
        copy_fixture_video(item, work)
        if fixture_video and Path(fixture_video).exists()
        else await download_video(item, work)
    )
    meta = await get_video_metadata(video)
    return PreparedMedia(
        kind="video",
        video_path=video,
        archive_video_path=video,
        duration_seconds=meta.duration_seconds,
        size_bytes=meta.size_bytes or video.stat().st_size,
    )


def media_from_disk(post: dict) -> Optional[PreparedMedia]:
    """
    Bangun kembali `PreparedMedia` dari berkas yang MASIH ada di disk.

    Dipakai jalur retry: postingan yang gagal di-upload tidak perlu diunduh
    ulang (hemat bandwidth dan tetap berhasil walau media TikTok sudah hilang).
    Kembalikan None bila berkasnya sudah tidak ada → pemanggil mengunduh ulang.

    `post` adalah baris `tiktok_posts` (punya `kind`, `media_path`,
    `local_images_json`, `duration_seconds`).
    """
    kind = (post.get("kind") or "video").strip() or "video"
    try:
        candidates = json.loads(post.get("local_images_json") or "[]")
    except ValueError:
        candidates = []
    images = [Path(p) for p in candidates if str(p).strip()]
    images = [p for p in images if p.exists()]

    media_path = Path(post["media_path"]) if post.get("media_path") else None
    if media_path is not None and not media_path.exists():
        media_path = None

    duration = float(post.get("duration_seconds") or 0)
    if kind == "photo":
        if not images:
            return None
        slide = media_path if media_path is not None and media_path.suffix == ".mp4" else None
        size = sum(p.stat().st_size for p in images)
        if slide is not None:
            size += slide.stat().st_size
        return PreparedMedia(
            kind="photo",
            video_path=slide,
            archive_video_path=None,
            images=images,
            image_parts=split_image_paths(images),
            duration_seconds=duration,
            size_bytes=size,
        )

    if media_path is None:
        return None
    return PreparedMedia(
        kind="video",
        video_path=media_path,
        archive_video_path=media_path,
        duration_seconds=duration,
        size_bytes=media_path.stat().st_size,
    )


def cleanup_media(media: PreparedMedia) -> None:
    """
    Hapus berkas kerja sementara (foto/part) setelah arsip sukses.

    File slide show hanya dihapus bila tidak dikirim ke Telegram — pengiriman
    media ke Telegram terjadi lebih dulu, jadi memanggil ini setelah semua
    upload selesai aman. Kegagalan hapus tidak pernah fatal.
    """
    for path in [*media.images, media.video_path, media.archive_video_path]:
        if path is None:
            continue
        try:
            Path(path).unlink(missing_ok=True)
        except OSError as exc:  # pragma: no cover - jalur IO
            logger.debug("Gagal menghapus berkas sementara %s: %s", path, exc)

