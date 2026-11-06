"""Builds the chat models and the single agent (LangChain v1 `create_agent`). Models come only
from settings; the agent is created once in the app lifespan and reused across requests."""

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ModelFallbackMiddleware,
    ToolCallLimitMiddleware,
)
from langchain_core.language_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph

from app.agent.context import AgentContext
from app.agent.fake import RuleBasedFakeModel
from app.agent.middleware import record_model_calls, record_tool_calls, session_prompt
from app.agent.tools import TOOLS
from app.config import Settings


def build_chat_model(settings: Settings, model_id: str | None) -> BaseChatModel:
    if settings.llm_provider == "fake":
        return RuleBasedFakeModel()
    if settings.llm_provider == "openrouter":
        from langchain_openrouter import ChatOpenRouter

        return ChatOpenRouter(
            model=model_id,
            api_key=settings.openrouter_api_key,
            temperature=0,
            max_tokens=settings.llm_max_tokens,
            timeout=settings.llm_timeout_s,
            max_retries=1,
        )
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(
        model=model_id,
        api_key=settings.google_api_key,
        temperature=0,
        max_tokens=settings.llm_max_tokens,
        timeout=settings.llm_timeout_s,
        retries=1,
    )


def build_agent(
    settings: Settings,
    model: BaseChatModel | None = None,
    fallback: BaseChatModel | None = None,
) -> CompiledStateGraph:
    """`model`/`fallback` override the configured ones (tests pass a ScriptedFakeModel)."""
    if model is None:
        model = build_chat_model(settings, settings.agent_model)
        if settings.fallback_model and settings.llm_provider != "fake":
            fallback = build_chat_model(settings, settings.fallback_model)

    # Order matters: record_model_calls wraps the fallback, so it sees whichever model answered.
    middleware: list[AgentMiddleware] = [
        session_prompt,
        record_model_calls,
        # 'error' on both limits: the alternatives append internal English text that would reach
        # the answer. The conversation service turns the error into a controlled response.
        ModelCallLimitMiddleware(run_limit=settings.model_call_limit, exit_behavior="error"),
        ToolCallLimitMiddleware(run_limit=settings.tool_call_limit, exit_behavior="error"),
        record_tool_calls,
    ]
    if fallback is not None:
        middleware.append(ModelFallbackMiddleware(fallback))
    return create_agent(
        model=model, tools=TOOLS, middleware=middleware, context_schema=AgentContext
    )
