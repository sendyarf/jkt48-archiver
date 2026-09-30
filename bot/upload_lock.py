"""
upload_lock.py - Kunci upload antar-proses untuk satu akun Telegram.

Mengapa perlu: rate limit Telegram dihitung per AKUN, bukan per file maupun
per proses. Dua proses yang meng-upload bersamaan (mis. bot PM2 yang merekam
live baru + `python -m bot.upload_pending` yang mengosongkan antrean) saling
berebut jatah request yang sama, dan keduanya jadi kena flood lebih cepat.

`TelegramSender._upload_guard` sudah men-serialisasi upload di dalam satu
proses (asyncio.Lock), tapi tidak antar-proses. Modul ini menutup celah itu
dengan lock direktori: `os.mkdir` bersifat atomik di Linux maupun Windows,
jadi tepat satu proses yang bisa memegang lock.

Lock yang sudah basi (pemilik mati, atau lebih tua dari
`TELEGRAM_UPLOAD_STALE_MINUTES`) dianggap bebas supaya proses yang mati dengan
keras tidak membekukan upload selamanya - gejala yang sama dengan bug hang
30 Sep 2026.
"""
from __future__ import annotations

import errno
import json
import logging
import os
import socket
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from bot.config import Config

logger = logging.getLogger("upload_lock")


class UploadBusyError(RuntimeError):
    """Upload sedang dipegang proses lain di mesin ini."""


def _default_lock_path() -> Path:
    #altet: satu lock per akun Telegram, supaya dua bot berbeda dengan akun
    # berbeda tidak saling mengunci.
    account = str(Config.TELEGRAM_SESSION_STRING or Config.TELEGRAM_PHONE or "default")
    tag = "".join(ch for ch in account if ch.isalnum())[-12:] or "default"
    base = Path(Config.DB_PATH).resolve().parent
    return base / f".telegram_upload_{tag}.lock"


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":  # pragma: no cover - POSIX VPS adalah target utama
        return True
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


def _read_owner(lock_dir: Path) -> Optional[dict]:
    try:
        return json.loads((lock_dir / "owner.json").read_text(encoding="utf-8"))
    except Exception:
        return None


def _is_stale(lock_dir: Path, max_age_seconds: float) -> Optional[str]:
    """Alasan lock dianggap basi, atau None bila masih valid."""
    try:
        age = time.time() - lock_dir.stat().st_mtime
    except OSError:
        return None
    owner = _read_owner(lock_dir)
    if owner is None:
        # Tidak ada metadata: pakai umur direktori saja.
        return "metadata hilang" if age > max_age_seconds else None
    same_host = str(owner.get("host") or "") == socket.gethostname()
    if same_host and not _process_alive(int(owner.get("pid") or 0)):
        return f"proses {owner.get('pid')} sudah mati"
    # Lock milik host lain (atau pid tidak bisa diverifikasi di platform ini):
    # hanya umur yang bisa dijadikan dasar, dan hanya setelah jauh melebihi batas.
    if age > max_age_seconds:
        return f"umur {age / 60:.0f} menit > batas"
    return None


@contextmanager
def interprocess_upload_lock(
    max_age_seconds: Optional[float] = None,
    path: Optional[Path] = None,
) -> Iterator[Path]:
    """Kunci eksklusif antar-proses untuk upload media.

    Mengambil lock; bila proses lain sedang meng-upload, ``UploadBusyError``
    dilempar (bukan menunggu) supaya pemanggil bisa menunda dan mencoba lagi di
    siklus berikutnya - menunggu di sini justru membekukan antrean.
    """
    if max_age_seconds is None:
        max_age_seconds = max(5, int(Config.TELEGRAM_UPLOAD_STALE_MINUTES)) * 60
    lock_dir = Path(path) if path is not None else _default_lock_path()
    lock_dir.parent.mkdir(parents=True, exist_ok=True)

    acquired = False
    for attempt in (1, 2):
        try:
            os.mkdir(lock_dir)
            acquired = True
            break
        except FileExistsError:
            reason = _is_stale(lock_dir, max_age_seconds)
            if reason is None or attempt == 2:
                owner = _read_owner(lock_dir) or {}
                raise UploadBusyError(
                    f"upload sedang berjalan di proses lain "
                    f"(pid {owner.get('pid', '?')} di {owner.get('host', '?')}"
                    + (f", lock dianggap basi karena {reason}" if reason else "")
                    + ")"
                )
            logger.warning(
                "Mengambil alih lock upload yang basi: %s (%s)",
                lock_dir, reason,
            )
            _release(lock_dir)
        except OSError as exc:  # pragma: no cover - permissions dll.
            raise UploadBusyError(f"tidak bisa membuat lock upload: {exc}") from exc

    if not acquired:  # pragma: no cover - loop selalu raise atau break
        raise UploadBusyError("lock upload tidak bisa diambil")

    try:
        (lock_dir / "owner.json").write_text(
            json.dumps({
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "acquired_at": time.time(),
            }),
            encoding="utf-8",
        )
        yield lock_dir
    finally:
        _release(lock_dir)


def _release(lock_dir: Path) -> None:
    try:
        (lock_dir / "owner.json").unlink(missing_ok=True)
    except OSError:
        pass
    try:
        os.rmdir(lock_dir)
    except OSError:
        pass


def upload_lock_holder(path: Optional[Path] = None) -> Optional[dict]:
    """Informasi pemilik lock saat ini, atau None bila bebas."""
    lock_dir = Path(path) if path is not None else _default_lock_path()
    if not lock_dir.exists():
        return None
    owner = _read_owner(lock_dir) or {}
    owner.setdefault("path", str(lock_dir))
    return owner
