"""Session title from the first query, without calling the LLM."""

from app.agent.validator import PART_NUMBER

MAX_TITLE = 60


def make_title(query: str) -> str:
    if found := PART_NUMBER.search(query):
        return found.group().upper()
    text = " ".join(query.split())
    return text if len(text) <= MAX_TITLE else text[: MAX_TITLE - 1].rstrip() + "…"
