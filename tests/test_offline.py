"""Offline tests: no Telegram, no Gemini. Run: python -m pytest -q"""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from callme import config, scheduler, tools

NOW = datetime(2026, 10, 30, 14, 0)


def test_parse_when():
    assert scheduler.parse_when("in 10 min", NOW) == NOW + timedelta(minutes=10)
    assert scheduler.parse_when("อีก 15 นาที", NOW) == NOW + timedelta(minutes=15)
    assert scheduler.parse_when("2 ชั่วโมง", NOW) == NOW + timedelta(hours=2)
    assert scheduler.parse_when("07:30", NOW) == datetime(2026, 10, 31, 7, 30)
    assert scheduler.parse_when("ตอน 16.45", NOW) == datetime(2026, 10, 30, 16, 45)
    assert scheduler.parse_when("9pm", NOW) == datetime(2026, 10, 30, 21, 0)
    assert scheduler.parse_when("now", NOW) == NOW
    assert scheduler.parse_when("banana", NOW) is None


def test_quiet_and_days():
    q = ("22:00", "07:00")
    assert scheduler.in_quiet(NOW.replace(hour=23), q)
    assert scheduler.in_quiet(NOW.replace(hour=3), q)
    assert not scheduler.in_quiet(NOW, q)
    assert scheduler.day_matches("mon-fri", NOW)  # Friday
    assert not scheduler.day_matches("sat,sun", NOW)
    assert scheduler.day_matches("fri-mon", NOW)


def test_commands():
    c = scheduler.parse_command("call me in 20 min about the oven", NOW)
    assert c["cmd"] == "call" and c["due"] == NOW + timedelta(minutes=20) and c["reason"] == "the oven"
    c = scheduler.parse_command("โทรมาอีก 5 นาที เรื่อง ปิดเตา", NOW)
    assert c["cmd"] == "call" and c["reason"] == "ปิดเตา"
    assert scheduler.parse_command("ยกเลิก")["cmd"] == "cancel"
    assert scheduler.parse_command("status")["cmd"] == "status"
    assert scheduler.parse_command("hello there") is None


def test_queue_and_routines(tmp_path):
    q = scheduler.Queue(tmp_path)
    a = q.add("later", NOW + timedelta(minutes=5))
    q.add("now", NOW)
    assert q.pop_due(NOW)["reason"] == "now"
    assert q.pop_due(NOW) is None
    assert q.pop_due(NOW + timedelta(minutes=6))["id"] == a["id"]
    r = [{"time": "13:55", "days": "daily", "reason": "check-in"}]
    assert len(q.routines_due(r, NOW)) == 1
    assert q.routines_due(r, NOW) == []  # only once per day
    q.log({"at": NOW.isoformat(), "result": "connected"})
    assert q.calls_today(NOW) == 1


def test_dotenv_and_plugins(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("LANGUAGE=th   # comment\nOWNER_NAME='Sir'\n", encoding="utf-8")
    monkeypatch.delenv("LANGUAGE", raising=False)
    monkeypatch.delenv("OWNER_NAME", raising=False)
    config.load_dotenv(env)
    assert os.environ["LANGUAGE"] == "th" and os.environ["OWNER_NAME"] == "Sir"
    loaded = tools.load_plugins(Path(__file__).resolve().parent.parent / "tools")
    assert "example_tools" in loaded
    names = [d["name"] for d in tools.declarations()]
    assert {"hang_up_call", "take_note", "schedule_callback", "get_current_time", "check_website"} <= set(names)
    assert "Unknown tool" in tools.run("nope", {})


def test_live_config_is_valid(tmp_path):
    from google.genai import types

    from callme.bridge import CallBridge, greeting_texts

    s = config.Settings(gemini_api_key="x", tg_api_id=1, tg_api_hash="x", owner="1", owner_name="Sir",
                        language="th", data_dir=tmp_path)
    b = CallBridge(None, 1, s, "test reason")
    cfg = types.LiveConnectConfig(**b.live_config(None))
    assert cfg.tools and cfg.response_modalities
    b.greeting = {"greet_text": "hi"}
    types.LiveConnectConfig(**b.live_config("handle123"))
    assert "Sir" in greeting_texts(s, False)["greet"]


def test_service_commands_without_telegram(tmp_path):
    from callme.service import Service

    s = config.Settings(gemini_api_key="x", tg_api_id=1, tg_api_hash="x", owner="1", data_dir=tmp_path)
    svc = Service(s)
    assert "call" in svc.handle_command("call me in 10 min about tax").lower()
    assert len(svc.q.pending()) == 1
    assert svc.webhook_call({"reason": "server down", "urgent": True})["ok"]
    assert "Scheduled" in svc.schedule_callback("in 20 minutes", "follow up")
    assert "Cancelled 3" in svc.handle_command("cancel")


def test_public_mode_limits_and_tools(tmp_path):
    import asyncio

    from google.genai import types

    from callme.bridge import PUBLIC_TOOLS, CallBridge, greeting_texts
    from callme.service import Service

    s = config.Settings(gemini_api_key="x", tg_api_id=1, tg_api_hash="x", owner="1", owner_name="Sir",
                        data_dir=tmp_path, public_mode=True, public_calls_per_caller=2, public_calls_per_day=3)
    b = CallBridge(None, 99, s, "", incoming=True, public=True, caller_name="Ann")
    cfg = b.live_config(None)
    types.LiveConnectConfig(**cfg)
    names = {d["name"] for d in cfg["tools"][1]["function_declarations"]}
    assert names <= PUBLIC_TOOLS and "schedule_callback" not in names
    assert "NOT Sir" in cfg["system_instruction"] and b.max_s == 180
    assert "Not available" in asyncio.run(b.run_tool("check_website", {"url": "x"}))
    assert "AI assistant" in greeting_texts(s, True, public=True)["greet"]
    svc = Service(s)
    assert svc.public_allowed(99)
    for _ in range(2):
        svc.q.log({"at": datetime.now().isoformat(), "public": True, "caller": 99, "result": "connected"})
    assert not svc.public_allowed(99) and svc.public_allowed(7)
    svc.q.log({"at": datetime.now().isoformat(), "public": True, "caller": 7, "result": "connected"})
    assert not svc.public_allowed(8)  # daily total reached
    assert svc.q.calls_today(datetime.now()) == 0  # public calls do not eat the owner's daily limit


def test_check_names_the_missing_settings(tmp_path, capsys):
    """First-run experience: a blank .env must say which settings are still empty."""
    import pytest

    from callme.__main__ import _check

    s = config.Settings(gemini_api_key="", tg_api_id=0, tg_api_hash="", owner="", data_dir=tmp_path)
    with pytest.raises(SystemExit) as e:
        _check(s)
    out = capsys.readouterr().out
    assert e.value.code == 1
    assert "not filled in .env: GEMINI_API_KEY, TG_API_ID, TG_API_HASH, OWNER" in out
    s = config.Settings(gemini_api_key="x", tg_api_id=1, tg_api_hash="x", owner="1", data_dir=tmp_path)
    with pytest.raises(SystemExit) as e:
        _check(s)
    assert "required settings" in capsys.readouterr().out and e.value.code == 0


def test_live_config_without_search_tool(tmp_path):
    """Real bug 2026-10-31: google_search made the Live handshake fail with a fake 'quota' 1011; we retry without it."""
    from google.genai import types

    from callme.bridge import CallBridge

    s = config.Settings(gemini_api_key="x", tg_api_id=1, tg_api_hash="x", owner="1", data_dir=tmp_path)
    b = CallBridge(None, 1, s, "t")
    assert any("google_search" in t for t in b.live_config(None)["tools"])
    b.use_search = False
    cfg = b.live_config(None)
    assert not any("google_search" in t for t in cfg["tools"]) and cfg["tools"][0]["function_declarations"]
    types.LiveConnectConfig(**cfg)
    assert CallBridge.use_search is True  # the switch is per-call, not global


def test_brain_error_classification():
    from callme.bridge import brain_error
    assert "quota" in brain_error(Exception("1011 You exceeded your current quota, please check your plan"))
    assert "key" in brain_error(Exception("API key not valid. Please pass a valid API key."))
    assert brain_error(Exception("1006 connection closed abnormally")) == ""


def test_no_hang_up_before_caller_speaks(tmp_path, monkeypatch):
    import asyncio
    from callme import bridge
    monkeypatch.setenv("CALLME_HOME", str(tmp_path))
    s = config.Settings(gemini_api_key="x", tg_api_id=1, tg_api_hash="x", owner="1", data_dir=tmp_path)
    b = bridge.CallBridge(None, 1, s, "test")

    async def go():
        r1 = await b.run_tool("hang_up_call", {})
        r2 = await b.run_tool("hang_up_call", {})
        b.caller_turns = 1
        r3 = await b.run_tool("hang_up_call", {})
        return r1, r2, r3
    r1, r2, r3 = asyncio.run(go())
    assert "not" in r1.lower() and "not" in r2.lower() and r3 == "Hanging up."


def _service_for_after_call(tmp_path, language="en"):
    from callme.service import Service

    s = config.Settings(gemini_api_key="x", tg_api_id=1, tg_api_hash="x", owner="1", data_dir=tmp_path, language=language)
    svc = Service(s)
    sent = []

    async def send_owner(text):
        sent.append(text)
    svc.send_owner = send_owner
    return svc, sent


def _fake_genai(monkeypatch, replies):
    """generate_content raises each Exception in replies and returns the first string."""
    from google import genai

    calls = []

    class Models:
        def generate_content(self, **_):
            calls.append(1)
            item = replies[len(calls) - 1]
            if isinstance(item, Exception):
                raise item
            return type("R", (), {"text": item})()

    class Client:
        def __init__(self, **_):
            self.models = Models()
    monkeypatch.setattr(genai, "Client", Client)
    return calls


def test_failed_summary_still_sends_post_call_message(tmp_path, monkeypatch):
    """Real bug 2026-10-31: a 503 on the summary request meant no post-call message at all."""
    import asyncio

    from callme import service

    monkeypatch.setattr(service, "SUMMARY_RETRY_DELAY_S", 0)
    calls = _fake_genai(monkeypatch, [Exception("503"), Exception("503")])
    svc, sent = _service_for_after_call(tmp_path, "th")
    r = {"result": "connected", "seconds": 46, "notes": ["buy milk"], "lines": ["a", "b"]}
    asyncio.run(svc.after_call("test", r))
    assert len(calls) == 2  # tried once more before giving up
    assert len(sent) == 1 and "0:46" in sent[0] and "buy milk" in sent[0] and "สรุปไม่สำเร็จ" in sent[0]


def test_summary_retry_recovers(tmp_path, monkeypatch):
    import asyncio

    from callme import service

    monkeypatch.setattr(service, "SUMMARY_RETRY_DELAY_S", 0)
    calls = _fake_genai(monkeypatch, [Exception("503"), "- all good"])
    svc, sent = _service_for_after_call(tmp_path)
    asyncio.run(svc.after_call("test", {"result": "connected", "seconds": 5, "lines": ["a", "b"]}))
    assert len(calls) == 2 and "all good" in sent[0] and "failed" not in sent[0]


def test_short_call_needs_no_summary_message(tmp_path, monkeypatch):
    import asyncio

    calls = _fake_genai(monkeypatch, [])
    svc, sent = _service_for_after_call(tmp_path)
    asyncio.run(svc.after_call("test", {"result": "connected", "seconds": 2, "lines": ["a"]}))
    assert calls == [] and sent == []


def _run_call_with_fake_service(tmp_path, body):
    """Run `python -m callme call` for real in a child process, with Telegram replaced by a fake Service."""
    import subprocess
    import sys

    script = f"""
import sys, threading, time
from callme import service
{body}
service.Service.connect = connect
service.Service.place = place
service.Service.after_call = after_call
sys.argv = ["callme", "call", "test"]
from callme.__main__ import main
main()
"""
    env = dict(os.environ, CALLME_HOME=str(tmp_path), GEMINI_API_KEY="x", TG_API_ID="1", TG_API_HASH="x", OWNER="1")
    return subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=60,
                          cwd=str(Path(__file__).resolve().parent.parent))


FAKE_STUCK_LIBRARY = """
class Client:
    disconnected = False
    async def disconnect(self):
        print("disconnected", flush=True)
async def connect(self):
    self.client = Client()
    # like the PyTgCalls thread pool: a non-daemon thread nobody can stop
    threading.Thread(target=lambda: time.sleep(600), daemon=False).start()
async def after_call(self, reason, r):
    pass
"""


def test_call_exits_cleanly_after_hang_up(tmp_path):
    """Real bug 2026-10-31: `callme call` never exited after hanging up. The timeout fails this test if it hangs."""
    r = _run_call_with_fake_service(tmp_path, FAKE_STUCK_LIBRARY + """
async def place(self, reason):
    return {"result": "connected"}
""")
    assert r.returncode == 0, r.stderr
    assert "result: connected" in r.stdout and "disconnected" in r.stdout


def test_call_exit_code_when_not_connected_or_crashed(tmp_path):
    r = _run_call_with_fake_service(tmp_path, FAKE_STUCK_LIBRARY + """
async def place(self, reason):
    return {"result": "no_answer"}
""")
    assert r.returncode == 1 and "result: no_answer" in r.stdout and "disconnected" in r.stdout
    r = _run_call_with_fake_service(tmp_path, FAKE_STUCK_LIBRARY + """
async def place(self, reason):
    raise RuntimeError("boom")
""")
    assert r.returncode == 1 and "call failed: RuntimeError: boom" in r.stdout and "disconnected" in r.stdout
