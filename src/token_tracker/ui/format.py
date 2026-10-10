"""纯格式化与分组工具：宽度模式、token/成本/时长格式、agent/模型短名、卡片文本片段（品牌行 / 指标）。"""

from rich.text import Text

from .console import get_console
from .theme import _S

AGENT_SHORT = {"claude-code": "Claude", "codex": "Codex", "kimi": "Kimi"}
AGENT_LABEL = {"claude-code": "Claude Code", "codex": "Codex", "kimi": "Kimi Code"}

MODEL_SHORT = {
    "claude-fable-5-1": "Fable 5.1",
    "claude-fable-5": "Fable 5",
    "claude-mythos-5-1": "Mythos 5.1",
    "claude-mythos-5": "Mythos 5",
    "claude-opus-5-5": "Opus 5.5",
    "claude-opus-5": "Opus 5",
    "claude-opus-4-6": "Opus 4.6",
    "claude-opus-4-7": "Opus 4.7",
    "claude-opus-4-8": "Opus 4.8",
    "claude-sonnet-5": "Sonnet 5",
    "claude-sonnet-5-5": "Sonnet 5.5",
    "claude-sonnet-4-6": "Sonnet 4.6",
    "claude-sonnet": "Sonnet",
    "claude-haiku-4-5-20251001": "Haiku 4.5",
    "claude-haiku-5-5": "Haiku 5.5",
    "claude-haiku": "Haiku",
    "gpt-6-astra": "GPT-6 Astra",
    "gpt-6-sol": "GPT-6 Sol",
    "gpt-6.1-sol": "GPT-6.1 Sol",
    "gpt-6-luna": "GPT-6 Luna",
    # OpenAI GPT-5.6 系列（sol/terra/luna 三档）
    "gpt-5.6-sol": "GPT-5.6 Sol",
    "gpt-5.6-terra": "GPT-5.6 Terra",
    "gpt-5.6-luna": "GPT-5.6 Luna",
    # Kimi Code 会话 wire.jsonl 里的模型 id（usage.record.model，带 alias 前缀）
    "kimi-code/k3": "Kimi K3",
    "kimi-code/kimi-for-coding": "Kimi K2.7",
    "kimi-code/kimi-for-coding-highspeed": "Kimi K2.7 HS",
    # 国产模型短名（与 cost.py 内置定价 key 一一对应）
    "kimi-k3": "Kimi K3",
    "kimi-k2.7-code": "Kimi K2.7",
    "kimi-k2.6": "Kimi K2.6",
    "kimi-k2.5": "Kimi K2.5",
    "moonshot-v1-8k": "Moonshot 8k",
    "moonshot-v1-32k": "Moonshot 32k",
    "moonshot-v1-128k": "Moonshot 128k",
    "glm-4.6": "GLM-4.6",
    "glm-4.5": "GLM-4.5",
    "glm-4.5-air": "GLM-4.5 Air",
    "glm-4.7": "GLM-4.7",
    "glm-5": "GLM-5",
    "glm-5.1": "GLM-5.1",
    "glm-5.2": "GLM-5.2",
    "glm-5.3": "GLM-5.3",
    "glm-5.3-flash": "GLM-5.3 Flash",
    "glm-5.3-flashx": "GLM-5.3 FlashX",
    "qwen3.6-plus": "Qwen3.6 Plus",
    "qwen3.7-plus": "Qwen3.7 Plus",
    "qwen3-coder-plus": "Qwen3 Coder",
    "qwen3-coder-next": "Qwen3 Coder Next",
    "qwen3-coder-flash": "Qwen3 Coder Flash",
    "qwen-max": "Qwen Max",
    "qwen-plus": "Qwen Plus",
    "doubao-seed-1-6": "Doubao 1.6",
    "doubao-seed-code": "Doubao Code",
    "doubao-seed-2.0-code": "Doubao 2.0 Code",
    "doubao-seed-2.1-pro": "Doubao 2.1 Pro",
    "doubao-1-5-pro-32k": "Doubao Pro 32k",
    "doubao-1-5-pro-256k": "Doubao Pro 256k",
    "deepseek-flash": "DeepSeek V4.1F",
    "deepseek-v4-flash": "DeepSeek V4F",
    "deepseek-v4-pro": "DeepSeek V4P",
    "deepseek-v4-flash-vision-exp": "DeepSeek V4 Vision",
    "deepseek-chat": "DeepSeek Chat",
    "deepseek-reasoner": "DeepSeek Rsnr",
    "MiniMax-M2": "MiniMax M2",
    "MiniMax-M2.1": "MiniMax M2.1",
    "MiniMax-M2.1-highspeed": "MiniMax M2.1 HS",
    "MiniMax-M2.5": "MiniMax M2.5",
    "MiniMax-M2.5-highspeed": "MiniMax M2.5 HS",
    "MiniMax-M2.7": "MiniMax M2.7",
    "MiniMax-M2.7-highspeed": "MiniMax M2.7 HS",
    "MiniMax-M3": "MiniMax M3",
    "mimo-v2.5-pro": "MiMo V2.5P",
    "mimo-v2.5": "MiMo V2.5",
    "mimo-v2.6-pro": "MiMo V2.6P",
    "mimo-v2.6-flash": "MiMo V2.6F",
    # Gemini（3.6 / 3.8 Flash 同时有内置离线价，其余沿用在线表）
    "gemini-2.5-pro": "Gemini 2.5 Pro",
    "gemini-2.5-flash": "Gemini 2.5 Flash",
    "gemini-3-pro-preview": "Gemini 3 Pro",
    "gemini-3-pro": "Gemini 3 Pro",
    "gemini-3-flash-preview": "Gemini 3 Flash",
    "gemini-3.1-pro-preview": "Gemini 3.1 Pro",
    "gemini-3.5-flash": "Gemini 3.5 Flash",
    "gemini-3.6-flash": "Gemini 3.6 Flash",
    "gemini-3.7-pro": "Gemini 3.7 Pro",
    "gemini-3.8-flash": "Gemini 3.8 Flash",
    "gemini-2.0-flash": "Gemini 2.0 Flash",
    # xAI Grok
    "grok-4.3": "Grok 4.3",
    "grok-4.5": "Grok 4.5",
    "grok-4.6": "Grok 4.6",
    "grok-4.7": "Grok 4.7",
    "grok-build-0.1": "Grok Build",
    "grok-code-fast-1": "Grok Code",
}


def _width_mode() -> str:
    w = get_console().width
    if w < 100:
        return "compact"
    if w < 120:
        return "medium"
    return "wide"


def _model_short(model: str) -> str:
    if model in MODEL_SHORT:
        return MODEL_SHORT[model]
    if "/" in model:
        return model.split("/")[-1][:16]
    return model[:16]


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _fmt_cost(usd: float) -> str:
    if usd >= 100:
        return f"${usd:.0f}"
    if usd >= 1:
        return f"${usd:.2f}"
    if usd > 0:
        return f"${usd:.3f}"
    return "$0"


def _display_width(s: str) -> int:
    w = 0
    for ch in s:
        w += 2 if ord(ch) > 0x7F else 1
    return w


def _project_short(project: str) -> str:
    return project if project else "unknown"


def brand_line(agents: list[str]) -> Text:
    """品牌行：Token Tracker（红）+ agents（暗红，` + ` 连接）。daily / weekly 卡片共用。"""
    line = Text()
    line.append("Token Tracker", style=f"bold {_S.red}")
    line.append(": ", style=f"bold {_S.red}")
    for i, a in enumerate(agents):
        if i:
            line.append(" + ", style=f"dim {_S.red}")
        line.append(a, style=f"dim {_S.red}")
    return line


def append_metric(body: Text, label: str, value: str, color: str,
                  cur: float | None = None, prev: float | None = None) -> None:
    """往 body 追加「label: value」（label 常规、value 加粗）；传 cur/prev 且 prev>0 时再追加环比（dim）。"""
    body.append(f"{label}: ", style=color)
    body.append(value, style=f"bold {color}")
    if cur is not None and prev and prev > 0:
        pct = (cur - prev) / prev * 100
        body.append(f" ({'↑' if pct >= 0 else '↓'}{abs(pct):.0f}%)", style=f"dim {color}")


def emit_metrics(body: Text, metrics: list[tuple[str, str]], color: str, avail: int) -> None:
    """把 (label, value) 列表追加到 body：字段间 3 空格分隔，按可用列宽 avail 贪心折行
    （窄终端下不溢出卡片、自动换到下一行）；不加行尾换行，行间换行由调用方控制。"""
    sep = "   "
    line_w = 0
    for i, (label, value) in enumerate(metrics):
        w = _display_width(f"{label}: {value}")
        if i and line_w + len(sep) + w > avail:
            body.append("\n")
            line_w = 0
        elif i:
            body.append(sep, style=_S.dim)
            line_w += len(sep)
        append_metric(body, label, value, color)
        line_w += w
