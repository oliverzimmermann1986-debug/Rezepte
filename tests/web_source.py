"""Source checks cover the local classic-script modules in their loading order."""
from pathlib import Path
import re

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


def read_web_scripts() -> str:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    names = re.findall(r'<script src="/static/([^"?]+)\?v=', html)
    return "\n".join((STATIC / name).read_text(encoding="utf-8") for name in names)
