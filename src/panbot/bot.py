class SarcasmLimitExceeded(Exception):
    """Raised when the user exceeds their daily sarcasm quota."""
    pass

class PanBot:
    def __init__(self, daily_limit=3):
        self.daily_limit = daily_limit
        # Implement internal state in actual implementation

    def should_reply(self, message):
        """Return True if bot should reply to the given message."""
        raise NotImplementedError

    def save_message(self, message):
        """Save a message to the memory/context."""
        raise NotImplementedError

    def build_conversation_prompt(self, message):
        """
        Build a context prompt including previous thread messages
        (excluding the current message).
        """
        raise NotImplementedError

    def process_reply(self, message):
        """
        Process a reply command, increment count, check limits,
        possibly raise SarcasmLimitExceeded.
        """
        raise NotImplementedError

    def _reset_limits_today(self):
        """Resets daily quotas (for testing only)."""
        raise NotImplementedError

    def get_context_for_user(self, user_id):
        """Return the context/memory for that user."""
        raise NotImplementedError