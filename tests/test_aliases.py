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
