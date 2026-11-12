from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from shared.config import get_settings


def get_chat_model(max_tokens: int | None = None) -> ChatOpenAI:
    """The single place that configures the chat LLM; every caller must use this."""
    cfg = get_settings()
    return ChatOpenAI(
        model_name=cfg.chat_model_name,
        openai_api_base=cfg.chat_model_endpoint,
        openai_api_key=cfg.chat_model_api_key,
        max_tokens=max_tokens,
        max_retries=6,
        request_timeout=120,
    )


def get_embedding_model() -> OpenAIEmbeddings:
    """The single place that configures the embedding model; every caller must use this."""
    cfg = get_settings()
    return OpenAIEmbeddings(
        model=cfg.embedding_model_name,
        openai_api_base=cfg.embedding_model_endpoint,
        openai_api_key=cfg.embedding_model_api_key,
        check_embedding_ctx_length=False,  # send raw strings; the service does the truncation checks
        max_retries=6,  # the embedding deployment's call-rate limit returns 429s during bulk ingestion
    )
