import os

from rich.panel import Panel
from rich.syntax import Syntax

from ai_functions.utils import get_console


def get_websearch_tool():
    """Search environment variables for the API key of a supported websearch tool and return the corresponding tool."""
    # see if we have API keys for any of the websearch tools supported by strands_tools
    if os.environ.get("EXA_API_KEY"):
        from strands_tools import exa as websearch_tool
    elif os.environ.get("TAVILY_API_KEY"):
        from strands_tools import tavily as websearch_tool
    else:
        raise ValueError("You need to set an environment variable containing the API key"
                         "for exa (EXA_API_KEY) or tavily (TAVILY_API_KEY)")
    return websearch_tool


console = get_console()


def display(title: str, content: str, lang: str = "yaml"):
    """Display content in a rich panel with syntax highlighting."""
    syntax = Syntax(content, lang, theme="monokai", word_wrap=True)
    console.print(Panel(syntax, title=title, border_style="cyan", expand=True))

def wait_for_ltm_update(
    memory,
    max_wait: int = 180,
    poll_interval: int = 10,
) -> bool:
    """Poll AgentCore until new LTM records appear after consolidation.

    Args:
        memory: An AgentCoreMemoryBackend instance.
        max_wait: Maximum seconds to wait before giving up.
        poll_interval: Seconds between polls.

    Returns:
        True if new LTM records were detected, False on timeout.
    """
    import time

    _, initial_ltm = memory.record_counts()

    elapsed = 0
    with get_console().status("Waiting for LTM consolidation..."):
        while elapsed < max_wait:
            time.sleep(poll_interval)
            elapsed += poll_interval
            _, current_ltm = memory.record_counts()
            if current_ltm > initial_ltm:
                display("LTM Update", f"New records detected after {elapsed}s ({initial_ltm} -> {current_ltm})")
                return True

    display("LTM Update", f"Timed out after {max_wait}s waiting for consolidation")
    return False
