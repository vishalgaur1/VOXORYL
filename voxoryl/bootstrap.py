from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from voxoryl.paths import (
    ensure_user_dirs,
    migrate_from_repo_if_needed,
    repo_root,
    user_data_dir,
    user_data_subdir,
    user_env_path,
    user_workspace_dir,
)

# Repo root holds templates + setup manifest; private files live under user_data_dir().
ROOT = repo_root()
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


def _dest_for_rel(rel: str, *, user_root: Path) -> Path:
    """Map setup-relative paths (data/..., workspace_sandbox) onto the OS user-data root."""
    norm = rel.replace("\\", "/").lstrip("./")
    return user_root / norm


def ensure_private_layout(root: Path | None = None) -> dict[str, Any]:
    """
    Read setup/voxoryl.setup.json and create private data files from templates.

    Templates are read from the install/repo root. Destinations are under the
    OS user-data directory (never only inside the clone). Existing personal
    files are never overwritten (never_overwrite: true).
    """
    migrate_from_repo_if_needed()
    ensure_user_dirs()

    code_root = root or ROOT
    user_root = user_data_dir()
    setup = load_setup(code_root / "setup" / "voxoryl.setup.json")
    created: list[str] = []
    skipped: list[str] = []
    generated: list[str] = []

    for directory in setup.get("directories") or []:
        path = _dest_for_rel(directory, user_root=user_root)
        path.mkdir(parents=True, exist_ok=True)

    for spec in setup.get("files") or []:
        rel = spec.get("path")
        if not rel:
            continue
        dest = _dest_for_rel(rel, user_root=user_root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        never_overwrite = bool(spec.get("never_overwrite", True))

        if dest.exists() and never_overwrite:
            skipped.append(rel)
            continue

        template_rel = spec.get("template")
        if template_rel:
            src = code_root / template_rel
            if not src.exists():
                skipped.append(f"{rel} (missing template {template_rel})")
                continue
            if dest.exists() and not never_overwrite:
                dest.unlink()
            shutil.copyfile(src, dest)
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
                skipped.append(f"{rel} (deferred)")
            continue

        if not dest.exists():
            dest.write_text("", encoding="utf-8")
            created.append(rel)

    # Knowledge stub from repo example if still missing
    knowledge_dest = user_data_subdir() / "knowledge.md"
    example_knowledge = code_root / "knowledge.example.md"
    if not knowledge_dest.exists() and example_knowledge.is_file():
        shutil.copyfile(example_knowledge, knowledge_dest)
        created.append("data/knowledge.md (from knowledge.example.md)")

    # Ensure user config.env exists (never overwrite secrets)
    env_dest = user_env_path()
    example_env = code_root / ".env.example"
    if not env_dest.exists() and example_env.is_file():
        from voxoryl.paths import rewrite_data_dir_in_env

        env_dest.write_text(example_env.read_text(encoding="utf-8"), encoding="utf-8")
        rewrite_data_dir_in_env(env_dest)
        created.append("config.env (from .env.example)")

    marker = user_data_subdir() / ".voxoryl_bootstrapped"
    marker.write_text(
        json.dumps(
            {
                "setup_version": setup.get("version"),
                "bootstrapped_at": _now(),
                "user_data_dir": str(user_root.resolve()),
                "created": created,
                "skipped_existing": skipped,
                "generated": generated,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    result: dict[str, Any] = {
        "ok": True,
        "setup": str((code_root / "setup" / "voxoryl.setup.json").resolve()),
        "code_root": str(code_root.resolve()),
        "user_data_dir": str(user_root.resolve()),
        "private_dir": str(user_data_subdir().resolve()),
        "workspace": str(user_workspace_dir().resolve()),
        "config_env": str(env_dest.resolve()),
        "created": created,
        "skipped_existing": skipped,
        "generated": generated,
        "privacy": setup.get("privacy"),
    }

    models_info: dict[str, Any] | None = None
    profile = user_data_subdir() / "hardware_profile.json"
    force_models = os.getenv("VOXORYL_FORCE_MODEL_SETUP", "0") == "1"
    try:
        from voxoryl.model_planner import plan_models, save_plan
        from voxoryl.models_setup import _save_report

        if force_models or not profile.exists():
            models_info = plan_models()
            save_plan(models_info)
            _save_report(
                {
                    "ok": True,
                    "recommendation": models_info,
                    "note": "pull via: python scripts/bootstrap_voxoryl.py  or  python -m voxoryl.models_setup --apply",
                }
            )
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
