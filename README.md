# Mail Agent

Daily email-to-message summary agent that turns your inbox into a concise daily brief.

## What it does
- Connects to your mailbox via IMAP and fetches recent emails.
- Classifies messages into **Important**, **Job Alerts**, and **Other**.
- Optionally uses an LLM summary for a polished daily digest.
- Delivers the summary through **WhatsApp** or **Telegram**.

## Setup
1. Enable IMAP for your mailbox.
2. For Gmail, create an App Password and use it as `MAIL_IMAP_PASSWORD`.
3. Choose your delivery channel:
   - `whatsapp` — requires Twilio WhatsApp credentials
   - `telegram` — requires Telegram bot token and chat id
   - `both`
4. Create a `.env` file based on `.env.example`.
5. Install the macOS launchd agent (see below).

## macOS auto-start setup

This repo includes `setup/com.govardhan.mail-agent.daily.plist`, which runs the agent
once at your configured `DAILY_TIME` and also runs once immediately when you log in
or restart the Mac — so it catches up if the Mac was off during the scheduled time.

Install it:
```bash
cp setup/com.govardhan.mail-agent.daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.govardhan.mail-agent.daily.plist
```

Notes:
- Set `DAILY_TIME` in `.env` (default `09:00`).
- macOS privacy may ask for full-disk access the first time `launchd` runs the script.
  Grant it in System Settings → Privacy & Security → Full Disk Access.
- Logs: `/tmp/mail-agent.out.log` and `/tmp/mail-agent.err.log`.
- State is stored in `.mail_agent_state.json` and is **not** committed to the repo.

## Commands
```bash
# Run one check immediately
python3 mail_agent.py --once

# Check whether required settings are available
python3 mail_agent.py --check-config
```

## Notes
- Runtime state is stored in `.mail_agent_state.json` and is not committed to the repository.
- Do not paste real secrets into shared files; use `.env` locally and `.env.example` for placeholders.
