"""Launch pytest with synthetic paths before application imports, even on main."""
from pathlib import Path
import os
import sys
import uuid


def main():
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path.insert(0, str(root))
    import yaml
    sandbox = root / ".tmp" / ("ci-" + uuid.uuid4().hex[:12])
    sandbox.mkdir(parents=True)
    config = yaml.safe_load((root / "config/config.example.yaml").read_text(encoding="utf-8"))
    paths = config.setdefault("paths", {})
    for key in set(paths) | {"db_path", "data_dir", "temp_dir", "logs_dir", "recipe_dir", "wedding_dir", "recipes_dir"}:
        if isinstance(paths.get(key, ""), str):
            paths[key] = str(sandbox / key)
    config.pop("mail", None)
    config.pop("schedule", None)
    config["webhooks"] = []
    config.setdefault("external_hdd", {})["enabled"] = False
    ai = config.setdefault("ai", {})
    ai["auto_translate"] = False
    ai.setdefault("openai", {})["api_key"] = ""
    ai.setdefault("image_generation", {})["enabled"] = False
    ai.setdefault("video_fallback", {})["enabled"] = False
    config_file = sandbox / "config.yaml"
    config_file.write_text(yaml.safe_dump(config), encoding="utf-8")
    os.environ["SCRAPPER_CONFIG"] = str(config_file)
    os.environ.pop("SCRAPPER_LEGAL_CONFIG_FILE", None)
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ["REZEPTE_BROWSER_ARTIFACT_DIR"] = str(root / "reports/ci/browser")
    from app.db import Database
    Database.__init__.__defaults__ = (sandbox / "fallback.db",)
    import pytest
    return pytest.main([*sys.argv[1:], "-p", "no:cacheprovider", "--basetemp", str(sandbox / "pytest")])


if __name__ == "__main__":
    raise SystemExit(main())
