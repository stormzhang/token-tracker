from . import agy, claude, codex, kimi
from .types import AgentInfo


def detect_agents() -> list[AgentInfo]:
    agents: list[AgentInfo] = []
    for detector in [claude.detect, codex.detect, kimi.detect, agy.detect]:
        info = detector()
        if info:
            agents.append(info)
    return agents
