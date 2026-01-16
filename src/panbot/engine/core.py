from langchain_core.runnables.history import RunnableWithMessageHistory
from src.panbot.engine.llm import LLMEngine
from src.panbot.models import BotResponse
from src.panbot.prompts.factory import get_chat_prompt
from src.panbot.history.manager import ChatContextHistory

class PanBotEngine:
    def __init__(self):
        self.llm_engine = LLMEngine()
        self.prompt = get_chat_prompt()

    async def generate_response(self, message, quoted_block: str, traits_block: str, user_name: str, user_message: str, custom_role: str = None):
        chat_id = message.chat.id
        reply_to_id = message.reply_to_message.message_id if message.reply_to_message else None
        llm = self.llm_engine.get_llm(chat_id)
        
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

        result = await runnable_with_history.ainvoke(
            {
                "quoted_block": quoted_block,
                "traits_block": traits_block,
                "user_name": user_name,
                "user_message": user_message,
                "custom_role": custom_role or "",
            },
            config={"configurable": {"session_id": str(chat_id)}},
        )

        try:
            content = result.content
            if isinstance(content, dict):
                return BotResponse(**content).response
            # TODO: analyze this condition a bit later
            import json

            clean_content = content.strip()
            if clean_content.startswith("```json"):
                clean_content = clean_content.replace("```json", "", 1).rstrip("` \n")
            elif clean_content.startswith("```"):
                clean_content = clean_content.replace("```", "", 1).rstrip("` \n")
            
            data = json.loads(clean_content)
            structured_result = BotResponse(**data)
            return structured_result.response
        except Exception:
            return result.content
