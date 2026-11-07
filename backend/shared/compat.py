"""Import shims for old libraries running against langchain 1.x / langchain-community 0.4."""

import importlib
import sys
import types


def _alias(name: str, target: str) -> None:
    if name not in sys.modules:
        sys.modules[name] = importlib.import_module(target)


def install_shims() -> None:
    # langfuse 2.x imports its LangChain handler's base classes from the pre-1.0 `langchain` paths.
    for pkg in ("langchain.callbacks", "langchain.schema"):
        sys.modules.setdefault(pkg, types.ModuleType(pkg))
    _alias("langchain.callbacks.base", "langchain_core.callbacks.base")
    _alias("langchain.schema.agent", "langchain_core.agents")
    _alias("langchain.schema.document", "langchain_core.documents")

    # ragas 0.3.1 imports ChatVertexAI, which langchain-community 0.4 removed; it is never used here.
    name = "langchain_community.chat_models.vertexai"
    if name not in sys.modules:
        stub = types.ModuleType(name)
        stub.ChatVertexAI = type("ChatVertexAI", (), {})  # type: ignore[attr-defined]
        sys.modules[name] = stub

    # ragas applies nest_asyncio, which breaks asyncio.wait_for on Python 3.14; we run no nested loops.
    import nest_asyncio

    nest_asyncio.apply = lambda *args, **kwargs: None
