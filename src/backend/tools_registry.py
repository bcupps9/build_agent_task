from typing import Any, Dict, Callable
import os
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

HAS_OPENAI = bool(os.environ.get("OPENAI_API_KEY"))  # <- exported flag

_openai_model = ChatOpenAI(model="gpt-4o-mini", temperature=0.2) if HAS_OPENAI else None

def tool_llm(prompt: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """LLM tool. Uses a dev stub when OPENAI_API_KEY isn't set."""
    if not HAS_OPENAI:
        upstream_keys = list((context.get("upstream") or {}).keys())
        return {
            "text": f"[DEV-LLM] {prompt}\n(upstream: {upstream_keys})",
            "meta": {"tool": "llm", "dev_stub": True}
        }
    sys = SystemMessage(content="You are a precise, terse expert assistant. Return only the answer.")
    user = HumanMessage(content=f"Task:\n{prompt}\n\nContext:\n{context}")
    out = _openai_model.invoke([sys, user])
    return {
        "text": out.content,
        "meta": {"tool": "llm", "dev_stub": False}
    }

def tool_echo(prompt: str, context: Dict[str, Any]) -> Dict[str, Any]:
    upstream_keys = list((context.get("upstream") or {}).keys())
    return {
        "text": f"[ECHO] {prompt}\n(upstream: {upstream_keys})",
        "meta": {"tool": "echo", "dev_stub": True}
    }

ToolRegistry: Dict[str, Callable[..., Dict[str, Any]]] = {
    "llm": tool_llm,
    "echo": tool_echo,
}
