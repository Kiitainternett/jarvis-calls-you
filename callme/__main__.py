"""python -m callme  login | serve | call "reason" | check"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from . import config


async def _login(s) -> None:
    from telethon import TelegramClient

    client = TelegramClient(str(s.session_path), s.tg_api_id, s.tg_api_hash)
    await client.start()  # asks for phone number, login code and 2FA password in the terminal
    me = await client.get_me()
    print(f"Logged in as {me.first_name} (id {me.id}). Keep data/ai_account.session private: it is the account key.")
    await client.disconnect()


def _exit_now(code: int) -> None:
    # Real first-run test 2026-10-31: `callme call` hung forever after hanging up, because PyTgCalls keeps a
    # non-daemon thread pool that it has no way to stop, and Python waits for those threads at normal exit.
    sys.stdout.flush()
    sys.stderr.flush()
    logging.shutdown()
    os._exit(code)


async def _call(s, reason: str) -> None:
    from . import tools
    from .service import Service

    tools.load_plugins(s.tools_dir)
    svc = Service(s)
    await svc.connect()
    code = 1
    try:
        r = await svc.place(reason)
        print("result:", r["result"], r.get("error", ""))
        await svc.after_call(reason, r)
        code = 0 if r["result"] == "connected" else 1
    except Exception as e:  # noqa: BLE001 — still has to reach the exit below, or the process hangs
        logging.exception("call failed")
        print(f"call failed: {type(e).__name__}: {e}")
    finally:
        await svc.close()
    _exit_now(code)


def _check(s) -> None:
    ok = True
    for mod, pip in (("telethon", "telethon"), ("pytgcalls", "py-tgcalls"), ("google.genai", "google-genai"),
                     ("audioop", "audioop-lts")):
        try:
            __import__(mod)
            print(f"✓ {pip}")
        except ImportError:
            ok = False
            print(f"✗ {pip}  →  pip install {pip}")
    try:
        import cv2  # noqa: F401
        print("✓ opencv (camera in calls)")
    except ImportError:
        print("• opencv not installed: calls work, camera images are skipped")
    # Found testing the first-run experience: with a blank .env this used to print only "owner=" and a login hint,
    # never saying which settings were still empty.
    required = {"GEMINI_API_KEY": s.gemini_api_key, "TG_API_ID": s.tg_api_id, "TG_API_HASH": s.tg_api_hash, "OWNER": s.owner}
    missing = [k for k, v in required.items() if not v]
    if missing:
        ok = False
        print("✗ not filled in .env: " + ", ".join(missing))
    else:
        print("✓ .env has the required settings")
    print("✓ logged in" if s.session_path.with_suffix(".session").exists() else "✗ not logged in  →  python -m callme login")
    print(f"owner={s.owner} language={s.language} voice={s.voice} model={s.live_model}")
    print(f"quiet hours={s.quiet_hours} max calls/day={s.max_calls_per_day} webhook={'on' if s.webhook_token else 'off'}")
    sys.exit(0 if ok else 1)


def main() -> None:
    p = argparse.ArgumentParser(prog="callme", description="An AI that phones you on Telegram.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login", help="log in the AI's Telegram account (one time)")
    sub.add_parser("serve", help="run 24/7: answer calls, scheduled calls, chat commands, webhook")
    c = sub.add_parser("call", help="call the owner once, right now")
    c.add_argument("reason", nargs="?", default="", help="what the call is about")
    sub.add_parser("check", help="check installation and settings")
    a = p.parse_args()
    s = config.load(require_keys=a.cmd != "check")
    logging.basicConfig(filename=str(s.data_dir / "callme.log"), level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        if a.cmd == "login":
            asyncio.run(_login(s))
        elif a.cmd == "call":
            asyncio.run(_call(s, a.reason))
        elif a.cmd == "check":
            _check(s)
        else:
            from .service import Service

            asyncio.run(Service(s).run())
    except KeyboardInterrupt:
        print("stopped")


if __name__ == "__main__":
    main()
