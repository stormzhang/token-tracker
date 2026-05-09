import os
import sys

LOW_THRESHOLD = 50
HIGH_THRESHOLD = 80
DEFAULT_WIDTH = 8

FILLED_CHAR = "━"
EMPTY_CHAR = "─"

ANSI_COLORS = {
    "green": "\033[32m",
    "yellow": "\033[33m",
    "red": "\033[31m",
    "reset": "\033[0m",
}


def use_color(color_opt: bool | None, stream=None) -> bool:
    if color_opt is not None:
        return color_opt
    if "NO_COLOR" in os.environ:
        return False
    stream = stream or sys.stdout
    return stream.isatty()


def progress_color(value: float) -> str:
    if value >= HIGH_THRESHOLD:
        return "red"
    if value >= LOW_THRESHOLD:
        return "yellow"
    return "green"


def render_progress(
    value: float | None,
    width: int = DEFAULT_WIDTH,
    color: bool = False,
) -> str:
    if value is None:
        return EMPTY_CHAR * width + " n/a"

    pct = max(0.0, min(100.0, value))
    filled = round(pct / 100 * width)
    bar = FILLED_CHAR * filled + EMPTY_CHAR * (width - filled)
    text = f"{bar} {pct:.0f}%"

    if not color:
        return text

    style = progress_color(pct)
    return f"{ANSI_COLORS[style]}{text}{ANSI_COLORS['reset']}"
