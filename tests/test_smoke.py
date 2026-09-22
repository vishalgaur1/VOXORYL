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
    assert callable(reels.import_collection)
    assert callable(reels.gate_reel)
    assert callable(reels.list_quarantined)
    assert callable(reels.collection_learned)
    assert callable(instagram.publish_reel)
    assert callable(instagram.is_configured)
    assert callable(instagram.list_recent_media)
    st = instagram.status()
    assert st.get("ok") is True
    assert "configured" in st
    assert "Jarvis" not in (st.get("hint") or "")
    assert "saved_note" in st


def test_reels_url_extract_and_heuristic_gate():
    from voxoryl.reels import _heuristic_gate, extract_urls_multiline

    text = (
        "https://www.instagram.com/reel/AAA/\n"
        "https://www.instagram.com/reel/BBB/\n"
    )
    urls = extract_urls_multiline(text)
    assert len(urls) == 2
    gate = _heuristic_gate(
        title="Shortcut tip",
        caption="Use this keyboard shortcut to speed up your workflow in VS Code",
        transcript="",
        summary="A reusable editor shortcut tip.",
    )
    assert gate["outcome"] in {"promote", "quarantine", "reject"}
    empty = _heuristic_gate(title="", caption="", transcript="", summary="")
    assert empty["outcome"] == "reject"
