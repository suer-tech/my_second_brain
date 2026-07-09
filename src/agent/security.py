import re
import asyncio
from duckduckgo_search import DDGS
from langchain_core.messages import HumanMessage, SystemMessage
from src.agent.llm_router import get_flash_llm
from src.agent.prompt_loader import load_prompt


def is_dangerous_command(command: str) -> bool:
    """Checks if a command contains potentially dangerous triggers like sudo, apt, npm, pip."""
    triggers = [
        "sudo",
        "apt",
        "apt-get",
        "npm install",
        "pip install",
        "rm -rf",
        "wget",
        "curl",
    ]
    cmd_lower = command.lower()
    for t in triggers:
        if t in cmd_lower:
            return True
    return False


def extract_packages(command: str) -> list[str]:
    """Extremely simplified heuristic to extract package names from common install commands."""
    packages = []
    # match npm install <pkg>
    npm_match = re.search(r"npm\s+i(?:nstall)?\s+([a-zA-Z0-9_\-\.@]+)", command)
    if npm_match:
        packages.append(npm_match.group(1))

    # match apt install <pkg>
    apt_match = re.search(
        r"apt(?:-get)?\s+install\s+(?:-y\s+)?([a-zA-Z0-9_\-\.]+)", command
    )
    if apt_match:
        packages.append(apt_match.group(1))

    # match pip install <pkg>
    pip_match = re.search(r"pip\s+install\s+([a-zA-Z0-9_\-\.]+)", command)
    if pip_match:
        packages.append(pip_match.group(1))

    return packages


def _search_web_for_package_sync(package: str) -> str:
    """Sync-часть веб-поиска. Запускается через asyncio.to_thread (фикс №8),
    т.к. DDGS использует блокирующий HTTP (requests)."""
    query = f"'{package}' package malware phishing typosquatting"
    results = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=3):
                results.append(f"Title: {r.get('title')}\nSnippet: {r.get('body')}")
        return (
            "\n\n".join(results)
            if results
            else "No suspicious reports found on the web."
        )
    except Exception as e:
        return f"Web search failed: {str(e)}"


async def review_bash_command(command: str) -> tuple[bool, str]:
    """
    Acts as a Security Supervisor (асинхронная версия, фикс №8).
    Returns (True, "") if allowed.
    Returns (False, "reason") if blocked.
    """
    if not is_dangerous_command(command):
        return True, ""

    packages = extract_packages(command)
    # Запускаем веб-поиск по пакетам параллельно в потоках (фикс №8):
    # DDGS внутри использует блокирующий requests.
    if packages:
        search_results = await asyncio.gather(
            *(asyncio.to_thread(_search_web_for_package_sync, pkg) for pkg in packages)
        )
    else:
        search_results = []

    search_context = ""
    for pkg, result in zip(packages, search_results):
        search_context += f"Web Search Results for package '{pkg}':\n{result}\n\n"

    llm = get_flash_llm()
    sys_msg = SystemMessage(content=load_prompt("system/safety"))

    user_prompt = f"Command to review:\n{command}\n\nContext:\n{search_context}"
    user_msg = HumanMessage(content=user_prompt)

    raw_content = (await llm.ainvoke([sys_msg, user_msg])).content
    # content может быть str или list[str|dict] (multimodal) — приводим к str.
    response = str(raw_content).strip()

    lines = response.split("\n", 1)
    decision = lines[0].strip().upper()
    reason = lines[1].strip() if len(lines) > 1 else "Blocked by security rules."

    if "DENY" in decision:
        return False, reason
    return True, ""
