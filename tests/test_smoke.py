"""Minimal smoke tests — keep public clone green."""

from __future__ import annotations


def test_import_voxoryl():
    import voxoryl

    assert getattr(voxoryl, "__version__", None)


def test_settings_load():
    from voxoryl.config import settings

    assert settings.voxoryl_port > 0
    assert str(settings.voxoryl_data_dir)


def test_software_knowledge_seed_has_no_hardcoded_owner_email():
    from voxoryl.software_knowledge import SEED

    blob = str(SEED.get("chrome", {}).get("profiles", {}))
    assert "vishalgaur2002" not in blob
    assert "neuoptic" not in blob.lower()


def test_reels_module_and_instagram_scaffold():
    from voxoryl import instagram, reels

    assert callable(reels.list_reels)
    assert callable(reels.ingest_url)
    assert callable(instagram.publish_reel)
    assert callable(instagram.is_configured)
    st = instagram.status()
    assert st.get("ok") is True
    assert "configured" in st
    assert "Jarvis" not in (st.get("hint") or "")
