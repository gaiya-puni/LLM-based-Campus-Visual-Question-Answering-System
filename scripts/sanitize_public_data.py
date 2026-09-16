"""Remove personal identifiers and free-text messages from public datasets."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_json(relative_path):
    path = ROOT / relative_path
    with path.open(encoding="utf-8") as source:
        return path, json.load(source)


def write_json(path, data, indent=2):
    with path.open("w", encoding="utf-8", newline="\n") as output:
        json.dump(data, output, ensure_ascii=False, indent=indent)
        output.write("\n")


def sanitize_tree_records(relative_path, indent):
    path, records = read_json(relative_path)
    for record in records:
        if record.get("owner") is not None:
            record["owner"] = {
                "name": "校园认养者",
                "title": "校园认养记录",
                "slogan": "",
                "content": "",
                "images": [],
            }
        record["user"] = None
        record["qrcode"] = None
    write_json(path, records, indent=indent)


def sanitize_pois():
    path, records = read_json("webapp/backend/campus_pois.json")
    private_fields = {
        "ownerName", "userName", "ownerTitle", "ownerSlogan", "ownerContent"
    }
    for record in records:
        meta = record.get("meta")
        if isinstance(meta, dict):
            for field in private_fields:
                meta.pop(field, None)
    write_json(path, records)


def sanitize_emotions():
    path, _records = read_json("webapp/frontend/src/assets/emotion_analysis.json")
    # Free-text adoption messages are private source material, not demo fixtures.
    write_json(path, [])


def main():
    for relative_path in (
        "data/all_trees.json",
        "data/all_trees_for_lib.json",
        "visualization/charts/sankey/data_for_sankey.json",
    ):
        indent = 2 if "sankey" in relative_path else 4
        sanitize_tree_records(relative_path, indent=indent)
    sanitize_pois()
    sanitize_emotions()


if __name__ == "__main__":
    main()
