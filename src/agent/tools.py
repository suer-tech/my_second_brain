import os
import asyncio
from langchain_core.tools import tool
from src.agent.security import review_bash_command

# Разрешаем агенту работать в директории выше (на уровне папки с проектами), если потребуется
# Но по умолчанию его корень — это корень агента
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


@tool
async def execute_bash_command(command: str) -> str:
    """Executes a bash/powershell command on the host system. Use this to run npm, python, git, or other CLI tools. Returns stdout and stderr."""
    # 1. Проверка безопасности (Security Agent Interceptor).
    # review_bash_command асинхронна: внутри может звать LLM и веб-поиск
    # через asyncio.to_thread, не блокируя event loop (фикс №8).
    is_safe, reason = await review_bash_command(command)
    if not is_safe:
        return f"SECURITY AGENT BLOCKED COMMAND: {reason}\nWARNING: Do not attempt to run this command again."

    try:
        # Асинхронный subprocess вместо блокирующего subprocess.run (фикс №8).
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=ROOT_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=120
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return "Error: command timed out after 120 seconds."
        output = stdout_bytes.decode("utf-8", errors="ignore")
        stderr = stderr_bytes.decode("utf-8", errors="ignore")
        if stderr:
            output += f"\nSTDERR:\n{stderr}"
        return output if output else "Command executed successfully with no output."
    except Exception as e:
        return f"Error executing command: {str(e)}"


@tool
async def read_file(path: str) -> str:
    """Reads the contents of a file. Provide the path relative to the project root, or an absolute path."""
    target_path = path if os.path.isabs(path) else os.path.join(ROOT_DIR, path)
    if not os.path.exists(target_path):
        return f"Error: File {target_path} does not exist."
    try:
        with open(target_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Error reading file: {str(e)}"


@tool
async def write_file(path: str, content: str) -> str:
    """Writes content to a file. Overwrites if exists, creates if it doesn't. Will create directories if needed."""
    target_path = path if os.path.isabs(path) else os.path.join(ROOT_DIR, path)
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    try:
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully wrote to {target_path}"
    except Exception as e:
        return f"Error writing file: {str(e)}"


@tool
async def list_directory(path: str = ".") -> str:
    """Lists the contents of a directory. Path is relative to project root. Returns files and folders."""
    target_path = path if os.path.isabs(path) else os.path.join(ROOT_DIR, path)
    if not os.path.exists(target_path):
        return f"Error: Directory {target_path} does not exist."
    try:
        items = os.listdir(target_path)
        return "\n".join(items) if items else "Directory is empty."
    except Exception as e:
        return f"Error listing directory: {str(e)}"


# Экспортируем список для LangGraph
developer_tools = [execute_bash_command, read_file, write_file, list_directory]
