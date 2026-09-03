from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage

from local_agent_chat.models import create_model


class ChatAgent:
    def __init__(self, model: BaseChatModel):
        self.model = model

    async def ainvoke(self, message: str) -> str:
        response = await self.model.ainvoke(
            [HumanMessage(content=message)]
        )

        return response.content


def create_agent() -> ChatAgent:
    model = create_model()

    return ChatAgent(model=model)
