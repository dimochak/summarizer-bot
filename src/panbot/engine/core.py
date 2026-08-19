from langchain_core.globals import set_debug
from langchain_core.runnables.history import RunnableWithMessageHistory
from src.core.llm import get_structured_llm
from src.panbot.prompts.factory import get_chat_prompt, get_reply_decision_prompt
from src.panbot.history.manager import ChatContextHistory
from src.panbot.models import BotResponse, ReplyDecision
from src.tools.config import log

class PanBotEngine:
    def __init__(self, debug: bool = False):
        self.prompt = get_chat_prompt()
        if debug:
            set_debug(True)

    async def generate_response(self, message, quoted_block: str, traits_block: str, user_name: str, user_message: str, custom_role: str = None, is_creator: bool = False):
        chat_id = message.chat.id
        reply_to_id = message.reply_to_message.message_id if message.reply_to_message else None
        llm = get_structured_llm(BotResponse, chat_id=chat_id, purpose="chat")

        chain = self.prompt | llm
        
        runnable_with_history = RunnableWithMessageHistory(
            chain,
            lambda session_id: ChatContextHistory(
                chat_id=chat_id, 
                current_message_id=message.message_id,
                reply_to_id=reply_to_id
            ),
            input_messages_key="user_message",
            history_messages_key="history",
        )

        input_data = {
            "quoted_block": quoted_block,
            "traits_block": traits_block,
            "user_name": user_name,
            "user_message": user_message,
            "custom_role": custom_role or "",
            "is_creator": is_creator,
        }
        
        log.info(f"Invoking PanBotEngine with custom_role: '{custom_role}'")
        # To debug the full prompt, we can use the chain.invoke or format it
        try:
            formatted_prompt = self.prompt.format(**input_data, history=[])
            log.debug(f"Formatted prompt (without history): {formatted_prompt}")
        except Exception as e:
            log.warning(f"Could not format prompt for logging: {e}")

        result = await runnable_with_history.ainvoke(
            input_data,
            config={"configurable": {"session_id": str(chat_id)}},
        )

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
