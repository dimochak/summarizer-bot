import src.tools.config as config
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from src.panbot.models import BotResponse

class LLMEngine:
    def __init__(self):
        self.gemini_llm = None
        self.openai_llm = None
        
        if config.GEMINI_API_KEY:
            self.gemini_llm = ChatGoogleGenerativeAI(
                model=config.GEMINI_MODEL_NAME,
                api_key=config.GEMINI_API_KEY,
            )
            
        if config.OPENAI_API_KEY:
            self.openai_llm = ChatOpenAI(
                model=config.OPENAI_MODEL_NAME,
                api_key=config.OPENAI_API_KEY,
            )

    def get_llm(self, chat_id: int):
        provider = self._determine_provider(chat_id)
        llm = self.openai_llm if provider == "openai" else self.gemini_llm
        if not llm:
            # Fallback
            llm = self.gemini_llm or self.openai_llm
        return llm

    def _determine_provider(self, chat_id: int) -> str:
        if chat_id in config.OPENAI_CHAT_IDS:
            return "openai"
        elif chat_id in config.GEMINI_CHAT_IDS:
            return "gemini"
        return "gemini" if self.gemini_llm else "openai"

    def get_structured_llm(self, chat_id: int):
        llm = self.get_llm(chat_id)
        return llm.with_structured_output(BotResponse)
