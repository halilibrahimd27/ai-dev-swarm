"""Telegram bidirectional control plane (Phase 5).

Coexists with the Phase 0 ``tools/telegram.py`` send-only notifier.
The notifier still sends one-way alerts (project shipped, milestone
blocked, escalations); the bot here owns inbound commands +
inline keyboards + Haiku-driven intent parsing.

Polling mode only — no webhook, no port exposed.
"""

from aidevswarm.telegram.bot import TelegramBot
from aidevswarm.telegram.intent import HaikuIntentParser, IntentParseError

__all__ = ["HaikuIntentParser", "IntentParseError", "TelegramBot"]
