"""Replace credential-shaped strings in tracked Jupyter notebooks."""

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SECRET_PATTERNS = (
    (re.compile(r"sk-[A-Za-z0-9_-]{16,}"), "REDACTED_API_KEY"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "REDACTED_AWS_ACCESS_KEY"),
    (re.compile(r"AIza[0-9A-Za-z_-]{20,}"), "REDACTED_GOOGLE_API_KEY"),
)


def sanitize(value):
    if isinstance(value, str):
        for pattern, replacement in SECRET_PATTERNS:
            value = pattern.sub(replacement, value)
        return value
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, dict):
        return {key: sanitize(item) for key, item in value.items()}
    return value


def main():
    for path in ROOT.rglob("*.ipynb"):
        if any(part in {".git", "node_modules", ".venv", "private_data"} for part in path.parts):
            continue
        with path.open(encoding="utf-8") as source:
            notebook = json.load(source)
        sanitized = sanitize(notebook)
        if sanitized != notebook:
            with path.open("w", encoding="utf-8", newline="\n") as output:
                json.dump(sanitized, output, ensure_ascii=False, indent=1)
                output.write("\n")


if __name__ == "__main__":
    main()
