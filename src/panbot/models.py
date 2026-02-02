from pydantic import BaseModel, Field

class BotResponse(BaseModel):
    response: str = Field(description="The sarcastic or ironic response to the user. DO NOT ask questions or for more information.")
