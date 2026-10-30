"""Settings from environment / .env (no extra dependency)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(os.environ.get("CALLME_HOME", Path.cwd())).resolve()


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        elif " #" in val:
            val = val.split(" #", 1)[0].rstrip()
        os.environ.setdefault(key.strip(), val)


def _hours(text: str) -> tuple[str, str] | None:
    text = (text or "").strip()
    if not text or "-" not in text:
        return None
    a, b = [x.strip() for x in text.split("-", 1)]
    return a, b


LANG_NAMES = {"th": "Thai", "en": "English", "ja": "Japanese", "zh": "Chinese", "ko": "Korean", "vi": "Vietnamese",
              "id": "Indonesian", "es": "Spanish", "fr": "French", "de": "German", "pt": "Portuguese", "hi": "Hindi"}


@dataclass
class Settings:
    gemini_api_key: str
    tg_api_id: int
    tg_api_hash: str
    owner: str  # Telegram user id (digits) or @username of the person the AI calls
    owner_name: str = "boss"
    assistant_name: str = "Jarvis"
    language: str = "en"
    timezone: str = ""
    live_model: str = "gemini-3.1-flash-live-preview"
    text_model: str = "gemini-flash-latest"
    voice: str = "Charon"
    quiet_hours: tuple[str, str] | None = ("22:00", "07:00")
    max_calls_per_day: int = 6
    retry_no_answer: int = 1
    retry_after_min: float = 3.0
    max_call_minutes: int = 20
    webhook_host: str = "127.0.0.1"
    webhook_port: int = 8787
    webhook_token: str = ""
    camera: bool = True
    public_mode: bool = False  # strangers may call the AI (demo line), with strict limits
    public_max_minutes: int = 3
    public_calls_per_caller: int = 2
    public_calls_per_day: int = 40
    data_dir: Path = field(default_factory=lambda: ROOT / "data")
    tools_dir: Path = field(default_factory=lambda: ROOT / "tools")
    persona_file: Path = field(default_factory=lambda: ROOT / "persona.md")
    public_persona_file: Path = field(default_factory=lambda: ROOT / "public_persona.md")

    @property
    def language_name(self) -> str:
        return LANG_NAMES.get(self.language, self.language)

    @property
    def session_path(self) -> Path:
        return self.data_dir / "ai_account"

    def persona(self) -> str:
        if self.persona_file.exists():
            return self.persona_file.read_text(encoding="utf-8")
        return f"You are {self.assistant_name}, a calm, witty personal AI assistant."

    def public_persona(self) -> str:
        if self.public_persona_file.exists():
            return self.public_persona_file.read_text(encoding="utf-8")
        return f"You are {self.assistant_name}, the AI assistant of {self.owner_name}, answering a public demo line."


def load(require_keys: bool = True) -> Settings:
    load_dotenv(ROOT / ".env")
    env = os.environ.get
    missing = [k for k in ("GEMINI_API_KEY", "TG_API_ID", "TG_API_HASH", "OWNER") if not env(k)]
    if require_keys and missing:
        raise SystemExit("Missing settings in .env: " + ", ".join(missing) + "  (copy .env.example to .env)")
    qh = env("QUIET_HOURS", "22:00-07:00")
    s = Settings(
        gemini_api_key=env("GEMINI_API_KEY", ""),
        tg_api_id=int(env("TG_API_ID", "0") or 0),
        tg_api_hash=env("TG_API_HASH", ""),
        owner=env("OWNER", ""),
        owner_name=env("OWNER_NAME", "boss"),
        assistant_name=env("ASSISTANT_NAME", "Jarvis"),
        language=env("LANGUAGE", "en"),
        timezone=env("TIMEZONE", ""),
        live_model=env("LIVE_MODEL", "gemini-3.1-flash-live-preview"),
        text_model=env("TEXT_MODEL", "gemini-flash-latest"),
        voice=env("VOICE", "Charon"),
        quiet_hours=_hours(qh) if qh.lower() not in ("", "off", "none") else None,
        max_calls_per_day=int(env("MAX_CALLS_PER_DAY", "6")),
        retry_no_answer=int(env("RETRY_NO_ANSWER", "1")),
        retry_after_min=float(env("RETRY_AFTER_MIN", "3")),
        max_call_minutes=int(env("MAX_CALL_MINUTES", "20")),
        webhook_host=env("WEBHOOK_HOST", "127.0.0.1"),
        webhook_port=int(env("WEBHOOK_PORT", "8787")),
        webhook_token=env("WEBHOOK_TOKEN", ""),
        camera=env("CAMERA", "on").lower() not in ("0", "off", "false", "no"),
        public_mode=env("PUBLIC_MODE", "off").lower() in ("1", "on", "true", "yes"),
        public_max_minutes=int(env("PUBLIC_MAX_MINUTES", "3")),
        public_calls_per_caller=int(env("PUBLIC_CALLS_PER_CALLER", "2")),
        public_calls_per_day=int(env("PUBLIC_CALLS_PER_DAY", "40")),
    )
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s
