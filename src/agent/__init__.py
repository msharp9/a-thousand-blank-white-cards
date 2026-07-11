"""agent — single tool-calling AI agent for card generation and judging."""

from agent.contract import InterpretResult
from agent.runtime import run_agent

__all__ = ["InterpretResult", "run_agent"]
