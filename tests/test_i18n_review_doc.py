"""docs/uz_review.md must list every unreviewed Uzbek string and be up to date."""

from __future__ import annotations

import json
from pathlib import Path

from core.i18n import review
from core.i18n.frames import all_frame_keys


def test_the_document_lists_every_unreviewed_frame_and_entry() -> None:
    text = review.build_markdown()
    for key in all_frame_keys():
        assert f"| `{key}` |" in text, key
    glossary = json.loads(review.GLOSSARY_FILE.read_text(encoding="utf-8"))["entries"]
    for key in glossary:
        assert f"| `{key}` |" in text, key
    backend = json.loads(review.CATALOGS[0][1].read_text(encoding="utf-8"))
    for key in backend:
        assert f"| `{key}` |" in text, key


def test_reviewed_texts_drop_out(tmp_path: Path) -> None:
    frames = {"frames": {"a.up.simple": {"reviewed": True, "headline": "x"},
                         "b.up.simple": {"reviewed": False, "headline": "y"}}}
    glossary = {"entries": {"k1": {"term": "t", "plain": "p", "reviewed": True},
                            "k2": {"term": "t2", "plain": "p2"}}}
    (tmp_path / "f.json").write_text(json.dumps(frames), encoding="utf-8")
    (tmp_path / "g.json").write_text(json.dumps(glossary), encoding="utf-8")
    text = review.build_markdown(tmp_path / "f.json", tmp_path / "g.json", ())
    assert "`b.up.simple`" in text and "`a.up.simple`" not in text
    assert "`k2`" in text and "`k1`" not in text
    assert "* Frames: 1" in text and "* Glossary entries: 1" in text


def test_pipes_and_newlines_are_escaped() -> None:
    assert review._escape("a|b\nc") == "a\\|b c"


def test_the_committed_document_is_current() -> None:
    assert review.OUTPUT.exists(), "run: python scripts/gen_uz_review.py"
    assert review.OUTPUT.read_text(encoding="utf-8") == review.build_markdown(), (
        "docs/uz_review.md is out of date: run python scripts/gen_uz_review.py"
    )
