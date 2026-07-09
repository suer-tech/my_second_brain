import os
import asyncio
import re
import tempfile
from langchain_core.tools import tool
from src.agent.security import review_bash_command
from src.agent.logger import log_tool_call, get_session_id, _now_ms as _log_now_ms

# Разрешаем агенту работать в директории выше (на уровне папки с проектами), если потребуется
# Но по умолчанию его корень — это корень агента
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

# Директории и расширения, которые игнорируются при поиске
_SEARCH_IGNORE_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".env", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "logs", ".opencode",
}
_SEARCH_TEXT_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".md", ".txt", ".json",
    ".yaml", ".yml", ".toml", ".cfg", ".ini", ".conf",
    ".html", ".css", ".scss", ".sql", ".sh", ".bat", ".ps1",
    ".xml", ".svg", ".env.example", ".dockerfile", ".gitignore",
    ".go", ".rs", ".java", ".kt", ".swift", ".c", ".cpp", ".h", ".hpp",
}
_MAX_SEARCH_RESULTS = 50
_MAX_FILE_SIZE = 512 * 1024  # 512 KB


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
    # Фикс: проверка, что путь не является директорией.
    if os.path.isdir(target_path):
        result = f"Error: {target_path} is a directory, not a file. Use list_directory to list its contents."
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
    # Фикс: проверка, что не пытаемся писать в директорию.
    if os.path.isdir(target_path):
        result = f"Error: {target_path} is a directory, cannot write to it."
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
    # Фикс: если передан путь к файлу, а не к директории — корректно сообщаем.
    if os.path.isfile(target_path):
        result = f"Error: {target_path} is a file, not a directory."
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


@tool
async def search_content(pattern: str, path: str = ".", include: str = "") -> str:
    """Поиск содержимого файлов по регулярному выражению. Возвращает файлы с номерами строк и контекстом.

    Args:
        pattern: регулярное выражение для поиска (Python regex).
        path: путь к директории для поиска (относительно корня проекта, по умолчанию корень).
        include: фильтр по расширениям, разделённых пробелами (например '.py .md .txt'). Пусто — все текстовые расширения.
    """
    t0 = _log_now_ms()
    session_id = get_session_id() or ""
    target_dir = path if os.path.isabs(path) else os.path.join(ROOT_DIR, path)

    if not os.path.exists(target_dir):
        result = f"Error: Directory {target_dir} does not exist."
        if session_id:
            log_tool_call(session_id, "search_content", f"pattern={pattern[:100]} {path[:100]}", "error", _log_now_ms() - t0, result_summary=result[:200])
        return result

    if not os.path.isdir(target_dir):
        result = f"Error: {target_dir} is not a directory."
        if session_id:
            log_tool_call(session_id, "search_content", f"pattern={pattern[:100]} {path[:100]}", "error", _log_now_ms() - t0, result_summary=result[:200])
        return result

    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        result = f"Error: invalid regex pattern — {e}"
        if session_id:
            log_tool_call(session_id, "search_content", f"pattern={pattern[:100]}", "error", _log_now_ms() - t0, result_summary=result[:200])
        return result

    allowed_exts: set[str] | None = None
    if include.strip():
        allowed_exts = {ext.strip().lower() if ext.startswith(".") else f".{ext.strip().lower()}" for ext in include.split()}

    matches: list[str] = []
    file_count = 0

    for root, dirs, files in os.walk(target_dir):
        dirs[:] = [d for d in dirs if d not in _SEARCH_IGNORE_DIRS]

        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if allowed_exts is not None and ext not in allowed_exts:
                continue
            if allowed_exts is None and ext not in _SEARCH_TEXT_EXTENSIONS:
                continue

            fpath = os.path.join(root, fname)
            if os.path.getsize(fpath) > _MAX_FILE_SIZE:
                continue

            file_count += 1
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    for line_no, line in enumerate(f, 1):
                        if compiled.search(line):
                            rel_path = os.path.relpath(fpath, ROOT_DIR)
                            matches.append(f"{rel_path}:{line_no}: {line.rstrip()[:200]}")
                            if len(matches) >= _MAX_SEARCH_RESULTS:
                                break
                    if len(matches) >= _MAX_SEARCH_RESULTS:
                        break
            except Exception:
                pass

    result_parts = [f"Найдено совпадений: {len(matches)}  (просканировано файлов: {file_count})"]
    if matches:
        result_parts.append("")
        result_parts.extend(matches)
    if len(matches) >= _MAX_SEARCH_RESULTS:
        result_parts.append(f"\n... показано {_MAX_SEARCH_RESULTS} результатов, возможно есть ещё.")

    result = "\n".join(result_parts)

    if session_id:
        log_tool_call(session_id, "search_content", f"pattern={pattern[:100]} {path[:100]}", "ok", _log_now_ms() - t0, result_summary=f"matches={len(matches)} files={file_count}")

    return result


@tool
async def apply_patch(diff: str) -> str:
    """Apply a unified diff patch to the codebase. Use this INSTEAD of write_file when modifying existing files.
    
    Generate a diff in unified format (like `git diff` output) describing ONLY the lines to change.
    The tool validates the patch against the current file state — if the context doesn't match,
    the patch is rejected, preventing accidental data loss.
    
    Args:
        diff: The unified diff content. Example:
            --- a/src/file.py
            +++ b/src/file.py
            @@ -10,3 +10,5 @@
             old line
            +new line
    """
    t0 = _log_now_ms()
    session_id = get_session_id() or ""

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".diff", delete=False, encoding="utf-8"
    ) as f:
        f.write(diff)
        patch_path = f.name

    try:
        proc = await asyncio.create_subprocess_shell(
            f'git apply --unidiff-zero "{patch_path}"',
            cwd=ROOT_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=30
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            result = "Error: patch apply timed out."
            if session_id:
                log_tool_call(session_id, "apply_patch", diff[:200], "timeout", _log_now_ms() - t0, result_summary=result)
            return result

        stderr = stderr_bytes.decode("utf-8", errors="ignore")
        if proc.returncode != 0:
            result = f"Patch rejected:\n{stderr}"
            if session_id:
                log_tool_call(session_id, "apply_patch", diff[:200], "rejected", _log_now_ms() - t0, result_summary=result[:200])
            return result

        result = "Patch applied successfully."
        if session_id:
            log_tool_call(session_id, "apply_patch", diff[:200], "ok", _log_now_ms() - t0, result_summary=result)
        return result
    except Exception as e:
        result = f"Error applying patch: {str(e)}"
        if session_id:
            log_tool_call(session_id, "apply_patch", diff[:200], "error", _log_now_ms() - t0, result_summary=result[:200])
        return result
    finally:
        try:
            os.unlink(patch_path)
        except OSError:
            pass


# Экспортируем список для LangGraph
developer_tools = [
    execute_bash_command,
    read_file,
    write_file,
    apply_patch,
    list_directory,
    search_content,
]

# Read-only tools для Q&A-ветки: прямой запуск/редактирование кода запрещён,
# но агент может читать файлы и листать директории для контекста.
qa_readonly_tools = [read_file, list_directory, search_content]
