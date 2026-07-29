"""docdex's own artifacts must never appear in OS desktop search.

Reported as a bug: docdex's extracted `.txt` files show up in Spotlight and Finder
search. They should not — one reason cosmetic, one not:

  * **Duplicate hits.** Searching a phrase from a contract returns docdex's extracted
    copy alongside (or instead of) the real document.
  * **Confidentiality.** docdex writes a *plain-text* copy of every document it
    extracts, including PDFs and Office files whose contents were previously opaque
    to the OS indexer. Unprotected, the full text of private documents becomes
    searchable and sits in the Spotlight store. For a tool whose stated promise is
    "zero residue", that is a defect.

**Measured on macOS 26.5, because the obvious fix does not work.** A new file was
written into four directories and Spotlight queried after it settled:

    .hidden_dir/           -> 0 hits   (a dot-prefixed directory is skipped)
    something.noindex/     -> 0 hits   (the `.noindex` suffix is honoured)
    visible_dir/           -> 1 hit    (indexed, as expected)
    dir with an empty `.metadata_never_index` marker inside -> 1 hit — INDEXED

So the widely-cited `.metadata_never_index` marker is ineffective for a subdirectory
here and was removed rather than shipped as reassurance that does nothing.

That measurement also explains the original report. The v2 layout keeps state in
`~/.cache/docdex/...`, and `.cache` is hidden, so it was already safe — which is why
17,317 cached files on this machine were absent from Spotlight. But the **v1 layout**
kept state in a *visible* `_index/_state/extracted` inside the project: 164 such files
in an indexed folder were all 164 in the Spotlight index, and a content search
returned 149 of them. Same for any project whose `index_dir` is not hidden, or a
`DOCDEX_CACHE_DIR` pointed at a visible path.

Hence the fix does not depend on where state happens to live: the state directory's
own name ends in `.noindex`, so it is skipped whatever its parents look like.

These tests assert the structural invariant (every directory holding document text has
a path component Spotlight skips) rather than querying Spotlight, which needs tens of
seconds to settle and would make the suite slow and flaky.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from docdex.config import STATE_DIR
from docdex.scaffold import run_init
from docdex.sync import run_sync


def skipped_by_indexer(path: Path) -> bool:
    """Does the OS desktop indexer skip this path?

    Deliberately a LOCAL definition rather than an import of docdex's own helper.
    Two reasons, both learned the hard way:

    * A test that imports the function it is verifying tests that function against
      itself. This states the rule independently, from the measurement.
    * The release gate runs this file against the PREVIOUS release to prove the tests
      catch the old behaviour. Importing a helper this release introduced makes the
      whole file error at collection instead of failing an assertion — which proves
      the API changed, not that the behaviour did. The gate rejected exactly that.

    The rule, measured on macOS 26.5: a path component that is dot-prefixed or ends
    in `.noindex` is skipped. `.` and `..` start with a dot but hide nothing.
    """
    return any(part.startswith(".") or part.endswith(".noindex")
               for part in path.parts
               if part not in ("/", "", ".", ".."))


@pytest.fixture
def synced_project(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "contract.txt").write_text(
        "Confidential: liability cap is INR 4.2 crore.\n", encoding="utf-8")
    project = run_init(root, quiet=True)
    run_sync(project, quiet=True)
    return project


def test_every_dir_holding_document_text_is_hidden_from_search(synced_project):
    """The contract, stated once: nothing docdex writes document text into is
    reachable by the OS indexer."""
    for label, d in (("extracted text", synced_project.extracted_dir),
                     ("context dumps", synced_project.dumps_dir),
                     ("vision notes cache", synced_project.vision_dir),
                     ("index state", synced_project.state_dir)):
        assert skipped_by_indexer(d), (
            f"{label} ({d}) has no path component the OS indexer skips — its "
            f"contents will be indexed by Spotlight")


def test_the_extracted_text_really_is_there_to_protect(synced_project):
    """Guards the test above from passing vacuously over an empty cache."""
    texts = list(synced_project.extracted_dir.rglob("*.txt"))
    assert texts, "no extracted text was written; the test above proves nothing"
    assert any("4.2 crore" in t.read_text(encoding="utf-8") for t in texts), (
        "extracted text does not contain the document's content")


def test_protection_holds_even_in_a_fully_visible_location(tmp_path):
    """The real-world failure: a project (or cache) in a plainly visible directory.

    The v1 layout put state in a visible `_index/`, and that is what put 164
    extracted files into Spotlight. Relying on `~/.cache` being hidden is luck, not
    a design — so this builds the whole thing under a visible path and still expects
    protection.
    """
    visible_root = tmp_path / "VisibleDocuments"
    visible_root.mkdir()
    (visible_root / "deal.txt").write_text(
        "Liability cap is INR 9 crore.\n", encoding="utf-8")
    project = run_init(visible_root, quiet=True)
    run_sync(project, quiet=True)

    assert "hidden" not in str(visible_root).lower()
    assert skipped_by_indexer(project.extracted_dir), (
        f"extracted text under a visible root is exposed: {project.extracted_dir}")


def test_the_state_dir_name_is_what_provides_it(synced_project):
    """Pin the mechanism, not just the outcome.

    If a future change renames the state dir back to something plain, the invariant
    above could still pass by accident whenever the cache happens to sit under a
    hidden parent — and would silently stop protecting anyone whose cache does not.
    """
    assert STATE_DIR.endswith(".noindex"), (
        f"STATE_DIR is {STATE_DIR!r}; the `.noindex` suffix is the only part of this "
        f"that works regardless of where state lives")
    assert synced_project.state_dir.name.endswith(".noindex")


def test_the_users_own_documents_stay_searchable(synced_project):
    """The one place protection must NEVER be applied.

    Excluding the user's own corpus from Spotlight would be a far worse bug than the
    one being fixed — docdex is not entitled to make someone's documents unfindable.
    """
    assert not skipped_by_indexer(synced_project.root), (
        "docdex made the user's own document folder unsearchable")
    doc = synced_project.root / "contract.txt"
    assert doc.exists() and not skipped_by_indexer(doc.parent)


def test_a_pre_existing_visible_state_dir_is_migrated(tmp_path):
    """Existing installs must be protected without re-extracting anything.

    A rename is lossless and instant; re-extracting 10k PDFs would not be, so the
    upgrade path moves the directory rather than rebuilding it.
    """
    root = tmp_path / "legacyish"
    root.mkdir()
    (root / "a.txt").write_text("payment terms are net-45\n", encoding="utf-8")
    project = run_init(root, quiet=True)
    run_sync(project, quiet=True)

    # Simulate the pre-fix layout: move state back to the old plain name and leave a
    # marker file inside, so we can prove the SAME directory was carried over.
    old = project.state_dir.parent / "_state"
    project.state_dir.rename(old)
    (old / "carried_over.marker").write_text("x", encoding="utf-8")

    from docdex.config import ensure_state_dirs
    ensure_state_dirs(project)

    assert project.state_dir.name.endswith(".noindex")
    assert (project.state_dir / "carried_over.marker").exists(), (
        "the existing state directory was not migrated — a fresh empty one was "
        "created instead, orphaning the extracted text")
    assert not old.exists(), "the old visible state directory was left behind"


def test_migration_does_not_clobber_an_existing_new_dir(tmp_path):
    """If both names exist, the current one wins and the stale one is left alone for
    the operator, rather than silently overwritten."""
    root = tmp_path / "both"
    root.mkdir()
    (root / "a.txt").write_text("net-45\n", encoding="utf-8")
    project = run_init(root, quiet=True)
    run_sync(project, quiet=True)
    keep = project.state_dir / "inventory.tsv"
    assert keep.exists()

    stale = project.state_dir.parent / "_state"
    stale.mkdir()
    (stale / "inventory.tsv").write_text("stale\n", encoding="utf-8")

    from docdex.config import ensure_state_dirs
    ensure_state_dirs(project)
    assert "stale" not in keep.read_text(encoding="utf-8"), "stale state overwrote live state"


# ------------------ hardening from external review of this release --------------

def test_a_dotdot_component_is_not_mistaken_for_a_hidden_dir():
    """`..` starts with a dot but hides nothing.

    Found by adversarial review: the check returned True for `../_state`, so any
    caller — `docdex doctor` included — would report a plainly indexed directory as
    safe. A privacy guarantee that reports itself satisfied when it isn't is worse
    than no guarantee.
    """
    from docdex.config import is_hidden_from_desktop_search
    assert is_hidden_from_desktop_search(Path("../_state")) is False
    assert is_hidden_from_desktop_search(Path("../../visible/_state")) is False
    # ... while the real mechanisms still register.
    assert is_hidden_from_desktop_search(Path("/x/.cache/s")) is True
    assert is_hidden_from_desktop_search(Path("/x/s.noindex")) is True


def test_doctor_reports_a_leftover_exposed_state_dir(tmp_path, capsys):
    """A pre-fix state directory still on disk must be reported, not glossed over.

    Found by adversarial review: `doctor` only looked at the NEW paths, so if the
    rename had not run — or had failed — it printed "not indexed by Spotlight" while
    the old, fully-indexed directory sat beside it holding the same document text.
    """
    from docdex.doctor import Doctor
    root = tmp_path / "leftover"
    root.mkdir()
    (root / "a.txt").write_text("payment terms are net-45\n", encoding="utf-8")
    project = run_init(root, quiet=True)
    run_sync(project, quiet=True)

    old = project.state_dir.parent / "_state"
    old.mkdir(exist_ok=True)
    (old / "leaked.txt").write_text("CONFIDENTIAL liability cap\n", encoding="utf-8")

    d = Doctor(project)
    d.check_hidden_from_desktop_search()
    name, ok, detail = d.results[-1]
    assert not ok, f"doctor reported safe while {old} was still exposed: {detail}"
    assert "_state" in detail


def test_a_failed_migration_refuses_to_run_on_empty_state(tmp_path, monkeypatch):
    """If the rename fails, docdex must stop — not quietly index nothing.

    Found by adversarial review: the OSError was swallowed, then `mkdir` created a
    fresh empty state directory. docdex would then answer every query with "no
    results" for documents whose extracted text was sitting right there in the old
    directory, while that directory stayed exposed to Spotlight. Silent degradation
    is the failure mode this project exists to avoid.
    """
    from docdex.config import ConfigError, ensure_state_dirs
    root = tmp_path / "failmigrate"
    root.mkdir()
    (root / "a.txt").write_text("payment terms are net-45\n", encoding="utf-8")
    project = run_init(root, quiet=True)
    run_sync(project, quiet=True)

    # Put state back under the pre-fix name, then make the rename fail.
    old = project.state_dir.parent / "_state"
    project.state_dir.rename(old)

    def refuse(self, target):
        raise OSError("device or resource busy")
    monkeypatch.setattr(Path, "rename", refuse)

    with pytest.raises(ConfigError, match="_state"):
        ensure_state_dirs(project)
    assert old.is_dir(), "the original state directory must be left intact"
