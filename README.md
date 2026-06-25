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
5. Run once or start scheduled daily delivery using the script config in this folder.

## Commands
```bash
python3 mail_agent.py --once
python3 mail_agent.py
```

## Notes
- Runtime state is stored in `.mail_agent_state.json` and is not committed to the repository.
- Do not paste real secrets into shared files; use `.env` locally and `.env.example` for placeholders.
