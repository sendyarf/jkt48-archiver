"""
auth_youtube.py - YouTube OAuth setup (Supports multi-channel).

Run this on your local PC to authorise the bot to upload to YouTube channels.
It opens a browser for Google OAuth consent and saves the token JSON.

Usage:
    python -m bot.auth_youtube              # Authorise all configured channels
    python -m bot.auth_youtube --channel 1  # Authorise Channel 1 only
    python -m bot.auth_youtube --channel 2  # Authorise Channel 2 only
"""
import argparse
import os
from google_auth_oauthlib.flow import InstalledAppFlow
from bot.config import Config

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def auth_single_channel(label: str, secret_file: str, token_file: str) -> bool:
    print("-" * 60)
    print(f"Authenticating: {label}")
    print(f"  Secret: {secret_file}")
    print(f"  Target Token: {token_file}")

    if not os.path.exists(secret_file):
        print(f"  ❌ Secret file not found: {secret_file}")
        print("  Download OAuth client secret from Google Cloud Console first.")
        return False

    try:
        flow = InstalledAppFlow.from_client_secrets_file(secret_file, SCOPES)
        creds = flow.run_local_server(port=0)
        with open(token_file, "w") as f:
            f.write(creds.to_json())
        print(f"  ✅ Token successfully saved to: {token_file}")
        return True
    except Exception as e:
        print(f"  ❌ Authentication failed for {label}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="YouTube Multi-Channel OAuth Setup")
    parser.add_argument(
        "--channel",
        type=int,
        default=None,
        help="Specific channel number to authenticate (e.g. 1 or 2)",
    )
    args = parser.parse_args()

    channels = Config.load_youtube_channels()
    if not channels:
        print("❌ No channels configured in .env")
        return

    print("=" * 60)
    print("  JKT48 Live Bot - YouTube OAuth Setup")
    print(f"  Found {len(channels)} configured channel(s)")
    print("=" * 60)

    if args.channel is not None:
        idx = args.channel - 1
        if 0 <= idx < len(channels):
            ch = channels[idx]
            auth_single_channel(ch.label, ch.secret_file, ch.token_file)
        else:
            print(f"❌ Channel index {args.channel} out of range (1 - {len(channels)})")
    else:
        for ch in channels:
            auth_single_channel(ch.label, ch.secret_file, ch.token_file)
            print()

    print("=" * 60)
    print("Authentication process complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
