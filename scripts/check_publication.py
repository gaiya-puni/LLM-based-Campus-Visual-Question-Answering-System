"""Fail CI when tracked source contains credentials or public personal data."""

import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SECRET_PATTERNS = {
    "OpenAI-style API key": re.compile(rb"sk-[A-Za-z0-9_-]{16,}"),
    "AWS access key": re.compile(rb"AKIA[0-9A-Z]{16}"),
    "Google-style API key": re.compile(rb"AIza[0-9A-Za-z_-]{20,}"),
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


def read_json(relative_path):
    with (ROOT / relative_path).open(encoding="utf-8") as source:
        return json.load(source)


def check_public_data():
    failures = []
    for record in read_json("data/all_trees.json"):
        owner = record.get("owner") or {}
        if owner.get("name") not in (None, "", "校园认养者"):
            failures.append("data/all_trees.json: personal owner name")
            break
        if owner.get("slogan") or owner.get("content") or record.get("user") or record.get("qrcode"):
            failures.append("data/all_trees.json: message, user, or QR data")
            break

    private_poi_fields = {
        "ownerName", "userName", "ownerTitle", "ownerSlogan", "ownerContent"
    }
    for record in read_json("webapp/backend/campus_pois.json"):
        meta = record.get("meta") or {}
        if private_poi_fields.intersection(meta):
            failures.append("webapp/backend/campus_pois.json: private metadata field")
            break

    if read_json("webapp/frontend/src/assets/emotion_analysis.json"):
        failures.append("emotion_analysis.json: public free-text messages must be empty")
    return failures


def main():
    failures = check_secrets() + check_public_data()
    if failures:
        raise SystemExit("Publication security check failed:\n- " + "\n- ".join(failures))
    print("Publication security check passed.")


if __name__ == "__main__":
    main()
