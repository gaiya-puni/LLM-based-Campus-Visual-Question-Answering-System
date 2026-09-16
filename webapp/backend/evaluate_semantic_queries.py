"""Evaluate direct lookup and scene classification on semantic_query_cases.json."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from server import (
    _SEMANTIC_RETRIEVER,
    _classify_poi_query,
    _looks_like_institution_location_query,
    _matched_scene_profiles,
    direct_configured_poi_matches,
    rank_campus_places,
    search_colleges,
    search_plants,
)


BASE = Path(__file__).resolve().parent


def evaluate_case(case: dict) -> tuple[bool, str]:
    query = case["query"]
    mode = case["mode"]
    plants = search_plants(query)
    colleges = search_colleges(query)

    if mode == "direct":
        matches = direct_configured_poi_matches(query)
        if len(matches) != 1:
            return False, f"expected exactly one direct match, got {len(matches)}"
        expected_id = case.get("expectedPoiId")
        if expected_id and matches[0].get("id") != expected_id:
            return False, f"expected {expected_id}, got {matches[0].get('id')}"
        if matches[0].get("category") != case.get("expectedCategory"):
            return False, f"unexpected category {matches[0].get('category')}"
        ranked = rank_campus_places(
            query,
            plants,
            colleges,
            institution_location_requested=_looks_like_institution_location_query(query),
        )
        return (not ranked, "direct query incorrectly produced Top-K" if ranked else "ok")

    if mode == "scene":
        scene_ids = [item.get("id") for item in _matched_scene_profiles(query)]
        expected_scene = case.get("expectedScene")
        if expected_scene not in scene_ids:
            return False, f"expected scene {expected_scene}, got {scene_ids}"
        profile = _classify_poi_query(
            query,
            plants,
            colleges,
            _looks_like_institution_location_query(query),
        )
        expected_category = case.get("expectedCategory")
        if expected_category not in profile.get("categories", []):
            return False, f"expected category {expected_category}, got {profile.get('categories')}"
        return True, "ok"

    if mode == "unsupported":
        matches = direct_configured_poi_matches(query)
        ranked = rank_campus_places(
            query,
            plants,
            colleges,
            institution_location_requested=_looks_like_institution_location_query(query),
        )
        return (
            not matches and not ranked,
            f"unexpected matches={len(matches)} ranked={len(ranked)}" if matches or ranked else "ok",
        )

    return False, f"unknown mode {mode}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true", help="exit non-zero when any case fails")
    args = parser.parse_args()
    cases = json.loads((BASE / "semantic_query_cases.json").read_text(encoding="utf-8"))
    totals = Counter()
    failures = []
    for case in cases:
        passed, detail = evaluate_case(case)
        totals[(case["mode"], "pass" if passed else "fail")] += 1
        if not passed:
            failures.append((case["query"], detail))

    passed_count = sum(value for (mode, result), value in totals.items() if result == "pass")
    print("semantic backend:", _SEMANTIC_RETRIEVER.status())
    print(f"passed={passed_count}/{len(cases)} accuracy={passed_count / len(cases):.1%}")
    for mode in sorted({case["mode"] for case in cases}):
        print(f"{mode}: pass={totals[(mode, 'pass')]} fail={totals[(mode, 'fail')]}")
    for query, detail in failures:
        print(f"FAIL {query}: {detail}")
    if args.strict and failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
