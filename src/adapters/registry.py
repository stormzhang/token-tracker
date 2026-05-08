from .types import AgentInfo
from . import claude


def detect_agents() -> list[AgentInfo]:
    agents: list[AgentInfo] = []
    info = claude.detect()
    if info:
        agents.append(info)
    return agents
