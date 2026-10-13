"""CLI for the campus asset pipeline.

The first version is intentionally offline: it consumes a profile and a
candidate JSON file.  Network discovery belongs behind a future harvester
adapter; the generated bundle already has the fields needed to attach sources,
confidence and review status without changing the runtime service.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .normalize import normalize_candidates
from .profile import load_profile, runtime_config, validate_profile, write_json
from .validate import validate_bundle, validate_pois, validate_runtime_config
from .harvest import JsonHarvester
from .harvest import AmapPoiHarvester, HarvestCache, PublicWebHarvester


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "webapp" / "backend"


def _read_candidates(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("candidates must be a JSON array")
    return data


def _profile_and_existing(args):
    profile = load_profile(args.profile)
    existing = None
    base = Path(args.base_config) if getattr(args, "base_config", None) else None
    if base and base.exists():
        existing = json.loads(base.read_text(encoding="utf-8"))
    return profile, existing


def cmd_profile(args) -> int:
    profile = {
        "school": {
            "id": args.school_id,
            "name": args.school_name,
            "enName": args.school_en_name or "",
            "aliases": [],
            "stopWords": [],
        },
        "campus": {
            "name": args.campus_name,
            "slug": args.campus_slug,
            "aliases": [],
            "center": [args.lng, args.lat],
            "trustRadiusM": args.trust_radius,
            "coordinateSystem": args.coordinate_system,
            "waterName": "",
        },
    }
    problems = validate_profile(profile)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 2
    write_json(args.output, profile)
    print(f"profile written: {args.output}")
    return 0


def cmd_validate(args) -> int:
    profile, existing = _profile_and_existing(args)
    problems = validate_runtime_config(runtime_config(profile, existing))
    if args.candidates:
        candidates = _read_candidates(Path(args.candidates))
        normalized, pending = normalize_candidates(
            candidates, profile["campus"]["name"], campus_profile=profile["campus"],
        )
        problems.extend(validate_pois(
            normalized, profile["campus"]["name"], campus_profile=profile["campus"],
        ))
        print(f"normalized={len(normalized)} pending={len(pending)}")
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print("validation passed")
    return 0


def cmd_normalize(args) -> int:
    profile = load_profile(args.profile)
    candidates = _read_candidates(Path(args.candidates))
    normalized, pending = normalize_candidates(
        candidates, profile["campus"]["name"], campus_profile=profile["campus"],
    )
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "normalized_pois.json", normalized)
    write_json(output / "pending_pois.json", pending)
    print(f"normalized={len(normalized)} pending={len(pending)} output={output}")
    return 0


def cmd_harvest(args) -> int:
    result = JsonHarvester(args.input).collect()
    output = Path(args.output)
    write_json(output, result.candidates)
    print(json.dumps({
        "source": result.source,
        "candidateCount": len(result.candidates),
        "warnings": result.warnings,
        "output": str(output),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_harvest_amap(args) -> int:
    profile = load_profile(args.profile)
    cache = HarvestCache(args.cache, args.ttl) if args.cache else None
    result = AmapPoiHarvester(
        profile, args.keyword, api_key=args.api_key, cache=cache,
        max_pages=args.max_pages, page_size=args.page_size,
    ).collect()
    write_json(args.output, result.candidates)
    print(json.dumps({"source": result.source, "candidateCount": len(result.candidates),
                      "warnings": result.warnings, "output": str(args.output)},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_harvest_web(args) -> int:
    cache = HarvestCache(args.cache, args.ttl) if args.cache else None
    result = PublicWebHarvester(args.url, cache=cache, max_pages=args.max_pages).collect()
    write_json(args.output, result.candidates)
    print(json.dumps({"source": result.source, "candidateCount": len(result.candidates),
                      "warnings": result.warnings, "output": str(args.output)},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_build(args) -> int:
    profile, existing = _profile_and_existing(args)
    candidates = _read_candidates(Path(args.candidates))
    normalized, pending = normalize_candidates(
        candidates, profile["campus"]["name"], campus_profile=profile["campus"],
    )
    runtime = runtime_config(profile, existing)
    problems = validate_bundle(profile, runtime, normalized)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "campus_profile.json", profile)
    write_json(output / "runtime_config.json", runtime)
    write_json(output / "normalized_pois.json", normalized)
    write_json(output / "pending_pois.json", pending)
    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "campus": profile["campus"]["name"],
        "school": profile["school"]["name"],
        "normalizedCount": len(normalized),
        "pendingCount": len(pending),
        "validationProblems": problems,
        "status": "ready_for_asset_build" if not problems else "blocked",
        "nextStep": "connect a harvester and run semantic/heatmap builders" if not problems else "fix validation problems",
    }
    write_json(output / "build_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if problems else 0


def cmd_check(args) -> int:
    bundle = Path(args.bundle)
    profile = json.loads((bundle / "campus_profile.json").read_text(encoding="utf-8"))
    runtime = json.loads((bundle / "runtime_config.json").read_text(encoding="utf-8"))
    pois = json.loads((bundle / "normalized_pois.json").read_text(encoding="utf-8"))
    problems = validate_bundle(profile, runtime, pois)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print(f"bundle valid: {bundle}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="campus-generator")
    sub = parser.add_subparsers(dest="command", required=True)

    profile = sub.add_parser("profile", help="create a campus profile template")
    profile.add_argument("--school-id", required=True)
    profile.add_argument("--school-name", required=True)
    profile.add_argument("--school-en-name")
    profile.add_argument("--campus-name", required=True)
    profile.add_argument("--campus-slug", required=True)
    profile.add_argument("--lng", type=float, required=True)
    profile.add_argument("--lat", type=float, required=True)
    profile.add_argument("--trust-radius", type=float, default=2000)
    profile.add_argument("--coordinate-system", choices=("GCJ-02", "WGS84", "BD-09"),
                         default="GCJ-02")
    profile.add_argument("--output", type=Path, required=True)
    profile.set_defaults(func=cmd_profile)

    for name in ("validate",):
        command = sub.add_parser(name, help="validate a profile and optional candidates")
        command.add_argument("--profile", type=Path, required=True)
        command.add_argument("--candidates", type=Path)
        command.add_argument("--base-config", type=Path, default=BACKEND / "campuses.json")
        command.set_defaults(func=cmd_validate)

    normalize = sub.add_parser("normalize", help="normalize candidate POIs")
    normalize.add_argument("--profile", type=Path, required=True)
    normalize.add_argument("--candidates", type=Path, required=True)
    normalize.add_argument("--output", type=Path, required=True)
    normalize.set_defaults(func=cmd_normalize)

    harvest = sub.add_parser("harvest", help="collect candidates through an offline source adapter")
    harvest.add_argument("--input", type=Path, required=True, help="JSON candidate source")
    harvest.add_argument("--output", type=Path, required=True)
    harvest.set_defaults(func=cmd_harvest)

    amap = sub.add_parser("harvest-amap", help="collect nearby POIs from AMap Web Service")
    amap.add_argument("--profile", type=Path, required=True)
    amap.add_argument("--keyword", action="append", required=True)
    amap.add_argument("--api-key", default=None)
    amap.add_argument("--cache", type=Path)
    amap.add_argument("--ttl", type=int, default=86400)
    amap.add_argument("--max-pages", type=int, default=2)
    amap.add_argument("--page-size", type=int, default=25)
    amap.add_argument("--output", type=Path, required=True)
    amap.set_defaults(func=cmd_harvest_amap)

    web = sub.add_parser("harvest-web", help="collect structured place evidence from public HTTPS pages")
    web.add_argument("--url", action="append", required=True)
    web.add_argument("--cache", type=Path)
    web.add_argument("--ttl", type=int, default=86400)
    web.add_argument("--max-pages", type=int, default=10)
    web.add_argument("--output", type=Path, required=True)
    web.set_defaults(func=cmd_harvest_web)

    build = sub.add_parser("build", help="write an auditable campus asset bundle")
    build.add_argument("--profile", type=Path, required=True)
    build.add_argument("--candidates", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--base-config", type=Path, default=BACKEND / "campuses.json")
    build.set_defaults(func=cmd_build)

    check = sub.add_parser("check", help="recheck a generated asset bundle")
    check.add_argument("--bundle", type=Path, required=True)
    check.set_defaults(func=cmd_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
