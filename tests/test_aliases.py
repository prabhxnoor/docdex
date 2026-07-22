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


def test_alias_only_hit(tmp_path):
    project = _project_with_aliases(tmp_path, {"legal name": ["vendor"]})
    groups = al.load_aliases(project)
    # "legal" is absent from the text, but its synonym "vendor" is present:
    assert al.alias_only_hit("legal", "the vendor is Acme", groups)
    # a term with no alias and not present -> not an alias hit:
    assert not al.alias_only_hit("banana", "the vendor is Acme", groups)


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


def test_form_field_reads_value_after_synonym_label(tmp_path):
    # Field is "Legal name"; the document only has a "Vendor:" label.
    project = _synced_alias_project(
        tmp_path, {"legal name": ["vendor"]},
        {"form_src.txt": "Vendor: Acme Corporation Pvt Ltd\nGoverning law: Delaware\n"})
    packet = ctxmod.build_packet(project, "fill the form", budget=2000,
                                 form_fields=["Legal name"])
    assert "Acme Corporation" in packet          # value read after the synonym label
    assert "~approx" in packet                    # and honestly flagged approximate


def test_form_field_own_label_still_literal_and_exact(tmp_path):
    # When the literal label is present, the value is exact (no alias fallback).
    project = _synced_alias_project(
        tmp_path, {"legal name": ["vendor"]},
        {"form_src.txt": "Legal name: Beta Industries\n"})
    packet = ctxmod.build_packet(project, "fill the form", budget=2000,
                                 form_fields=["Legal name"])
    assert "Beta Industries" in packet
