"""Quick smoke checks for Chrome profile + routing (no GUI clicks)."""
from __future__ import annotations

from voxoryl.chrome_control import _act_really_clicked, _picker_present, classify_chrome_intent
from voxoryl.pipelines import match_pipeline
from voxoryl.software_knowledge import (
    list_chrome_local_profiles,
    resolve_chrome_profile_directory,
    resolve_profile,
)


def main() -> None:
    print("LOCAL PROFILES:")
    for p in list_chrome_local_profiles():
        print(" ", p["directory"], "|", p["gaia_name"], "|", p["user_name"])

    msg = "open Chrome and select my profile"
    prof = resolve_profile("chrome", msg)
    directory = resolve_chrome_profile_directory(prof)
    print("PROFILE", prof and prof.get("id"), prof and prof.get("prefer"))
    print("DIRECTORY", directory)
    assert match_pipeline(msg) == "chrome"
    assert classify_chrome_intent(msg) == "open_profile"
    assert match_pipeline("hello how are you") != "chrome"
    assert classify_chrome_intent("hello how are you") is None
    assert _picker_present("YES\nWho's using Chrome? profiles")
    assert not _picker_present("Voxoryl orb thinking on desktop")
    assert not _act_really_clicked({"ok": True, "plan": {"steps": []}})
    assert _act_really_clicked(
        {
            "ok": True,
            "clicked": True,
            "executed": {"executed": [{"ok": True, "action": "click"}]},
        }
    )
    from voxoryl.screen import computer_use_enabled
    from voxoryl.windows_ops import open_app  # noqa: F401

    print("computer_use_enabled", computer_use_enabled())
    print("OK")


if __name__ == "__main__":
    main()
