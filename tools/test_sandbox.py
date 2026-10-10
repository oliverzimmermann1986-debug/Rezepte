"""Test-Konfiguration vor jedem app-Import isolieren, auch bei direktem pytest."""
from pathlib import Path
import os
import uuid

import yaml

_sandbox: Path | None = None


def install_test_environment(root: Path) -> Path:
    global _sandbox
    if _sandbox is not None:
        return _sandbox
    root = root.resolve()
    sandbox = root / ".tmp" / f"tests-{uuid.uuid4().hex[:12]}"
    sandbox.mkdir(parents=True)
    config = yaml.safe_load((root / "config" / "config.example.yaml").read_text(encoding="utf-8"))
    paths = config.setdefault("paths", {})
    for key, value in list(paths.items()):
        if isinstance(value, str):
            paths[key] = str(sandbox / key)
    for key in ("data_dir", "db_path", "temp_dir", "logs_dir", "recipe_dir", "wedding_dir",
                "recipes_dir", "recipes_root", "recipe_output_dir", "wedding_output_dir"):
        paths[key] = str(sandbox / key)
    config.pop("mail", None)
    config.pop("schedule", None)
    config.setdefault("external_hdd", {})["enabled"] = False
    config["webhooks"] = []
    ai = config.setdefault("ai", {})
    ai["auto_translate"] = False
    ai.setdefault("openai", {})["api_key"] = ""
    ai.setdefault("image_generation", {})["enabled"] = False
    ai.setdefault("video_fallback", {})["enabled"] = False
    config_file = sandbox / "config.yaml"
    config_file.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    # Eingehende SCRAPPER_CONFIG-Werte können auf echte Installationen zeigen.
    # Testläufe bekommen deshalb immer eine eigene Konfiguration.
    os.environ["SCRAPPER_CONFIG"] = str(config_file)
    # Betreiberangaben gehören nicht in Testantworten oder Testartefakte.
    os.environ.pop("SCRAPPER_LEGAL_CONFIG_FILE", None)
    os.environ["REZEPTE_BROWSER_ARTIFACT_DIR"] = str(sandbox / "browser")
    _sandbox = sandbox
    return sandbox
