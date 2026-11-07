"""Creates the AI chat model from settings."""

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from shared.config import Settings


def get_chat_model(settings: Settings) -> BaseChatModel:
    if not settings.llm_model:
        raise ValueError("LLM_MODEL is not set; add it to backend/.env")
    api_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
    return init_chat_model(
        settings.llm_model,
        model_provider=settings.llm_provider,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        base_url=settings.llm_base_url,
        api_key=api_key,
    )
