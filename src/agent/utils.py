import os
import glob
import trafilatura
from urllib.parse import urlparse
import uuid

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
WIKI_DIR = os.path.join(BASE_DIR, "wiki")
RAW_DIR = os.path.join(BASE_DIR, "raw")

def read_user_profile() -> str:
    """Reads the user knowledge map for personalization."""
    profile_path = os.path.join(WIKI_DIR, "user_knowledge_map.md")
    if os.path.exists(profile_path):
        with open(profile_path, "r", encoding="utf-8") as f:
            return f.read()
    return "User profile not found."

def update_user_profile(new_goals: str) -> None:
    """Appends new goals, intentions, and experience to the user knowledge map."""
    profile_path = os.path.join(WIKI_DIR, "user_knowledge_map.md")
    if os.path.exists(profile_path):
        with open(profile_path, "a", encoding="utf-8") as f:
            f.write(f"\n## Дополнения к профилю (Опыт и Цели)\n{new_goals}\n")

def read_all_wiki() -> str:
    """Reads all markdown files in wiki directory as context for Q&A."""
    content = []
    for filepath in glob.glob(os.path.join(WIKI_DIR, "*.md")):
        # Skip user profile to avoid duplicating context if handled separately
        if "user_knowledge_map.md" in filepath:
            continue
        with open(filepath, "r", encoding="utf-8") as f:
            content.append(f"--- File: {os.path.basename(filepath)} ---\n{f.read()}")
    return "\n\n".join(content)

def fetch_url_text(url: str) -> str:
    """Fetches text from a URL using trafilatura."""
    downloaded = trafilatura.fetch_url(url)
    if downloaded:
        text = trafilatura.extract(downloaded)
        return text if text else "Could not extract text from URL."
    return "Could not download URL."

def is_url(text: str) -> bool:
    try:
        result = urlparse(text)
        return all([result.scheme, result.netloc])
    except ValueError:
        return False

def save_raw_file(content: str, is_url_content: bool = False) -> str:
    """Saves raw content to the raw/ directory."""
    filename = f"{uuid.uuid4().hex}.txt"
    filepath = os.path.join(RAW_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return filepath

def save_wiki_file(title: str, content: str) -> str:
    """Saves compiled markdown to the wiki/ directory."""
    # Sanitize title for filename
    safe_title = "".join([c if c.isalnum() else "_" for c in title])
    filepath = os.path.join(WIKI_DIR, f"{safe_title}.md")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return filepath
