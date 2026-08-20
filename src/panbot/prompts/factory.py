from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder, PromptTemplate
from langchain_core.prompts.chat import SystemMessagePromptTemplate, HumanMessagePromptTemplate

def get_chat_prompt():
    return ChatPromptTemplate.from_messages([
        SystemMessagePromptTemplate.from_template_file(
            "src/panbot/templates/system.j2", 
            input_variables=["quoted_block", "traits_block", "custom_role", "is_creator", "facts_block"], 
            template_format="jinja2"
        ),
        MessagesPlaceholder(variable_name="history"),
        HumanMessagePromptTemplate.from_template_file(
            "src/panbot/templates/human.j2", 
            input_variables=["user_name", "user_message"], 
            template_format="jinja2"
        ),
    ])

def get_reply_decision_prompt():
    template = Path("src/panbot/templates/reply_decision.j2").read_text(encoding="utf-8")
    return PromptTemplate.from_template(
        template,
        template_format="jinja2",
    )
