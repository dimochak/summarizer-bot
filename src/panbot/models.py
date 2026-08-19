from pydantic import BaseModel, Field

class BotResponse(BaseModel):
    response: str = Field(description="The sarcastic or ironic response to the user. DO NOT ask questions or for more information.")


class ReplyDecision(BaseModel):
    reply: bool = Field(description="Whether the bot should reply to the message. Must be true or false.")
