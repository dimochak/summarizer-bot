from langchain_core.globals import set_debug
from src.core.llm import get_structured_llm
from src.panbot.prompts.factory import get_chat_prompt, get_reply_decision_prompt
from src.panbot.history.manager import ChatContextHistory
from src.panbot.models import BotResponse, ReplyDecision
from src.tools.config import log
from src.tools.db import db_call

class PanBotEngine:
    def __init__(self, debug: bool = False):
        self.prompt = get_chat_prompt()
        if debug:
            set_debug(True)

    async def generate_response(self, message, quoted_block: str, traits_block: str, user_name: str, user_message: str, custom_role: str = None, is_creator: bool = False, facts_block: str = ""):
        chat_id = message.chat.id
        reply_to_id = message.reply_to_message.message_id if message.reply_to_message else None
        llm = get_structured_llm(BotResponse, chat_id=chat_id, purpose="chat")

        chain = self.prompt | llm

        # Історію підставляємо самі, без RunnableWithMessageHistory. Той шар
        # існував лише заради цієї підстановки: зворотний бік — дозапис
        # відповіді в історію — у нас порожній, бо історія щоразу
        # перезбирається з БД. Натомість він на КОЖНІЙ відповіді кидав
        # ValueError у логи, намагаючись перетворити BotResponse на повідомлення.
        history = ChatContextHistory(
            chat_id=chat_id,
            current_message_id=message.message_id,
            reply_to_id=reply_to_id,
        )
        # .messages — синхронна property, що ходить у БД: у потік її, щоб не
        # блокувати event loop (раніше це робив за нас LangChain).
        history_messages = await db_call(lambda: history.messages)

        input_data = {
            "history": history_messages,
            "quoted_block": quoted_block,
            "traits_block": traits_block,
            "user_name": user_name,
            "user_message": user_message,
            "custom_role": custom_role or "",
            "is_creator": is_creator,
            "facts_block": facts_block,
        }
        
        log.info(
            f"Invoking PanBotEngine for chat {chat_id}: "
            f"custom_role='{custom_role}', history={len(history_messages)} msgs, "
            f"facts={'yes' if facts_block else 'no'}"
        )

        result = await chain.ainvoke(input_data)

        log.info(f"PanBotEngine result: {result.response}")
        return result.response

    async def should_reply_by_agent(
        self,
        message,
        user_message: str,
        reply_to_text: str | None,
        is_reply_to_bot: bool,
        has_trigger: bool,
    ) -> bool:
        chat_id = message.chat.id
        llm = get_structured_llm(ReplyDecision, chat_id=chat_id, purpose="decision")
        prompt = get_reply_decision_prompt().format(
            user_message=user_message,
            is_reply_to_bot=is_reply_to_bot,
            has_trigger=has_trigger,
            reply_to_text=reply_to_text or "",
        )

        result = await llm.ainvoke(prompt)
        return bool(result.reply)
