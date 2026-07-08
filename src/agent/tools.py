import os
import asyncio
from langchain_core.tools import tool
from src.agent.security import review_bash_command
from src.agent.logger import log_tool_call, get_session_id, _now_ms as _log_now_ms

# Разрешаем агенту работать в директории выше (на уровне папки с проектами), если потребуется
# Но по умолчанию его корень — это корень агента
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


@tool
async def execute_bash_command(command: str) -> str:
    """Executes a bash/powershell command on the host system. Use this to run npm, python, git, or other CLI tools. Returns stdout and stderr."""
    t0 = _log_now_ms()
    session_id = get_session_id() or ""

    # 1. Проверка безопасности (Security Agent Interceptor).
    is_safe, reason = await review_bash_command(command)
    if not is_safe:
        result = f"SECURITY AGENT BLOCKED COMMAND: {reason}\nWARNING: Do not attempt to run this command again."
        if session_id:
            log_tool_call(
                session_id,
                "execute_bash_command",
                command[:200],
                "blocked",
                _log_now_ms() - t0,
                result_summary=result[:200],
            )
        return result

    try:
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
            result = "Error: command timed out after 120 seconds."
            if session_id:
                log_tool_call(
                    session_id,
                    "execute_bash_command",
                    command[:200],
                    "timeout",
                    _log_now_ms() - t0,
                    result_summary=result,
                )
            return result
        output = stdout_bytes.decode("utf-8", errors="ignore")
        stderr = stderr_bytes.decode("utf-8", errors="ignore")
        if stderr:
            output += f"\nSTDERR:\n{stderr}"
        result = output if output else "Command executed successfully with no output."
        if session_id:
            log_tool_call(
                session_id,
                "execute_bash_command",
                command[:200],
                "ok",
                _log_now_ms() - t0,
                result_summary=result[:200],
            )
        return result
    except Exception as e:
        result = f"Error executing command: {str(e)}"
        if session_id:
            log_tool_call(
                session_id,
                "execute_bash_command",
                command[:200],
                "error",
                _log_now_ms() - t0,
                result_summary=result[:200],
            )
        return result


@tool
async def read_file(path: str) -> str:
    """Reads the contents of a file. Provide the path relative to the project root, or an absolute path."""
    t0 = _log_now_ms()
    session_id = get_session_id() or ""
    target_path = path if os.path.isabs(path) else os.path.join(ROOT_DIR, path)
    if not os.path.exists(target_path):
        result = f"Error: File {target_path} does not exist."
        if session_id:
            log_tool_call(
                session_id,
                "read_file",
                path[:200],
                "error",
                _log_now_ms() - t0,
                result_summary=result[:200],
            )
        return result
    try:
        with open(target_path, "r", encoding="utf-8") as f:
            content = f.read()
        if session_id:
            log_tool_call(
                session_id,
                "read_file",
                path[:200],
                "ok",
                _log_now_ms() - t0,
                result_summary=f"len={len(content)}",
            )
        return content
    except Exception as e:
        result = f"Error reading file: {str(e)}"
        if session_id:
            log_tool_call(
                session_id,
                "read_file",
                path[:200],
                "error",
                _log_now_ms() - t0,
                result_summary=result[:200],
            )
        return result


@tool
async def write_file(path: str, content: str) -> str:
    """Writes content to a file. Overwrites if exists, creates if it doesn't. Will create directories if needed."""
    t0 = _log_now_ms()
    session_id = get_session_id() or ""
    target_path = path if os.path.isabs(path) else os.path.join(ROOT_DIR, path)
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    try:
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(content)
        result = f"Successfully wrote to {target_path}"
        if session_id:
            log_tool_call(
                session_id,
                "write_file",
                path[:200],
                "ok",
                _log_now_ms() - t0,
                result_summary=f"len={len(content)}",
            )
        return result
    except Exception as e:
        result = f"Error writing file: {str(e)}"
        if session_id:
            log_tool_call(
                session_id,
                "write_file",
                path[:200],
                "error",
                _log_now_ms() - t0,
                result_summary=result[:200],
            )
        return result


@tool
async def list_directory(path: str = ".") -> str:
    """Lists the contents of a directory. Path is relative to project root. Returns files and folders."""
    t0 = _log_now_ms()
    session_id = get_session_id() or ""
    target_path = path if os.path.isabs(path) else os.path.join(ROOT_DIR, path)
    if not os.path.exists(target_path):
        result = f"Error: Directory {target_path} does not exist."
        if session_id:
            log_tool_call(
                session_id,
                "list_directory",
                path[:200],
                "error",
                _log_now_ms() - t0,
                result_summary=result[:200],
            )
        return result
    try:
        items = os.listdir(target_path)
        result = "\n".join(items) if items else "Directory is empty."
        if session_id:
            log_tool_call(
                session_id,
                "list_directory",
                path[:200],
                "ok",
                _log_now_ms() - t0,
                result_summary=f"items={len(items)}",
            )
        return result
    except Exception as e:
        result = f"Error listing directory: {str(e)}"
        if session_id:
            log_tool_call(
                session_id,
                "list_directory",
                path[:200],
                "error",
                _log_now_ms() - t0,
                result_summary=result[:200],
            )
        return result


# Экспортируем список для LangGraph
developer_tools = [execute_bash_command, read_file, write_file, list_directory]

# Read-only tools для Q&A-ветки: прямой запуск/редактирование кода запрещён,
# но агент может читать файлы и листать директории для контекста.
qa_readonly_tools = [read_file, list_directory]
