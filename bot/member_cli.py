"""
member_cli.py - CLI untuk mengelola channel/member JKT48 yang direkam bot.

Contoh pemakaian:
    python3 -m bot.member_cli list                  # daftar semua member + status
    python3 -m bot.member_cli list --mode active    # hanya yang dipantau
    python3 -m bot.member_cli list --mode stopped   # hanya yang di-stop
    python3 -m bot.member_cli list --hls            # sertakan URL HLS lengkap
    python3 -m bot.member_cli info jkt48_lulu
    python3 -m bot.member_cli add jkt48_lulu --name "Lulu JKT48"
    python3 -m bot.member_cli add jkt48_lulu --hls "https://...channel.XXXX.m3u8"
    python3 -m bot.member_cli stop jkt48-official jkt48_feni
    python3 -m bot.member_cli resume jkt48-official
    python3 -m bot.member_cli remove jkt48_lulu
    python3 -m bot.member_cli set-hls jkt48_lulu "https://...channel.XXXX.m3u8"

Catatan: perubahan berlaku pada siklus poll bot berikutnya (±15 detik) tanpa
restart. Untuk menghentikan rekaman yang SEDANG berjalan seketika, pakai
command /stop pada Telegram admin bot (lihat `python3 -m bot.admin_bot`).
"""
import argparse
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from bot import member_manager


def _plain(text: str) -> str:
    """Buang tag HTML sederhana agar rapi di terminal."""
    return re.sub(r"</?b>", "", text or "")


def _print_result(result: dict) -> None:
    icon = "✔" if result.get("ok") else "✘"
    print(f"{icon} {_plain(result.get('message', ''))}\n")


def _run_bulk(action_fn, usernames: list[str]) -> int:
    """Jalankan aksi untuk beberapa username; return exit code."""
    exit_code = 0
    for name in usernames:
        result = action_fn(name)
        _print_result(result)
        if not result.get("ok"):
            exit_code = 1
    if action_fn is member_manager.stop_member:
        print(
            "ℹ️ Rekaman yang sedang berjalan tidak dibatalkan dari CLI — pakai /stop\n"
            "   pada Telegram admin bot bila ingin menghentikan seketika.\n"
        )
    return exit_code


def cmd_list(args: argparse.Namespace) -> int:
    rows = member_manager.list_members(args.mode)
    print(member_manager.format_member_list(rows, args.mode))
    if args.hls:
        print()
        print("── HLS URL ──")
        for row in rows:
            print(f"  {row['username']:<20} {row['hls_url'] or '(belum diketahui)'}")
    print()
    print(member_manager.format_status_summary(member_manager.list_members("all")))
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    row, error = member_manager.resolve_member_token(args.username)
    if row is None:
        print(f"❌ {error}")
        return 1
    print(member_manager.format_member_detail(row))
    if row["hls_url"]:
        print(f"HLS raw    : {row['hls_url']}")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    result = member_manager.add_member(
        args.username, display_name=args.name, hls_url=args.hls
    )
    _print_result(result)
    return 0 if result.get("ok") else 1


def cmd_stop(args: argparse.Namespace) -> int:
    return _run_bulk(member_manager.stop_member, args.username)


def cmd_resume(args: argparse.Namespace) -> int:
    return _run_bulk(member_manager.resume_member, args.username)


def cmd_remove(args: argparse.Namespace) -> int:
    return _run_bulk(member_manager.remove_member, args.username)


def cmd_set_hls(args: argparse.Namespace) -> int:
    result = member_manager.set_member_hls(args.username, args.hls_url)
    _print_result(result)
    return 0 if result.get("ok") else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m bot.member_cli",
        description="Kelola channel/member yang direkam bot JKT48 Live.",
    )
    sub = parser.add_subparsers(dest="command")

    p_list = sub.add_parser("list", help="Tampilkan daftar member + status (default: all)")
    p_list.add_argument(
        "--mode",
        choices=list(member_manager.LIST_MODES),
        default="all",
        help="all | active | stopped | live | unknown",
    )
    p_list.add_argument("--hls", action="store_true", help="Tampilkan URL HLS lengkap")
    p_list.set_defaults(func=cmd_list)

    p_info = sub.add_parser("info", help="Detail satu member")
    p_info.add_argument("username", help="username atau potongan nama (mis. lulu)")
    p_info.set_defaults(func=cmd_info)

    p_add = sub.add_parser("add", help="Tambah/aktifkan member untuk direkam")
    p_add.add_argument("username")
    p_add.add_argument("--name", default=None, help="Display name (mis. 'Lulu JKT48')")
    p_add.add_argument("--hls", default=None, help="HLS URL manual (opsional)")
    p_add.set_defaults(func=cmd_add)

    p_stop = sub.add_parser("stop", help="Hentikan rekaman member (enabled=0)")
    p_stop.add_argument("username", nargs="+")
    p_stop.set_defaults(func=cmd_stop)

    p_resume = sub.add_parser("resume", help="Aktifkan kembali member yang di-stop")
    p_resume.add_argument("username", nargs="+")
    p_resume.set_defaults(func=cmd_resume)

    p_remove = sub.add_parser("remove", help="Hapus member dari database & members.txt")
    p_remove.add_argument("username", nargs="+")
    p_remove.set_defaults(func=cmd_remove)

    p_sethls = sub.add_parser("set-hls", help="Set HLS URL manual + aktifkan member")
    p_sethls.add_argument("username")
    p_sethls.add_argument("hls_url")
    p_sethls.set_defaults(func=cmd_set_hls)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())