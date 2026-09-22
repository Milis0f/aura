"""The settings registry: typed reads, refused writes, and what developer mode may hide."""

from __future__ import annotations

import pytest

from aura import db
from aura.core import settings


def test_a_declared_setting_reads_back_typed():
    """The table stores text; nothing downstream should ever have to parse it again."""
    assert settings.get("search.timeout_seconds") == 12.0
    assert isinstance(settings.get("search.timeout_seconds"), float)
    assert settings.get("cards.enrich_budget") == 24
    assert isinstance(settings.get("cards.enrich_budget"), int)
    assert settings.get("automount") is True
    assert isinstance(settings.get("automount"), bool)


def test_a_value_out_of_range_is_refused_with_a_reason():
    """Refused at the door, so a setting can never put the box somewhere only a reinstall clears."""
    with pytest.raises(ValueError, match="Maximum 60"):
        settings.put("search.timeout_seconds", 999)
    with pytest.raises(ValueError, match="Minimum 1"):
        settings.put("cards.enrich_parallel", 0)
    with pytest.raises(ValueError, match="parmi"):
        settings.put("dev.log_level", "BAVARD")
    with pytest.raises(ValueError, match="valide"):
        settings.put("cards.enrich_budget", "beaucoup")
    assert settings.get("search.timeout_seconds") == 12.0  # nothing was stored


def test_an_undeclared_key_is_not_storable():
    with pytest.raises(KeyError):
        settings.put("search.inventé", "1")
    with pytest.raises(KeyError):
        settings.get("search.inventé")


def test_a_write_that_bypasses_the_registry_still_invalidates_the_cache():
    """Any code path may call db.set_setting; a cache that missed it would serve a stale value
    until the next restart."""
    assert settings.get("search.timeout_seconds") == 12.0  # fill the cache
    db.set_setting("search.timeout_seconds", "31")
    assert settings.get("search.timeout_seconds") == 31.0


def test_a_stored_value_that_no_longer_passes_falls_back():
    """Bounds can tighten between versions, and an older database will hold values outside them.
    Reading one must give the default, not raise on a page nobody can then open."""
    db.set_setting("cards.enrich_parallel", "9999")  # above the declared maximum of 16
    assert settings.get("cards.enrich_parallel") == 6
    db.set_setting("search.timeout_seconds", "pas un nombre")
    assert settings.get("search.timeout_seconds") == 12.0


def test_developer_settings_are_absent_not_merely_hidden():
    keys = {entry["key"] for entry in settings.schema(developer=False)}
    assert "search.timeout_seconds" not in keys
    assert "automount" in keys
    assert "search.timeout_seconds" in {entry["key"] for entry in settings.schema(developer=True)}


def test_leaving_developer_mode_restores_the_defaults():
    """Otherwise the box keeps behaving oddly for a reason nobody can see any more."""
    settings.put(settings.DEVELOPER_KEY, True)
    settings.put("search.timeout_seconds", 45)
    settings.put("cards.enrich_parallel", 16)
    assert settings.get("search.timeout_seconds") == 45.0

    cleared = settings.reset_scope(settings.DEVELOPER)

    assert "search.timeout_seconds" in cleared
    assert settings.get("search.timeout_seconds") == 12.0
    assert settings.get("cards.enrich_parallel") == 6
    assert settings.developer_on() is True  # the switch itself is not one of the knobs it guards


def test_a_secret_never_travels_back_to_the_page():
    settings.put("tmdb_api_key", "a-real-key-that-must-not-leak")
    entry = next(e for e in settings.schema(developer=True) if e["key"] == "tmdb_api_key")
    assert entry["secret"] is True
    assert entry["value"] is True  # set, and that is all the interface needs
    assert "a-real-key" not in str(entry)


def test_every_declaration_is_self_consistent():
    """A typo in the table above would otherwise only surface the day someone opens that panel."""
    for setting in settings.DECLARED:
        assert setting.scope in (settings.USER, settings.DEVELOPER), setting.key
        assert setting.label and setting.group, setting.key
        assert setting.coerce(setting.store(setting.default)) == setting.default, setting.key
        if setting.choices:
            assert str(setting.default) in setting.choices, setting.key
        if setting.kind in (int, float) and setting.minimum is not None and setting.maximum is not None:
            assert setting.minimum <= setting.default <= setting.maximum, setting.key
