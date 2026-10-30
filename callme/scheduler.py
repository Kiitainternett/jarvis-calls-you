"""Call queue, daily routines, quiet hours and the "when" parser (English + Thai)."""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

_lock = threading.Lock()


def parse_when(text: str, now: datetime | None = None) -> datetime | None:
    """'now' / 'in 10 min' / 'in 2 hours' / '07:30' / '7.30' / 'อีก 15 นาที' / '2 ชั่วโมง' / 'ตอน 6:30' → datetime"""
    now = now or datetime.now()
    t = (text or "").strip().lower()
    if not t or t in ("now", "ตอนนี้", "เดี๋ยวนี้", "เลย"):
        return now
    m = re.search(r"(\d+(?:\.\d+)?)\s*(m|min|mins|minute|minutes|นาที)\b", t) or \
        re.search(r"(\d+(?:\.\d+)?)\s*(นาที)", t)
    if m:
        return now + timedelta(minutes=float(m[1]))
    m = re.search(r"(\d+(?:\.\d+)?)\s*(h|hr|hrs|hour|hours|ชั่วโมง|ชม)", t)
    if m:
        return now + timedelta(hours=float(m[1]))
    m = re.search(r"\b(\d{1,2})[:.](\d{2})\s*(am|pm)?", t)
    if m:
        h, mi = int(m[1]), int(m[2])
        if m[3] == "pm" and h < 12:
            h += 12
        if m[3] == "am" and h == 12:
            h = 0
        if h > 23 or mi > 59:
            return None
        due = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        return due if due > now else due + timedelta(days=1)
    m = re.search(r"\b(\d{1,2})\s*(am|pm)\b", t)
    if m:
        h = int(m[1]) % 12 + (12 if m[2] == "pm" else 0)
        due = now.replace(hour=h, minute=0, second=0, microsecond=0)
        return due if due > now else due + timedelta(days=1)
    return None


def _hm(s: str, base: datetime) -> datetime:
    h, m = [int(x) for x in s.replace(".", ":").split(":")]
    return base.replace(hour=h, minute=m, second=0, microsecond=0)


def in_quiet(now: datetime, quiet: tuple[str, str] | None) -> bool:
    if not quiet:
        return False
    a, b = _hm(quiet[0], now), _hm(quiet[1], now)
    return a <= now < b if a < b else (now >= a or now < b)


DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def day_matches(spec: str, now: datetime) -> bool:
    spec = (spec or "daily").lower().replace(" ", "")
    if spec in ("daily", "everyday", "all", "ทุกวัน"):
        return True
    today = DAYS[now.weekday()]
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            if a in DAYS and b in DAYS:
                i, j = DAYS.index(a), DAYS.index(b)
                rng = DAYS[i:j + 1] if i <= j else DAYS[i:] + DAYS[:j + 1]
                if today in rng:
                    return True
        elif part == today:
            return True
    return False


class Queue:
    """Pending calls stored in data/queue.json so they survive restarts."""

    def __init__(self, data_dir: Path):
        self.path = data_dir / "queue.json"
        self.log_path = data_dir / "calls.jsonl"
        self.routine_state = data_dir / "routines_done.json"

    def _read(self) -> list:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _write(self, items: list) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def add(self, reason: str, due: datetime, source: str = "", urgent: bool = False, attempt: int = 0) -> dict:
        item = {"id": uuid.uuid4().hex[:8], "reason": reason, "due": due.isoformat(timespec="seconds"),
                "source": source, "urgent": urgent, "attempt": attempt}
        with _lock:
            items = self._read()
            items.append(item)
            items.sort(key=lambda x: x["due"])
            self._write(items)
        return item

    def pending(self) -> list:
        with _lock:
            return self._read()

    def pop_due(self, now: datetime) -> dict | None:
        with _lock:
            items = self._read()
            for i, it in enumerate(items):
                if datetime.fromisoformat(it["due"]) <= now:
                    items.pop(i)
                    self._write(items)
                    return it
        return None

    def cancel(self, item_id: str = "all") -> int:
        with _lock:
            items = self._read()
            keep = [] if item_id == "all" else [x for x in items if x["id"] != item_id]
            self._write(keep)
            return len(items) - len(keep)

    def log(self, entry: dict) -> None:
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def entries(self) -> list:
        try:
            return [json.loads(x) for x in self.log_path.read_text(encoding="utf-8").splitlines() if x.strip()]
        except (OSError, ValueError):
            return []

    def calls_today(self, now: datetime) -> int:
        n = 0
        try:
            for line in self.log_path.read_text(encoding="utf-8").splitlines():
                e = json.loads(line)
                if (e.get("at", "")[:10] == now.date().isoformat() and e.get("result") == "connected"
                        and not e.get("public")):
                    n += 1
        except (OSError, ValueError):
            pass
        return n

    def routines_due(self, routines: list, now: datetime) -> list:
        """Daily routines whose time passed within the last 10 minutes and that have not fired today."""
        try:
            done = json.loads(self.routine_state.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            done = {}
        out = []
        for r in routines:
            key = f"{r.get('time')}|{r.get('reason', '')[:40]}"
            try:
                at = _hm(r["time"], now)
            except (KeyError, ValueError):
                continue
            if done.get(key) == now.date().isoformat() or not day_matches(r.get("days", "daily"), now):
                continue
            if at <= now < at + timedelta(minutes=10):
                done[key] = now.date().isoformat()
                out.append(r)
        if out:
            self.routine_state.write_text(json.dumps(done), encoding="utf-8")
        return out


def load_routines(path: Path) -> list:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data.get("routines", [])
    except (OSError, ValueError):
        return []


# ---------------------------------------------------------------- owner chat commands
CALL_WORDS = ("call me", "call", "โทร", "โทรหา", "โทรมา")
CANCEL_WORDS = ("cancel", "ยกเลิก")
STATUS_WORDS = ("status", "สถานะ", "queue", "คิว")


def parse_command(text: str, now: datetime | None = None) -> dict | None:
    """Owner's Telegram message → {"cmd": call|cancel|status|help, ...}"""
    t = (text or "").strip()
    low = t.lower()
    if not t:
        return None
    if low in ("help", "/help", "/start", "ช่วย", "วิธีใช้"):
        return {"cmd": "help"}
    if any(low.startswith(w) for w in CANCEL_WORDS):
        return {"cmd": "cancel"}
    if any(low.startswith(w) for w in STATUS_WORDS):
        return {"cmd": "status"}
    if any(low.startswith(w) for w in CALL_WORDS):
        rest = t
        for w in sorted(CALL_WORDS, key=len, reverse=True):
            if low.startswith(w):
                rest = t[len(w):].strip()
                break
        due = parse_when(rest, now) or (now or datetime.now())
        reason = re.split(r"\b(?:about|re|to)\b|เรื่อง|เพื่อ", rest, maxsplit=1)
        reason = reason[1].strip() if len(reason) > 1 else rest
        return {"cmd": "call", "due": due, "reason": reason}
    return None
