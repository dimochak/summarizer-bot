"""Інструменти агента.

Інструменти будуються під конкретний чат: `chat_id` замикається всередині,
а не приходить аргументом від моделі. Інакше модель могла б підставити чужий
chat_id і витягти історію сусіднього чату.
"""
from langchain_core.tools import BaseTool

from src.agent.tools.chat import build_chat_tools
from src.agent.tools.weather import get_weather


def build_tools(chat_id: int) -> list[BaseTool]:
    return [*build_chat_tools(chat_id), get_weather]


__all__ = ["build_tools"]
