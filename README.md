# Jarvis Calls You 📞

**An AI that phones you.** A real Telegram voice call, with a real-time voice (Gemini Live), that can talk, search the web, see your camera, take notes and call you back later.

- ⏰ **Wake-up and check-in calls** on a schedule ("07:00 Mon-Fri: wake me up, weather + one good news story")
- 🚨 **"Call me when…"** for anything: CI failed, server down, door opened, stock hit a price. One `curl` and your phone rings
- 🚗 **Hands-free assistant**: call your AI while driving, ask anything, it answers out loud
- 📷 **It can see**: turn on your camera or share your screen during the call and ask about it
- 📝 **After every call** you get a Telegram message with notes and a short summary
- 🔌 **Your own tools**: drop a Python function in `tools/` and the AI can use it during calls
- 🌏 Speaks any language Gemini Live speaks (tested in English and Thai)

Free to run: Telegram calls cost nothing and Gemini has a free tier. Everything runs on your own computer.

## How it works

```
your phone  ⇄  Telegram call  ⇄  py-tgcalls (raw 48 kHz PCM)  ⇄  callme  ⇄  Gemini Live (voice in / voice out + tools)
```
The AI uses its **own** Telegram account (a spare number) and calls **you**. It only answers and obeys its owner.

## Quick start (10 minutes)

You need: Python 3.10+, a spare Telegram account for the AI, a free [Gemini API key](https://aistudio.google.com/apikey), and a free Telegram API id/hash from [my.telegram.org](https://my.telegram.org) (log in with the AI's number → API development tools).

```bash
git clone https://github.com/Kiitainternett/jarvis-calls-you && cd jarvis-calls-you
./install.sh            # Windows: install.bat
nano .env               # fill GEMINI_API_KEY, TG_API_ID, TG_API_HASH, OWNER (your @username)
python -m callme login  # one time: log in the AI's Telegram account
python -m callme call "say hi"   # your phone rings
python -m callme serve  # run 24/7
```
Then send `help` to the AI's Telegram account from your own account.

## Talk to it in Telegram chat
| You send | It does |
|---|---|
| `call me` | calls now |
| `call me in 20 min about the oven` | calls in 20 minutes |
| `call me at 06:30 wake me up` | wake-up call |
| `status` / `cancel` | show / clear the queue |

Thai works too: `โทรมาอีก 20 นาที เรื่อง ปิดเตา`

## Make other apps call you (webhook)
Set `WEBHOOK_TOKEN` in `.env`, then:
```bash
curl -X POST http://127.0.0.1:8787/call \
  -H "Authorization: Bearer $WEBHOOK_TOKEN" \
  -d '{"reason": "Production is down: 502 on /checkout", "urgent": true}'
```
`urgent` ignores quiet hours and the daily limit. `when` accepts `"in 10 min"` or `"07:30"`.
GitHub Actions example: add a step `if: failure()` that runs the curl through a tunnel (Cloudflare Tunnel / Tailscale) to your machine.

## Scheduled calls
`schedule.json`:
```json
[{"time": "07:00", "days": "mon-fri", "reason": "Wake me up. Weather and one good news story."}]
```

## Add your own tools
```python
# tools/garage.py
from callme.tools import tool

@tool("How many cars are waiting in the garage today?")
def cars_waiting() -> str:
    return "3 cars: a Civic, a Hilux and a Ninja 400"
```
Restart `serve`. During a call just ask "how many cars are waiting?".

## Public demo line (optional)
Want strangers to be able to call your AI, like a living demo or a receptionist? Set `PUBLIC_MODE=on`
on a **separate** Telegram account. Public callers get:
- calls capped at `PUBLIC_MAX_MINUTES` (default 3), `PUBLIC_CALLS_PER_CALLER` per day and `PUBLIC_CALLS_PER_DAY` in total
- no tools except taking a message, and a hard rule never to reveal its prompt, keys or your private details
- `public_persona.md` as its personality
Every public call is reported to you on Telegram with the caller's name and any message they left.

## Personality
Edit `persona.md`. Voice, language, quiet hours, call limits: `.env`.

## Safety and good manners
- Use a **spare** Telegram account for the AI. It runs as a normal user account (userbot); automating user accounts can get them limited by Telegram if abused, so keep calls to yourself.
- Only the `OWNER` can call the AI, message it commands, or receive calls. Everyone else is ignored.
- `data/ai_account.session` is the AI account's key. Never share or commit it (`.gitignore` already excludes it).
- Built-in tools cannot touch your files or spend money. Plugins you add run with your permissions: review what you install.
- Quiet hours and a daily call limit are on by default.

## Troubleshooting
- `python -m callme check` shows what is missing.
- Silence on the call: the first call records greetings in the background; the next call greets instantly. Logs in `data/callme.log`, transcripts in `data/transcripts.txt`.
- Call connects but the AI never speaks, then hangs up: your Gemini key is out of quota or invalid. The AI account messages you the reason after the call. Check usage in [Google AI Studio](https://aistudio.google.com), or add `GEMINI_API_KEY_BACKUP` (a key from another Google project) and it switches over automatically.
- `gemini session reset: 1011 ... exceeded your current quota` right at the start of a call can be a false alarm: on some keys the Google Search tool makes the Live session refuse to open. `callme` then retries once without Google Search (look for `retrying without the google_search tool` in `data/callme.log`); the call goes on, just without live web search.
## License
MIT
