"""User-defined synonym registry: deterministic, off by default, never fabricates."""
from __future__ import annotations

import json

from docdex import aliases as al


def _project_with_aliases(tmp_path, mapping):
    from docdex.scaffold import run_init
    project = run_init(tmp_path, quiet=True)
    project.aliases_path.write_text(json.dumps(mapping), encoding="utf-8")
    return project


def test_absent_file_is_no_aliases(tmp_path):
    from docdex.scaffold import run_init
    project = run_init(tmp_path, quiet=True)
    project.aliases_path.unlink(missing_ok=True)
    assert al.load_aliases(project) == []


def test_malformed_file_is_ignored_not_raised(tmp_path):
    from docdex.scaffold import run_init
    project = run_init(tmp_path, quiet=True)
    project.aliases_path.write_text("{not json", encoding="utf-8")
    assert al.load_aliases(project) == []          # no exception


def test_group_loads_key_plus_synonyms(tmp_path):
    project = _project_with_aliases(tmp_path, {"legal name": ["vendor", "supplier"]})
    groups = al.load_aliases(project)
    assert len(groups) == 1
    assert set(groups[0]) == {"legal name", "vendor", "supplier"}


def test_expand_stems_is_symmetric(tmp_path):
    project = _project_with_aliases(tmp_path, {"legal name": ["vendor", "supplier"]})
    groups = al.load_aliases(project)
    from docdex.stemming import stem
    # query mentions "vendor" -> expansion offers legal/name/supplier stems
    got = al.expand_stems("who is the vendor", groups)
    assert stem("supplier") in got and stem("legal") in got


def test_label_variants(tmp_path):
    project = _project_with_aliases(tmp_path, {"legal name": ["vendor", "supplier"]})
    groups = al.load_aliases(project)
    variants = al.label_variants("Legal name", groups)
    assert {"vendor"} in variants and {"supplier"} in variants


from docdex import context as ctxmod
from docdex import index_db


def _synced_alias_project(tmp_path, mapping, files):
    from docdex.scaffold import run_init
    from docdex.sync import run_sync
    root = tmp_path / "corpus"
    root.mkdir()
    for name, body in files.items():
        (root / name).write_text(body, encoding="utf-8")
    project = run_init(root, quiet=True)
    project.aliases_path.write_text(json.dumps(mapping), encoding="utf-8")
    run_sync(project, quiet=True)
    index_db.build(project, quiet=True)
    return project


def test_alias_widens_retrieval(tmp_path):
    project = _synced_alias_project(
        tmp_path, {"legal name": ["vendor", "supplier"]},
        {"deal.txt": "The vendor is Acme Corporation of Delaware.\n"})
    packet = ctxmod.build_packet(project, "legal name", budget=1500)
    assert "deal.txt" in packet          # found via the alias "vendor"


def test_alias_hit_is_tagged_approx(tmp_path):
    project = _synced_alias_project(
        tmp_path, {"legal name": ["vendor"]},
        {"deal.txt": "The vendor is Acme Corporation.\n"})
    packet = ctxmod.build_packet(project, "legal name", budget=1500)
    assert "~approx" in packet


def test_no_alias_file_unchanged(tmp_path):
    # Without the mapping, "legal name" does NOT find a doc that only says vendor.
    project = _synced_alias_project(
        tmp_path, {}, {"deal.txt": "The vendor is Acme Corporation.\n"})
    project.aliases_path.unlink(missing_ok=True)
    packet = ctxmod.build_packet(project, "legal name", budget=1500)
    assert "deal.txt" not in packet


def test_explain_shows_alias_expansion(tmp_path):
    project = _synced_alias_project(
        tmp_path, {"legal name": ["vendor", "supplier"]},
        {"deal.txt": "The vendor is Acme.\n"})
    packet = ctxmod.build_packet(project, "legal name", budget=1500, explain=True)
    assert "aliases:" in packet


def _section(packet: str, header: str) -> str:
    """The body of a `## Header` section (up to the next `## `), for asserting a
    value landed in the RIGHT place (Answers vs Conflicts), not just anywhere."""
    lines = packet.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == header:
            body = []
            for nxt in lines[i + 1:]:
                if nxt.startswith("## "):
                    break
                body.append(nxt)
            return "\n".join(body)
    return ""


def _answer_line(packet: str, label: str) -> str:
    for line in packet.splitlines():
        if line.startswith(f"- {label}:"):
            return line
    return ""


def test_form_field_reads_value_after_synonym_label(tmp_path):
    # Field is "Effective date"; the doc only carries the synonym label.
    project = _synced_alias_project(
        tmp_path, {"effective date": ["commencement date"]},
        {"form_src.txt": "Commencement date: 31/12/2026\n"})
    packet = ctxmod.build_packet(project, "fill the form", budget=2000,
                                 form_fields=["Effective date"])
    line = _answer_line(packet, "Effective date")
    assert "## Answers" in packet
    assert "31/12/2026" in line          # value read after the synonym label
    assert "~approx" in line             # and honestly flagged approximate


def test_form_field_own_label_still_literal_and_exact(tmp_path):
    # Both labels present as distinct facts: the field's OWN literal label wins
    # and is exact. (Indexing collapses newlines to spaces, so the two labelled
    # facts are separated by a clause boundary to keep them distinct — as they
    # would be on the two separate lines of the form.)
    project = _synced_alias_project(
        tmp_path, {"effective date": ["commencement date"]},
        {"form_src.txt": "Effective date: 01/01/2026; Commencement date: 31/12/2026\n"})
    packet = ctxmod.build_packet(project, "fill the form", budget=2000,
                                 form_fields=["Effective date"])
    line = _answer_line(packet, "Effective date")
    assert "01/01/2026" in line          # own literal label wins
    assert "31/12/2026" not in line      # not the synonym-labelled value
    assert "~approx" not in line         # literal match → exact, not approximate


def test_alias_conflict_is_flagged(tmp_path):
    # Two docs disagree under SYNONYM labels of the same field — must surface as a
    # conflict keyed on the field, not vanish because neither says "legal name".
    project = _synced_alias_project(
        tmp_path, {"legal name": ["vendor", "supplier"]},
        {"a.txt": "Vendor: 11AAAAA1111A1Z5\n",
         "b.txt": "Supplier: 22BBBBB2222B2Z5\n"})
    packet = ctxmod.build_packet(project, "fill the form", budget=2000,
                                 form_fields=["Legal name"])
    conflicts = _section(packet, "## Conflicts")
    assert conflicts                      # a Conflicts section exists
    assert "a.txt" in conflicts and "b.txt" in conflicts   # both sources named


def test_unrelated_token_not_tagged_approx(tmp_path):
    # Query never contained the full phrase "service level"; a lone shared token
    # ("owner") against group service level↔SLA must NOT be tagged ~approx.
    project = _synced_alias_project(
        tmp_path, {"service level": ["sla"]},
        {"sla.txt": "SLA owner: alice@x.com\n"})
    packet = ctxmod.build_packet(project, "service owner email", budget=2000)
    assert "alice@x.com" in packet        # the hit was retrieved and packed
    assert "~approx" not in packet        # but not falsely flagged as an alias hit
