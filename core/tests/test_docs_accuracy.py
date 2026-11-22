"""
Docs accuracy check (Phase 17): every file path and `path::name` reference in
README.md and docs/*.md must exist in the real code, and every relative
Markdown link must point to an existing file.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

BASE_DIR = Path(settings.BASE_DIR)
DOC_FILES = [BASE_DIR / "README.md", *sorted((BASE_DIR / "docs").glob("*.md"))]
CODE_REFERENCE = re.compile(r"`([\w./-]+\.(?:py|html|js|css|yml|toml|txt|md))(?:::([\w.]+))?`")
FOLDER_REFERENCE = re.compile(r"`((?:[\w-]+/)+)`")
MARKDOWN_LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")
# Names that are intentionally not files of this repo (git-ignored or spec names).
IGNORED = {"venv/", "docs/PROGRESS.md", "docs/CODE_MAP.md", "media/", "staticfiles/", ".github/"}
PROJECT_FOLDERS = {
    "accounts",
    "activity",
    "config",
    "core",
    "docs",
    "projects",
    "sprints",
    "static",
    "tasks",
    "templates",
}


def defines(path: Path, name: str) -> bool:
    """True if `name` (or Class.method) is defined in the file."""
    source = path.read_text(encoding="utf-8")
    last = name.split(".")[-1]
    pattern = rf"^\s*(def|class)\s+{re.escape(last)}\b|^{re.escape(last)}\s*[:=]"
    return re.search(pattern, source, re.MULTILINE) is not None


class DocsAccuracyTests(SimpleTestCase):
    def test_every_code_reference_in_docs_exists(self):
        """`path/file.py::function` and `path/file.ext` references point to real code."""
        problems = []
        for doc in DOC_FILES:
            text = doc.read_text(encoding="utf-8")
            for match in CODE_REFERENCE.finditer(text):
                relative, name = match.group(1), match.group(2)
                if relative in IGNORED:
                    continue
                path = BASE_DIR / relative
                if not path.is_file():
                    problems.append(f"{doc.name}: missing file {relative}")
                elif name and not defines(path, name):
                    problems.append(f"{doc.name}: {relative} has no {name}")
        self.assertEqual(problems, [])

    def test_every_folder_reference_exists(self):
        """`folder/` references point to real folders."""
        problems = [
            f"{doc.name}: missing folder {match.group(1)}"
            for doc in DOC_FILES
            for match in FOLDER_REFERENCE.finditer(doc.read_text(encoding="utf-8"))
            # Only repo paths; API URL fragments like `login/` are not folders.
            if match.group(1).split("/")[0] in PROJECT_FOLDERS
            and not (BASE_DIR / match.group(1)).is_dir()
        ]
        self.assertEqual(problems, [])

    def test_relative_markdown_links_work(self):
        """[text](relative/path) links resolve to existing files."""
        problems = []
        for doc in DOC_FILES:
            for match in MARKDOWN_LINK.finditer(doc.read_text(encoding="utf-8")):
                target = match.group(1)
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                if not (doc.parent / target).exists():
                    problems.append(f"{doc.name}: broken link {target}")
        self.assertEqual(problems, [])
