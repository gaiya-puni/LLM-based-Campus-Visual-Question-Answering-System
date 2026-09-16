"""Fail CI when tracked source contains credentials."""

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SECRET_PATTERNS = {
    "OpenAI-style API key": re.compile(rb"sk-[A-Za-z0-9_-]{16,}"),
    "AWS access key": re.compile(rb"AKIA[0-9A-Z]{16}"),
    "Google-style API key": re.compile(rb"AIza[0-9A-Za-z_-]{20,}"),
    "hardcoded AMap web service key": re.compile(
        rb"AMAP_KEY\s*=\s*['\"][0-9a-fA-F]{32}['\"]"
    ),
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def tracked_files():
    output = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=ROOT
    )
    for raw_path in output.split(b"\0"):
        if raw_path:
            yield ROOT / raw_path.decode("utf-8")


def check_secrets():
    failures = []
    for path in tracked_files():
        if not path.is_file():
            continue
        data = path.read_bytes()
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(data):
                failures.append(f"{path.relative_to(ROOT)}: {label}")
    return failures


def main():
    failures = check_secrets()
    if failures:
        raise SystemExit("Publication security check failed:\n- " + "\n- ".join(failures))
    print("Publication security check passed.")


if __name__ == "__main__":
    main()
