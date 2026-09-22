from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Repo root = parent of voxoryl package's parent when run as module, else cwd
ROOT = Path(__file__).resolve().parent.parent
SETUP_PATH = ROOT / "setup" / "voxoryl.setup.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_setup(path: Path | None = None) -> dict[str, Any]:
    setup_file = path or SETUP_PATH
    if not setup_file.exists():
        raise FileNotFoundError(
            f"Missing {setup_file}. Clones need setup/voxoryl.setup.json to bootstrap private data."
        )
    return json.loads(setup_file.read_text(encoding="utf-8"))


def ensure_private_layout(root: Path | None = None) -> dict[str, Any]:
    """
    Read setup/voxoryl.setup.json and create private data files from templates.
    Never overwrites existing personal files (never_overwrite: true).
    """
    root = root or ROOT
    setup = load_setup(root / "setup" / "voxoryl.setup.json")
    created: list[str] = []
    skipped: list[str] = []
    generated: list[str] = []

    for directory in setup.get("directories") or []:
        path = root / directory
        path.mkdir(parents=True, exist_ok=True)

    for spec in setup.get("files") or []:
        rel = spec.get("path")
        if not rel:
            continue
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        never_overwrite = bool(spec.get("never_overwrite", True))

        if dest.exists() and never_overwrite:
            skipped.append(rel)
            continue

        template_rel = spec.get("template")
        if template_rel:
            src = root / template_rel
            if not src.exists():
                skipped.append(f"{rel} (missing template {template_rel})")
                continue
            if dest.exists() and not never_overwrite:
                dest.unlink()
            shutil.copyfile(src, dest)
            # Fill owner placeholder timestamps lightly for memory.json
            if dest.suffix == ".json" and dest.name == "memory.json":
                try:
                    data = json.loads(dest.read_text(encoding="utf-8"))
                    if data.get("updated_at") is None:
                        data["updated_at"] = _now()
                        dest.write_text(json.dumps(data, indent=2), encoding="utf-8")
                except json.JSONDecodeError:
                    pass
            created.append(rel)
            continue

        if spec.get("generate") == "mindmap":
            try:
                from voxoryl.knowledge import knowledge
                from voxoryl.mindmap import mindmap

                graph = mindmap.sync_from_knowledge(knowledge.sections())
                mindmap.render_html(graph)
                generated.append(rel)
            except Exception:
                # First boot before modules warm — mindmap builds on /mindmap hit
                skipped.append(f"{rel} (deferred)")
            continue

        if not dest.exists():
            dest.write_text("", encoding="utf-8")
            created.append(rel)

    marker = root / "data" / ".voxoryl_bootstrapped"
    marker.write_text(
        json.dumps(
            {
                "setup_version": setup.get("version"),
                "bootstrapped_at": _now(),
                "created": created,
                "skipped_existing": skipped,
                "generated": generated,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    result = {
        "ok": True,
        "setup": str((root / "setup" / "voxoryl.setup.json").resolve()),
        "private_dir": str((root / "data").resolve()),
        "created": created,
        "skipped_existing": skipped,
        "generated": generated,
        "privacy": setup.get("privacy"),
    }

    # Probe hardware + recommend (fast). Heavy ollama pulls happen via install script / --install only.
    models_info: dict[str, Any] | None = None
    profile = root / "data" / "hardware_profile.json"
    force_models = os.getenv("VOXORYL_FORCE_MODEL_SETUP", "0") == "1"
    try:
        from voxoryl.models_setup import recommend_models, _save_report

        if force_models or not profile.exists():
            models_info = recommend_models()
            _save_report({"ok": True, "recommendation": models_info, "note": "pull via: python -m voxoryl.models_setup --install"})
        else:
            models_info = json.loads(profile.read_text(encoding="utf-8"))
            models_info["cached"] = True
    except Exception as exc:
        models_info = {"ok": False, "error": str(exc)}
    result["models"] = models_info
    return result


if __name__ == "__main__":
    result = ensure_private_layout()
    print(json.dumps(result, indent=2))
