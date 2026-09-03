import os

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_ollama import ChatOllama


def create_model() -> BaseChatModel:
    model_name = os.environ.get("OLLAMA_MODEL", "qwen3-coder:30b")
    model_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

    return ChatOllama(
        model=model_name,
        base_url=model_url,
        temperature=0.2
    )
