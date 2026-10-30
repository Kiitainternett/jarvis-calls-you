"""Always-on service: answers the owner's calls, places scheduled calls, listens to owner chat commands
and an optional local webhook so other apps (CI, Home Assistant, n8n, cron) can say "call me now"."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from . import bridge as br
from . import tools
from .config import Settings
from .scheduler import Queue, in_quiet, load_routines, parse_command, parse_when

RING_TIMEOUT_S = 45
MAX_AUDIO_RETRIES = 3

HELP = {
    "en": ("Commands (send to me here):\n"
           "• call me — call now\n• call me in 20 min about the oven\n• call me at 06:30 wake me up\n"
           "• status — what is queued\n• cancel — clear the queue\nYou can also just call me on Telegram."),
    "th": ("คำสั่ง (พิมพ์ส่งมาที่นี่):\n"
           "• โทรหา — โทรเดี๋ยวนี้\n• โทรมาอีก 20 นาที เรื่อง ปิดเตา\n• โทร 06:30 เรื่อง ปลุก\n"
           "• สถานะ — ดูคิว\n• ยกเลิก — ล้างคิว\nโทรหาผมทาง Telegram ได้ตลอดเลยครับ"),
}


class Service:
    def __init__(self, s: Settings):
        self.s = s
        self.q = Queue(s.data_dir)
        self.client = self.calls = None
        self.owner_id: int | None = None
        self.active: dict[int, br.CallBridge] = {}
        self.loop: asyncio.AbstractEventLoop | None = None
        self.routines_path = s.data_dir.parent / "schedule.json"

    def t(self, key: str) -> str:
        return HELP.get(self.s.language, HELP["en"]) if key == "help" else key

    # ---------------------------------------------------------------- telegram
    async def connect(self) -> None:
        from pytgcalls import PyTgCalls, filters
        from pytgcalls.types import ChatUpdate, Device, Direction, StreamFrames
        from telethon import TelegramClient, events

        if not self.s.session_path.with_suffix(".session").exists():
            raise SystemExit("Not logged in yet. Run:  python -m callme login")
        self.client = TelegramClient(str(self.s.session_path), self.s.tg_api_id, self.s.tg_api_hash)
        await self.client.start()
        self.owner_id = await self.resolve_owner()
        self.calls = PyTgCalls(self.client)

        @self.calls.on_update(filters.stream_frame(directions=Direction.INCOMING))
        async def _frames(_, update: StreamFrames):
            b = self.active.get(update.chat_id)
            if b:
                video = update.device in (Device.CAMERA, Device.SCREEN)
                for f in update.frames:
                    b.on_incoming_video(f) if video else b.on_incoming_pcm(f.frame)

        @self.calls.on_update(filters.chat_update(ChatUpdate.Status.DISCARDED_CALL | ChatUpdate.Status.LEFT_CALL))
        async def _hangup(_, update):
            b = self.active.get(update.chat_id)
            if b:
                b.ended.set()

        @self.calls.on_update(filters.chat_update(ChatUpdate.Status.INCOMING_CALL))
        async def _incoming(_, update):
            if self.active:
                logging.info("busy, ignored call from %s", update.chat_id)
                return
            if update.chat_id == self.owner_id:
                asyncio.ensure_future(self.answer(update.chat_id))
            elif self.s.public_mode and self.public_allowed(update.chat_id):
                asyncio.ensure_future(self.answer(update.chat_id, public=True))
            else:
                logging.info("ignored call from %s", update.chat_id)  # only the owner (or demo-line callers)

        @self.client.on(events.NewMessage(incoming=True))
        async def _msg(event):
            if event.sender_id != self.owner_id or not event.is_private:
                return
            reply = self.handle_command(event.raw_text)
            if reply:
                await event.reply(reply)

        await self.calls.start()

    async def resolve_owner(self) -> int:
        o = self.s.owner.strip()
        target = int(o) if o.lstrip("-").isdigit() else o
        try:
            return (await self.client.get_entity(target)).id
        except ValueError:
            await self.client.get_dialogs()
            return (await self.client.get_entity(target)).id

    async def send_owner(self, text: str) -> None:
        try:
            await self.client.send_message(self.owner_id, text[:4000])
        except Exception:  # noqa: BLE001
            logging.exception("send message failed")

    # ---------------------------------------------------------------- commands (chat + webhook)
    def handle_command(self, text: str) -> str | None:
        c = parse_command(text)
        if not c:
            return None
        th = self.s.language == "th"
        if c["cmd"] == "help":
            return self.t("help")
        if c["cmd"] == "cancel":
            n = self.q.cancel()
            return f"ยกเลิกแล้ว {n} สาย" if th else f"Cancelled {n} call(s)."
        if c["cmd"] == "status":
            items = self.q.pending()
            if not items:
                return "ไม่มีสายในคิว" if th else "Nothing queued."
            return "\n".join(f"• {i['due'][11:16]} {i['reason'] or '-'}" for i in items)
        item = self.q.add(c["reason"], c["due"], source="chat")
        when = "เดี๋ยวนี้" if th else "now"
        if c["due"] > datetime.now() + timedelta(seconds=30):
            when = c["due"].strftime("%H:%M")
        return (f"รับทราบ จะโทรตอน {when}" if th else f"OK, I'll call you {('at ' + when) if when != 'now' else 'now'}.") + \
            f" (id {item['id']})"

    def schedule_callback(self, when: str, reason: str) -> str:
        due = parse_when(when)
        if not due:
            return "Could not understand the time. Ask again with a time like 'in 20 minutes' or '06:30'."
        self.q.add(reason, due, source="callback")
        return f"Scheduled a call at {due:%H:%M}."

    def webhook_call(self, body: dict) -> dict:
        due = parse_when(str(body.get("when", "now"))) or datetime.now()
        item = self.q.add(str(body.get("reason", ""))[:500], due, source="webhook", urgent=bool(body.get("urgent")))
        return {"ok": True, "id": item["id"], "due": item["due"]}

    def start_webhook(self) -> None:
        if not self.s.webhook_token:
            logging.info("webhook off (set WEBHOOK_TOKEN to enable)")
            return
        svc = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, data):
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                if self.path.rstrip("/") != "/call":
                    return self._send(404, {"error": "use POST /call"})
                if self.headers.get("Authorization", "") != f"Bearer {svc.s.webhook_token}":
                    return self._send(401, {"error": "bad token"})
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(n) or b"{}")
                except ValueError:
                    return self._send(400, {"error": "json body expected"})
                self._send(200, svc.webhook_call(body))

        srv = ThreadingHTTPServer((self.s.webhook_host, self.s.webhook_port), H)
        Thread(target=srv.serve_forever, daemon=True).start()
        logging.info("webhook on http://%s:%d/call", self.s.webhook_host, self.s.webhook_port)
        print(f"Webhook ready: POST http://{self.s.webhook_host}:{self.s.webhook_port}/call")

    # ---------------------------------------------------------------- calls
    async def _streams(self, user_id: int, config=None) -> None:
        from pytgcalls.types import ExternalMedia, MediaStream, RecordStream
        from pytgcalls.types.raw import AudioParameters

        params = AudioParameters(br.CALL_RATE, 1)
        await self.calls.play(user_id, MediaStream(ExternalMedia.AUDIO, params), config=config)
        await self.calls.record(user_id, RecordStream(True, params, camera=self.s.camera, screen=self.s.camera))

    def _bridge(self, user_id: int, reason: str, incoming: bool, public: bool = False,
                caller_name: str = "") -> br.CallBridge:
        b = br.CallBridge(self.calls, user_id, self.s, reason, incoming,
                          on_callback=None if public else self.schedule_callback, public=public, caller_name=caller_name)
        b.greeting = br.load_greeting(self.s, incoming, public)
        return b

    def public_allowed(self, caller_id: int) -> bool:
        """Demo line limits: per caller per day, and a total per day."""
        today = datetime.now().date().isoformat()
        mine = total = 0
        for e in self.q.entries():
            if e.get("at", "")[:10] == today and e.get("public"):
                total += 1
                mine += e.get("caller") == caller_id
        return mine < self.s.public_calls_per_caller and total < self.s.public_calls_per_day

    async def caller_label(self, uid: int) -> str:
        try:
            u = await self.client.get_entity(uid)
            name = " ".join(x for x in (getattr(u, "first_name", ""), getattr(u, "last_name", "")) if x)
            return f"{name} @{u.username}" if getattr(u, "username", None) else (name or str(uid))
        except Exception:  # noqa: BLE001
            return str(uid)

    async def place(self, reason: str) -> dict:
        from ntgcalls import TelegramServerError
        from pytgcalls.exceptions import CallBusy, CallDeclined, CallDiscarded, TimedOutAnswer
        from pytgcalls.types import CallConfig

        uid = self.owner_id
        for attempt in range(1, MAX_AUDIO_RETRIES + 1):
            b = self._bridge(uid, reason, incoming=False)
            b.start_brain()  # connect the AI while it rings: no dead air after pick-up
            self.active[uid] = b
            try:
                await self._streams(uid, CallConfig(timeout=RING_TIMEOUT_S))
            except TelegramServerError:
                await b.abort()
                self.active.pop(uid, None)
                await asyncio.sleep(3)
                continue
            except (TimedOutAnswer, CallDeclined, CallBusy, CallDiscarded) as e:
                await b.abort()
                self.active.pop(uid, None)
                return {"result": {TimedOutAnswer: "no_answer", CallDeclined: "declined", CallBusy: "busy",
                                   CallDiscarded: "declined"}[type(e)]}
            except Exception as e:  # noqa: BLE001
                logging.exception("call failed")
                await b.abort()
                self.active.pop(uid, None)
                return {"result": "failed", "error": f"{type(e).__name__}: {e}"}
            try:
                return {"result": "connected", **(await b.run())}
            finally:
                self.active.pop(uid, None)
        return {"result": "failed", "error": "audio did not connect"}

    async def answer(self, uid: int, public: bool = False) -> None:
        label = await self.caller_label(uid) if public else ""
        b = self._bridge(uid, "", incoming=True, public=public, caller_name=label.split(" @")[0] if public else "")
        self.active[uid] = b
        try:
            await self._streams(uid)
            r = {"result": "connected", **(await b.run())}
        except Exception as e:  # noqa: BLE001
            logging.exception("answer failed")
            await b.abort()
            r = {"result": "failed", "error": str(e)}
        finally:
            self.active.pop(uid, None)
        if public:
            r.update(public=True, caller=uid)
        await self.after_call(f"public call from {label}" if public else "incoming", r)

    async def after_call(self, reason: str, r: dict) -> None:
        self.q.log({"at": datetime.now().isoformat(timespec="seconds"), "reason": reason,
                    **{k: v for k, v in r.items() if k != "lines"}})
        if r.get("result") != "connected":
            return
        head = f"🌐 {reason}\n" if r.get("public") else ""
        parts = [f"{head}📞 {r.get('seconds', 0) // 60}:{r.get('seconds', 0) % 60:02d}"]
        if r.get("notes"):
            parts.append("📝 " + "\n📝 ".join(r["notes"]))
        summary = await asyncio.to_thread(summarize, self.s, r.get("lines", []))
        if summary:
            parts.append(summary)
        if len(parts) > 1:
            await self.send_owner("\n\n".join(parts))

    # ---------------------------------------------------------------- scheduler loop
    async def tick(self) -> None:
        now = datetime.now()
        for r in self.q.routines_due(load_routines(self.routines_path), now):
            self.q.add(r.get("reason", ""), now, source="routine")
        if self.active:
            return
        item = self.q.pop_due(now)
        if not item:
            return
        if not item.get("urgent"):
            if in_quiet(now, self.s.quiet_hours):
                self.q.add(item["reason"], self._after_quiet(now), item["source"], attempt=item["attempt"])
                return
            if self.q.calls_today(now) >= self.s.max_calls_per_day:
                await self.send_owner(f"(skipped call, daily limit) {item['reason']}")
                return
        r = await self.place(item["reason"])
        await self.after_call(item["reason"], r)
        if r["result"] in ("no_answer", "busy") and item["attempt"] < self.s.retry_no_answer:
            self.q.add(item["reason"], datetime.now() + timedelta(minutes=self.s.retry_after_min),
                       item["source"], item.get("urgent", False), item["attempt"] + 1)
        elif r["result"] != "connected":
            await self.send_owner(f"☎️ {r['result']}: {item['reason']}")

    def _after_quiet(self, now: datetime) -> datetime:
        end = datetime.strptime(self.s.quiet_hours[1], "%H:%M").time()
        due = datetime.combine(now.date(), end)
        return due if due > now else due + timedelta(days=1)

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        loaded = tools.load_plugins(self.s.tools_dir)
        await self.connect()
        made = await br.prepare_greetings(self.s)
        self.start_webhook()
        print(f"{self.s.assistant_name} is ready. Owner id {self.owner_id}. Plugins: {', '.join(loaded) or '-'}."
              f" Greetings recorded: {made}. Send 'help' to the AI account on Telegram.")
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                logging.exception("tick failed")
            await asyncio.sleep(5)


def summarize(s: Settings, lines: list[str]) -> str:
    if len(lines) < 2:
        return ""
    try:
        from google import genai

        client = genai.Client(api_key=s.gemini_api_key)
        r = client.models.generate_content(
            model=s.text_model,
            contents=f"Summarize this phone call in {s.language_name} in 1-3 short bullet points: decisions, "
                     "to-dos, numbers. Transcript:\n" + "\n".join(lines[-80:]))
        return (r.text or "").strip()
    except Exception:  # noqa: BLE001 — a summary is a bonus, never fail the call flow for it
        logging.exception("summary failed")
        return ""
