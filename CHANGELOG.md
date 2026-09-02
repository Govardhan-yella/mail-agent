# Changelog

- [initial] - Project checkpoint system enabled
- [2026-07-18] - Fix missing daily summaries:
  - Handle binary/unknown-encoded mail parts in `get_text_body` without crashing.
  - Correct launchd Python interpreter path in macOS plist.
- [unreleased] - Accuracy and quality upgrades:
  - Enrich LLM context with date, links, and longer snippet.
  - Classify important / job-alert via a single merged LLM call when available; keep keyword fallback.
  - Retry WhatsApp/Telegram sends with exponential backoff (3 attempts).
  - Prune stale seen_uids and cap at 500.
  - Structured logging to ~/Library/Logs/mail-agent.log.
  - Optional failure alert via SEND_FAILURE_ALERT=true (off by default).
  - pytest suite for send retry logic.
