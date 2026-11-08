"""Debug runner: stream the investor advisor graph node by node, without the HTTP server.

Usage (from backend/):
    uv run python debug_run.py "how is apple's risk factor?"
    uv run python debug_run.py "how is apple's share price?"

Prints each node's state update as it finishes, so routing (decide), retrieval and market
research can be inspected separately. The run is traced in Langfuse under the name "debug".
"""

import argparse

from langchain_core.messages import HumanMessage
from langfuse import Langfuse

from graphs.investor_advisor.investor_advisor import State, build_graph
from shared.compat import install_shims
from shared.config import get_settings

install_shims()  # langfuse's LangChain handler imports pre-1.0 langchain paths

from langfuse.callback import CallbackHandler  # needs install_shims() first

TRACE_NAME = "debug"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", help="question to ask the investor advisor")
    question: str = parser.parse_args().question

    get_settings()  # exports LANGFUSE_* from .env into os.environ before the handler is built
    handler = CallbackHandler(trace_name=TRACE_NAME, tags=[TRACE_NAME])
    try:
        for step in build_graph().stream(
            State(messages=[HumanMessage(question)]),
            config={"callbacks": [handler]},
            stream_mode="updates",
        ):
            for node, update in step.items():
                print(f"\n=== {node} ===")
                print(update)
    finally:
        # short-lived process: make sure the trace reaches Langfuse before exit
        handler.flush()
        Langfuse().flush()


if __name__ == "__main__":
    main()
