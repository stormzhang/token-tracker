from . import claude, codex, kimi, pi
from .types import AgentInfo


def detect_agents() -> list[AgentInfo]:
    agents: list[AgentInfo] = []
    for detector in [claude.detect, codex.detect, kimi.detect, pi.detect]:
        info = detector()
        if info:
            agents.append(info)
    return agents
