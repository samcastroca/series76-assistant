"""Rebuilds the model's view of earlier turns from stored messages: plain user and assistant text.
Tool results are not replayed; the session prompt lists the part numbers shown so far and the
agent calls the tool again when it needs a value (docs/adr/0004)."""

from collections.abc import Iterable

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.db.models import Message


def to_messages(rows: Iterable[Message]) -> list[BaseMessage]:
    return [
        HumanMessage(row.content) if row.role == "user" else AIMessage(row.content) for row in rows
    ]


def update_parts(state: dict, products: list[str]) -> dict:
    """New session state: products shown this turn become active; all shown parts accumulate."""
    shown = list(dict.fromkeys([*state.get("shown_parts", []), *products]))
    active = products or state.get("active_parts", [])
    return {**state, "active_parts": active, "shown_parts": shown}
