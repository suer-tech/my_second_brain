import re
from duckduckgo_search import DDGS
from langchain_core.messages import HumanMessage, SystemMessage
from src.agent.llm_router import get_flash_llm

def is_dangerous_command(command: str) -> bool:
    """Checks if a command contains potentially dangerous triggers like sudo, apt, npm, pip."""
    triggers = ["sudo", "apt", "apt-get", "npm install", "pip install", "rm -rf", "wget", "curl"]
    cmd_lower = command.lower()
    for t in triggers:
        if t in cmd_lower:
            return True
    return False

def extract_packages(command: str) -> list[str]:
    """Extremely simplified heuristic to extract package names from common install commands."""
    packages = []
    # match npm install <pkg>
    npm_match = re.search(r'npm\s+i(?:nstall)?\s+([a-zA-Z0-9_\-\.@]+)', command)
    if npm_match:
        packages.append(npm_match.group(1))
    
    # match apt install <pkg>
    apt_match = re.search(r'apt(?:-get)?\s+install\s+(?:-y\s+)?([a-zA-Z0-9_\-\.]+)', command)
    if apt_match:
        packages.append(apt_match.group(1))
        
    # match pip install <pkg>
    pip_match = re.search(r'pip\s+install\s+([a-zA-Z0-9_\-\.]+)', command)
    if pip_match:
        packages.append(pip_match.group(1))
        
    return packages

def search_web_for_package(package: str) -> str:
    """Searches DuckDuckGo for the package to see if it's known as malware."""
    query = f"'{package}' package malware phishing typosquatting"
    results = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=3):
                results.append(f"Title: {r.get('title')}\nSnippet: {r.get('body')}")
        return "\n\n".join(results) if results else "No suspicious reports found on the web."
    except Exception as e:
        return f"Web search failed: {str(e)}"

def review_bash_command(command: str) -> tuple[bool, str]:
    """
    Acts as a Security Supervisor.
    Returns (True, "") if allowed.
    Returns (False, "reason") if blocked.
    """
    if not is_dangerous_command(command):
        return True, ""
        
    packages = extract_packages(command)
    search_context = ""
    if packages:
        for pkg in packages:
            search_context += f"Web Search Results for package '{pkg}':\n"
            search_context += search_web_for_package(pkg) + "\n\n"
            
    llm = get_flash_llm()
    sys_msg = SystemMessage(content=(
        "You are a strict Linux Security Supervisor Agent. Your job is to review a bash command "
        "requested by an autonomous AI developer and decide if it is safe to execute on the host VPS.\n"
        "RULES:\n"
        "1. Block 'rm -rf /' or anything targeting root filesystem.\n"
        "2. If it is installing a package (npm, pip, apt), check the provided Web Search Results for ANY signs of typosquatting, malware, or phishing.\n"
        "3. If the package looks like a known malicious package, BLOCK IT.\n"
        "4. If the command is generally safe (e.g. normal npm install express, or systemctl restart), ALLOW IT.\n\n"
        "Output EXACTLY one word on the first line: 'ALLOW' or 'DENY'.\n"
        "If 'DENY', output the reason on the second line."
    ))
    
    user_prompt = f"Command to review:\n{command}\n\nContext:\n{search_context}"
    user_msg = HumanMessage(content=user_prompt)
    
    response = llm.invoke([sys_msg, user_msg]).content.strip()
    
    lines = response.split('\n', 1)
    decision = lines[0].strip().upper()
    reason = lines[1].strip() if len(lines) > 1 else "Blocked by security rules."
    
    if "DENY" in decision:
        return False, reason
    return True, ""
