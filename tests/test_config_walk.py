from __future__ import annotations

from pathlib import Path

import pytest

from docdex.config import NotAProject, Project
from docdex.walk import iter_source_files


def rels(project):
    return {rel for rel, _ in iter_source_files(project)}


def test_discovery_walks_up(project):
    nested = project.root / "Notes" / "Deep" / "Nested"
    found = Project.discover(nested)
    assert found.root == project.root


def test_discovery_fails_outside(tmp_path):
    with pytest.raises(NotAProject):
        Project.discover(tmp_path)


def test_cache_names_are_collision_proof(project):
    a = project.cache_path_for("Reports/A B.docx")
    b = project.cache_path_for("Reports/A_B.docx")
    assert a != b
    assert a.parent == b.parent  # same top-folder bucket


def test_cache_names_capped_for_long_paths(project):
    rel = "Reports/" + ("x" * 300) + ".docx"
    cache = project.cache_path_for(rel)
    assert len(cache.name.encode("utf-8")) < 255
    assert cache.suffix == ".txt"


def test_top_folder_promotions(project):
    assert project.top_folder_for("Reports/a.md") == "Reports"
    assert project.top_folder_for("rootfile.md") == "_root"
    assert project.top_folder_for(".docdex/Update/x.md") == "Update"
    assert project.top_folder_for(".docdex/vision_notes/x.md") == "vision_notes"


def test_walker_includes_and_skips(project):
    (project.update_dir / "dropped.md").write_text("inbox file", encoding="utf-8")
    (project.notes_dir / "note.md").write_text("Source: x\nnote", encoding="utf-8")
    got = rels(project)

    assert "Reports/Q1 report.md" in got
    assert "Notes/Deep/Nested/deep file.md" in got
    assert "_indexes/sibling.md" in got  # sibling of index dir must be indexed
    assert ".docdex/Update/dropped.md" in got
    assert ".docdex/vision_notes/note.md" in got
    assert "unsupported.bin" in got  # inventoried even if not extractable

    assert not any(r.startswith("node_modules/") for r in got)
    assert not any(r.startswith(".docdex/_state") for r in got)
    assert ".docdex/HANDOFF.md" not in got  # home internals are not corpus
    assert ".docdex/config.json" not in got  # the marker is never indexed
    assert ".hiddenfile.md" not in got
    assert "~$lock.docx" not in got


def test_walker_skips_custom_dirs(corpus):
    p = Project.create(corpus, index_dir="_index", skip_dirs=["Reports"])
    p.save()
    got = rels(p)
    assert not any(r.startswith("Reports/") for r in got)
    assert "Notes/hello.txt" in got


def test_index_dir_with_spaces(corpus):
    p = Project.create(corpus, index_dir="About My Project")
    from docdex.config import ensure_state_dirs
    ensure_state_dirs(p)
    p.save()
    (p.update_dir / "inbox.md").write_text("inbox", encoding="utf-8")
    got = rels(p)
    assert "About My Project/Update/inbox.md" in got
    assert not any(r.startswith("About My Project/_state") for r in got)
