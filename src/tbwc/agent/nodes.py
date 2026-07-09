"""tbwc.agent.nodes — LangGraph interpretation nodes.

Each node is a pure-ish function (InterpretState) -> dict (partial state update).
Later beads append more nodes (retrieve, classify, emit_ops, judge, …) to this file.
"""

from __future__ import annotations

from tbwc.agent.llm import get_chat_model
from tbwc.agent.prompts import INTERPRETER_SYSTEM
from tbwc.agent.state import InterpretState


def reason(state: InterpretState) -> dict:
    """Summarise the card's intent in one sentence to guide RAG retrieval.

    Reads: state["card_draft"]
    Writes: state["search_notes"] (a one-sentence intent summary string)
    """
    draft = state["card_draft"]
    llm = get_chat_model(temperature=0)
    messages = [
        {"role": "system", "content": INTERPRETER_SYSTEM},
        {
            "role": "human",
            "content": (
                f"Card title: {draft['title']}\n"
                f"Card description: {draft['description']}\n\n"
                "In ONE sentence, summarise what this card is intended to do "
                "so we can search for similar example cards."
            ),
        },
    ]
    response = llm.invoke(messages)
    return {"search_notes": response.content}
