from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, START, END, MessagesState

from local_agent_chat.models import create_model


class ChatAgent:
    def __init__(self, model: BaseChatModel, checkpointer):
        self.model = model

        graph = StateGraph(MessagesState)

        graph.add_node("model", self._call_model)

        graph.add_edge(START, "model")
        graph.add_edge("model", END)

        self.graph = graph.compile(checkpointer=checkpointer)

    async def _call_model(self, state: MessagesState):
        response = await self.model.ainvoke(state["messages"])

        return {"messages": [response]}

    async def ainvoke(self, message: str, thread_id: str) -> str:
        response = await self.graph.ainvoke(
            {"messages": [HumanMessage(content=message)]},
            config={"configurable": {"thread_id": thread_id}},
        )

        return response["messages"][-1].content


def create_agent(checkpointer) -> ChatAgent:
    model = create_model()

    return ChatAgent(model=model, checkpointer=checkpointer)
