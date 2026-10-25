"""User documentation stays bilingual and its relative links resolve."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
LINK = re.compile(r"\]\(([^)\s]+)\)")
FENCE = re.compile(r"```.*?```", re.S)


def _user_docs() -> list[Path]:
    return [ROOT / "README.md", ROOT / "README.de.md", ROOT / "CONTRIBUTING.md",
            *sorted(DOCS.glob("en/*.md")), *sorted(DOCS.glob("de/*.md"))]


def test_every_user_doc_exists_in_english_and_german() -> None:
    english = {p.name for p in DOCS.glob("en/*.md")}
    german = {p.name for p in DOCS.glob("de/*.md")}
    assert english
    assert english == german


@pytest.mark.parametrize("doc", _user_docs(), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_relative_links_resolve(doc: Path) -> None:
    text = FENCE.sub("", doc.read_text(encoding="utf-8"))
    broken = [
        target for target in LINK.findall(text)
        if not re.match(r"[a-z]+:", target) and not target.startswith("#")
        and not (doc.parent / target.split("#")[0]).exists()
    ]
    assert broken == []
