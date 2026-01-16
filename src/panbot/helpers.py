import tiktoken
import re
from src.tools.db import is_bot_message, get_user_traits
import src.tools.config as config

try:
    _encoder = tiktoken.encoding_for_model(config.OPENAI_MODEL_NAME)
except KeyError:
    _encoder = tiktoken.get_encoding("cl100k_base")

def get_quoted_block(message) -> str:
    try:
        if getattr(message, "reply_to_message", None):
            quoted = message.quote.text if hasattr(message, "quote") and message.quote else ""
            if not quoted:
                quoted = message.reply_to_message.text or ""
            return f'\n\nЦитований фрагмент бота (додатковий контекст):\n"{quoted}"'
    except Exception:
        pass
    return ""

def get_traits_block(user_id) -> str:
    traits = get_user_traits(user_id) if user_id else None
    if not traits:
        return ""
    
    tone = traits.get("tone") or {}
    style = traits.get("style") or {}
    lang = traits.get("language") or {}
    topics = traits.get("topics") or []
    topics_txt = ", ".join(t.get("name") for t in topics if isinstance(t, dict) and t.get("name"))[:200]
    
    traits_line = (
        f'UserTraits: summary="{traits.get("summary", "")}", '
        f'topics="{topics_txt}", '
        f'tone(f={tone.get("friendliness", 0)}, s={tone.get("sarcasm", 0)}, tox={tone.get("toxicity", 0)}), '
        f'style(verb={style.get("verbosity", 0)}, emoji={style.get("emoji_usage", 0)}), '
        f'lang="{lang.get("primary", "mixed")}"'
    )
    return f"\n\n{traits_line}\nВраховуй ці риси користувача при формуванні відповіді."

def check_summary_request(text: str):
    # Regex to find N messages or K hours
    # Examples: "що відбулось за останні 100 повідомлень", "що було за 5 годин"
    text = text.lower()
    if "що відбулось" not in text and "що було" not in text and "підсумуй" not in text:
        return None
    
    # Try to find N messages
    m_match = re.search(r"(\d+)\s+повідомлень", text)
    if m_match:
        return {"type": "messages", "value": min(int(m_match.group(1)), 2000)}
        
    # Try to find K hours
    h_match = re.search(r"(\d+)\s+годин", text)
    if h_match:
        return {"type": "hours", "value": min(int(h_match.group(1)), 48)}
        
    return None

def should_reply(message):
    """Return True if bot should reply to the given message."""
    if not (message.text or message.caption):
        return False

    text = (message.text or message.caption).lower()

    if hasattr(message, 'reply_to_message') and message.reply_to_message:
        chat_id = message.chat.id if hasattr(message, 'chat') else None
        reply_to_message_id = message.reply_to_message.message_id

        if chat_id and is_bot_message(chat_id, reply_to_message_id):
            return True

    # Check for trigger words (initial contact)
    bot_triggers = ["ботяндра", "ботяндрік", "пан бот"]
    if any(trigger in text for trigger in bot_triggers):
        return True

    # Check for keywords like "прокоментуй", "що думаєш" when replying to ANY message
    if hasattr(message, 'reply_to_message') and message.reply_to_message:
        reply_triggers = ["прокоментуй", "що думаєш", "що скажеш", "твій коментар"]
        if any(trigger in text for trigger in reply_triggers):
            return True

    return False
