"""Offline tests for the campus generator's first-stage contracts."""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.campus_generator.boundary import (covering_radius_m, point_in_polygon,
                                              point_relation)
from tools.campus_generator.cli import main
from tools.campus_generator.harvest import HarvestResult, JsonHarvester
from tools.campus_generator.harvest import AmapPoiHarvester, HarvestCache, PublicWebHarvester
from tools.campus_generator.jobs import BuildJobManager
from tools.campus_generator.discovery import (CampusProfileResolver,
                                                DiscoveryOrchestrator,
                                                infer_theme, _scoped_keyword)
from tools.campus_generator.discovery import (CampusPlantHarvester, PlantSupplementHarvester,
                                                PlantWebHarvester, CampusSceneHarvester,
                                                SceneSupplementHarvester, SceneWebHarvester,
                                                SearchEvidenceEnricher)
from tools.campus_generator.web_search import SearchHit, SearchResult
from tools.campus_generator.publish import build_publish_plan
from tools.campus_generator import publish as publish_module
from tools.campus_generator.preview import _write_preview_pois, build_preview
from tools.campus_generator.review import apply_review, load_review
from tools.campus_generator.normalize import normalize_candidates
from tools.campus_generator.profile import runtime_config, validate_profile
from tools.campus_generator.quality import assess_candidate_quality
from tools.campus_generator.validate import validate_pois


PROFILE = {
    "school": {"id": "demo", "name": "示例大学", "enName": "DEMO"},
    "campus": {
        "name": "示例校区", "slug": "demo_main", "center": [121.0, 31.0],
        "trustRadiusM": 1200,
    },
}
REVIEW_TOKEN = "test-review-token-1234"
REVIEW_HEADERS = {"X-Review-Token": REVIEW_TOKEN}

TRUSTED_BOUNDARY = {
    "type": "Polygon",
    "coordinates": [[
        [120.995, 30.999],
        [121.020, 30.999],
        [121.020, 31.001],
        [120.995, 31.001],
        [120.995, 30.999],
    ]],
}

BOUNDARY_PROFILE = {
    "school": {"id": "demo", "name": "示例大学", "enName": "DEMO"},
    "campus": {
        "name": "示例校区", "slug": "demo_main", "center": [121.0, 31.0],
        "trustRadiusM": 500,
        "coordinateSystem": "GCJ-02",
        "boundary": TRUSTED_BOUNDARY,
        "boundarySource": "official campus outline fixture",
        "boundaryConfidence": 0.95,
    },
}


class GeneratorTests(unittest.TestCase):
    def test_profile_and_runtime_config(self):
        self.assertEqual(validate_profile(PROFILE), [])
        runtime = runtime_config(PROFILE)
        self.assertEqual(runtime["campuses"][0]["school"], "demo")
        self.assertEqual(runtime["campuses"][0]["slug"], "demo_main")

    def test_trusted_boundary_validation_and_inclusive_membership(self):
        self.assertEqual(validate_profile(BOUNDARY_PROFILE), [])
        self.assertEqual(point_relation([121.0, 31.0], TRUSTED_BOUNDARY), "inside")
        self.assertEqual(point_relation([121.020, 31.0], TRUSTED_BOUNDARY), "boundary")
        self.assertEqual(point_relation([121.0, 31.002], TRUSTED_BOUNDARY), "outside")
        self.assertTrue(point_in_polygon([121.020, 31.0], TRUSTED_BOUNDARY))
        self.assertGreater(covering_radius_m(BOUNDARY_PROFILE["campus"]), 1500)

    def test_profile_rejects_invalid_or_untrusted_boundary(self):
        missing_source = json.loads(json.dumps(BOUNDARY_PROFILE))
        missing_source["campus"].pop("boundarySource")
        self.assertIn(
            "campus.boundarySource is required when campus.boundary is present",
            validate_profile(missing_source),
        )
        open_ring = json.loads(json.dumps(BOUNDARY_PROFILE))
        open_ring["campus"]["boundary"]["coordinates"][0][-1] = [120.996, 30.999]
        self.assertIn(
            "campus.boundary outer ring must be closed",
            validate_profile(open_ring),
        )
        outside_center = json.loads(json.dumps(BOUNDARY_PROFILE))
        outside_center["campus"]["center"] = [121.1, 31.0]
        self.assertIn(
            "campus.center must be inside campus.boundary",
            validate_profile(outside_center),
        )
        missing_crs = json.loads(json.dumps(BOUNDARY_PROFILE))
        missing_crs["campus"].pop("coordinateSystem")
        self.assertIn(
            "campus.coordinateSystem must be GCJ-02 when campus.boundary is present",
            validate_profile(missing_crs),
        )
        wrong_crs = json.loads(json.dumps(BOUNDARY_PROFILE))
        wrong_crs["campus"]["coordinateSystem"] = "WGS84"
        self.assertIn(
            "campus.coordinateSystem must be GCJ-02 when campus.boundary is present",
            validate_profile(wrong_crs),
        )
        invalid_crs = json.loads(json.dumps(PROFILE))
        invalid_crs["campus"]["coordinateSystem"] = "EPSG:0"
        self.assertIn(
            "campus.coordinateSystem must be one of GCJ-02, WGS84 or BD-09",
            validate_profile(invalid_crs),
        )
        with self.assertRaisesRegex(ValueError, "boundarySource"):
            AmapPoiHarvester(
                missing_source, ["校园"], api_key="key",
                request_json=lambda _: (_ for _ in ()).throw(AssertionError("must not request")),
                max_pages=1,
            ).collect()

    def test_runtime_config_rejects_existing_campus_collision(self):
        existing = runtime_config(PROFILE)
        with self.assertRaisesRegex(ValueError, "conflicts"):
            runtime_config(PROFILE, existing)

    def test_normalization_is_stable_and_rejects_duplicates(self):
        candidates = [
            {"name": "思源湖", "lng": 121.0, "lat": 31.0, "category": "scene", "tags": ["湖泊"]},
            {"name": "思源湖", "lng": 121.0, "lat": 31.0},
            {"name": "坏点", "lng": "nan", "lat": 31.0},
        ]
        normalized, pending = normalize_candidates(candidates, "示例校区")
        self.assertEqual(len(normalized), 1)
        self.assertEqual(len(pending), 2)
        self.assertEqual(validate_pois(normalized, "示例校区"), [])
        again, _ = normalize_candidates(candidates[:1], "示例校区")
        self.assertEqual(normalized[0]["id"], again[0]["id"])

    def test_quality_scores_verified_registry_candidate_inside_boundary(self):
        normalized, pending = normalize_candidates(
            [{
                "name": "校园图书馆", "lng": 121.0, "lat": 31.0,
                "coordinateSystem": "GCJ-02", "source": "campus_registry",
                "verified": True, "confidence": 1.0, "scenes": ["study"],
                "evidence": {"provider": "campus_registry"},
            }],
            "示例校区", campus_profile=BOUNDARY_PROFILE["campus"], theme="study",
        )
        self.assertEqual(pending, [])
        candidate = normalized[0]
        self.assertEqual(candidate["confidence"], 1.0)
        self.assertEqual(candidate["sourceConfidence"], 1.0)
        self.assertEqual(
            set(candidate["quality"]),
            {"scores", "confidence", "confidenceReasons"},
        )
        self.assertEqual(candidate["quality"]["scores"]["campusEvidence"], 1.0)
        self.assertEqual(candidate["quality"]["scores"]["boundaryEvidence"], 1.0)
        self.assertGreaterEqual(candidate["quality"]["confidence"], 0.8)

    def test_quality_keeps_outside_residential_garden_pending_and_flags_risk(self):
        candidate = {
            "name": "校外住宅花园", "locationName": "校外住宅花园",
            "lng": 121.0, "lat": 31.002, "coordinateSystem": "GCJ-02",
            "source": "amap", "text": "住宅小区楼盘",
        }
        normalized, pending = normalize_candidates(
            [candidate], "示例校区", campus_profile=BOUNDARY_PROFILE["campus"],
        )
        self.assertEqual(normalized, [])
        self.assertEqual(pending[0]["reasonCode"], "outside_campus")
        quality = assess_candidate_quality(candidate, theme="photo")
        self.assertEqual(quality["scores"]["commercialRisk"], 1.0)
        self.assertLess(quality["confidence"], 0.3)

    def test_quality_rewards_explicit_cross_source_agreement(self):
        base = {
            "name": "校园湖畔", "campus": "示例校区", "source": "amap",
            "scenes": ["photo"], "evidence": {
                "provider": "Amap", "campusMembershipMethod": "polygon",
                "campusMembershipRelation": "inside",
            },
        }
        single = assess_candidate_quality(base, theme="photo")
        multiple = assess_candidate_quality(
            dict(base, sources=["amap", "public_web"]), theme="photo",
        )
        self.assertEqual(single["scores"]["crossSourceAgreement"], 0.35)
        self.assertEqual(multiple["scores"]["crossSourceAgreement"], 1.0)
        self.assertGreater(multiple["confidence"], single["confidence"])
        self.assertTrue(any("独立来源" in reason
                            for reason in multiple["confidenceReasons"]))

    def test_quality_explains_theme_and_duplicate_risk(self):
        base = {
            "name": "樱花园", "campus": "示例校区", "source": "public_web",
            "scenes": ["photo"], "tags": ["拍照"],
            "evidence": {"campusMembershipMethod": "radius_fallback"},
        }
        relevant = assess_candidate_quality(base, theme="photo")
        duplicate = assess_candidate_quality(
            dict(base, duplicateOf="existing-poi"), theme="study",
        )
        self.assertEqual(relevant["scores"]["themeRelevance"], 1.0)
        self.assertEqual(duplicate["scores"]["themeRelevance"], 0.2)
        self.assertEqual(duplicate["scores"]["duplicateRisk"], 1.0)
        self.assertLess(duplicate["confidence"], relevant["confidence"])

    def test_quality_keeps_specific_primary_source_reliability(self):
        quality = assess_candidate_quality({
            "name": "植物候选", "source": "amap_plant",
            "evidence": {"provider": "Amap"},
        })
        self.assertEqual(quality["scores"]["sourceReliability"], 0.68)

    def test_normalization_enforces_boundary_coordinate_system_and_membership(self):
        candidates = [
            {"name": "边界内远点", "lng": 121.015, "lat": 31.0,
             "coordinateSystem": "GCJ-02"},
            {"name": "多边形外近点", "lng": 121.0, "lat": 31.002,
             "coordinateSystem": "GCJ-02"},
            {"name": "边界点", "lng": 121.020, "lat": 31.0,
             "coordinateSystem": "GCJ-02"},
            {"name": "缺坐标系", "lng": 121.0, "lat": 31.0},
            {"name": "错坐标系", "lng": 121.0, "lat": 31.0,
             "coordinateSystem": "WGS84"},
        ]
        normalized, pending = normalize_candidates(
            candidates, "示例校区", campus_profile=BOUNDARY_PROFILE["campus"],
        )
        self.assertEqual({item["name"] for item in normalized}, {"边界内远点", "边界点"})
        self.assertEqual(
            {item["reasonCode"] for item in pending},
            {"outside_campus", "coordinate_system"},
        )
        evidence = normalized[0]["evidence"]
        self.assertEqual(evidence["campusMembershipMethod"], "polygon")
        self.assertEqual(evidence["boundaryCoordinateSystem"], "GCJ-02")
        self.assertTrue(evidence["boundaryHash"])

    def test_normalization_enforces_radius_fallback(self):
        normalized, pending = normalize_candidates(
            [{"name": "校外点", "lng": 121.02, "lat": 31.0}],
            "示例校区", campus_profile=PROFILE["campus"],
        )
        self.assertEqual(normalized, [])
        self.assertEqual(pending[0]["reasonCode"], "outside_campus")

    def test_radius_policy_rejects_missing_or_mismatched_coordinate_system(self):
        campus = dict(PROFILE["campus"], coordinateSystem="GCJ-02")
        normalized, pending = normalize_candidates(
            [
                {"name": "正确", "lng": 121.0, "lat": 31.0,
                 "coordinateSystem": "GCJ-02"},
                {"name": "缺失", "lng": 121.0, "lat": 31.0},
                {"name": "网页坐标", "lng": 121.0, "lat": 31.0,
                 "coordinateSystem": "WGS84"},
            ],
            "示例校区", campus_profile=campus,
        )
        self.assertEqual([item["name"] for item in normalized], ["正确"])
        self.assertEqual(normalized[0]["evidence"]["campusCoordinateSystem"], "GCJ-02")
        self.assertEqual([item["reasonCode"] for item in pending],
                         ["coordinate_system", "coordinate_system"])

    def test_legacy_radius_policy_defaults_to_gcj_without_requiring_a_label(self):
        normalized, pending = normalize_candidates(
            [
                {"name": "旧数据", "lng": 121.0, "lat": 31.0},
                {"name": "显式高德", "lng": 121.0, "lat": 31.0,
                 "coordinateSystem": "GCJ-02"},
                {"name": "网页坐标", "lng": 121.0, "lat": 31.0,
                 "coordinateSystem": "WGS84"},
            ],
            "示例校区", campus_profile=PROFILE["campus"],
        )
        self.assertEqual({item["name"] for item in normalized}, {"旧数据", "显式高德"})
        self.assertTrue(all(item["coordinateSystem"] == "GCJ-02" for item in normalized))
        self.assertEqual(pending[0]["reasonCode"], "coordinate_system")
        self.assertTrue(all(
            item["evidence"]["campusCoordinateSystem"] == "GCJ-02"
            for item in normalized
        ))

    def test_validate_pois_rechecks_boundary_after_bundle_tampering(self):
        poi = {
            "id": "tampered", "category": "scene", "subCategory": "地点",
            "name": "被篡改点", "locationName": "被篡改点",
            "lng": 121.0, "lat": 31.002, "campus": "示例校区",
            "coordinateSystem": "GCJ-02", "text": "被篡改点", "tags": [],
            "confidence": 0.8,
        }
        problems = validate_pois(
            [poi], "示例校区", campus_profile=BOUNDARY_PROFILE["campus"],
        )
        self.assertTrue(any("outside campus boundary" in item for item in problems))

    def test_cli_build_writes_auditable_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "profile.json"
            candidates = root / "candidates.json"
            output = root / "bundle"
            profile.write_text(json.dumps(PROFILE, ensure_ascii=False), encoding="utf-8")
            candidates.write_text(json.dumps([{"name": "图书馆", "lng": 121, "lat": 31}], ensure_ascii=False), encoding="utf-8")
            self.assertEqual(main(["build", "--profile", str(profile), "--candidates", str(candidates), "--output", str(output), "--base-config", str(root / "missing.json")]), 0)
            self.assertEqual(main(["check", "--bundle", str(output)]), 0)
            self.assertTrue((output / "build_report.json").exists())

    def test_json_harvester_keeps_source_and_skips_invalid_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "raw.json"
            output = Path(directory) / "harvested.json"
            source.write_text(json.dumps([{"name": "湖"}, "bad"], ensure_ascii=False), encoding="utf-8")
            result = JsonHarvester(source).collect()
            self.assertEqual(len(result.candidates), 1)
            self.assertEqual(result.candidates[0]["source"], "json")
            self.assertEqual(len(result.warnings), 1)
            self.assertEqual(main(["harvest", "--input", str(source), "--output", str(output)]), 0)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))[0]["source"], "json")

    def test_amap_harvester_filters_radius_and_uses_cache(self):
        payloads = []
        def fake_request(params):
            payloads.append(params)
            return {"status": "1", "pois": [
                {"id": "ok", "name": "湖", "type": "风景名胜", "location": "121.0,31.0", "address": "校内"},
                {"id": "far", "name": "校外", "type": "地点", "location": "122.0,32.0", "address": ""},
            ]}
        profile = {"school": {"name": "示例大学"}, "campus": {
            "name": "示例校区", "slug": "demo", "center": [121.0, 31.0], "trustRadiusM": 1000}}
        with tempfile.TemporaryDirectory() as directory:
            cache = HarvestCache(Path(directory) / "cache")
            first = AmapPoiHarvester(profile, ["湖"], api_key="key", cache=cache,
                                     request_json=fake_request, max_pages=1).collect()
            second = AmapPoiHarvester(profile, ["湖"], api_key="key", cache=cache,
                                      request_json=lambda _: (_ for _ in ()).throw(AssertionError()), max_pages=1).collect()
            self.assertEqual(len(first.candidates), 1)
            self.assertEqual(len(second.candidates), 1)
            self.assertEqual(len(payloads), 1)
            self.assertEqual(first.candidates[0]["sourceId"], "ok")
            self.assertEqual(
                first.candidates[0]["evidence"]["campusMembershipMethod"],
                "radius_fallback",
            )

    def test_amap_cache_key_includes_exact_trust_radius(self):
        calls = []

        def fake_request(params):
            calls.append(params)
            return {"status": "1", "pois": [
                {"id": "near", "name": "近点", "type": "地点",
                 "location": "121.0005,31.0", "address": ""},
            ]}

        with tempfile.TemporaryDirectory() as directory:
            cache = HarvestCache(Path(directory) / "cache")
            wide = {"school": PROFILE["school"], "campus": dict(
                PROFILE["campus"], slug="same", trustRadiusM=90,
            )}
            narrow = {"school": PROFILE["school"], "campus": dict(
                PROFILE["campus"], slug="same", trustRadiusM=10,
            )}
            first = AmapPoiHarvester(
                wide, ["地点"], api_key="key", cache=cache,
                request_json=fake_request, max_pages=1,
            ).collect()
            second = AmapPoiHarvester(
                narrow, ["地点"], api_key="key", cache=cache,
                request_json=fake_request, max_pages=1,
            ).collect()
            self.assertEqual(len(first.candidates), 1)
            self.assertEqual(second.candidates, [])
            self.assertEqual(len(calls), 2)

    def test_amap_harvester_uses_polygon_before_radius(self):
        requests_seen = []

        def fake_request(params):
            requests_seen.append(params)
            return {"status": "1", "pois": [
                {"id": "inside-far", "name": "狭长校区东门", "type": "校园地点",
                 "location": "121.015,31.0", "address": "示例大学示例校区"},
                {"id": "outside-near", "name": "校外近点", "type": "地点",
                 "location": "121.0,31.002", "address": "校外"},
                {"id": "edge", "name": "边界点", "type": "校园地点",
                 "location": "121.020,31.0", "address": "示例大学示例校区"},
            ]}

        result = AmapPoiHarvester(
            BOUNDARY_PROFILE, ["校园"], api_key="key", request_json=fake_request,
            max_pages=1,
        ).collect()
        self.assertEqual({item["sourceId"] for item in result.candidates},
                         {"inside-far", "edge"})
        self.assertGreater(requests_seen[0]["radius"], 1500)
        evidence = result.candidates[0]["evidence"]
        self.assertEqual(evidence["campusMembershipMethod"], "polygon")
        self.assertEqual(evidence["boundarySource"], "official campus outline fixture")
        self.assertEqual(evidence["boundaryConfidence"], 0.95)
        self.assertEqual(evidence["boundaryCoordinateSystem"], "GCJ-02")
        self.assertTrue(evidence["boundaryHash"])
        self.assertEqual(result.candidates[0]["coordinateSystem"], "GCJ-02")
        self.assertTrue(any("excluded 1" in warning for warning in result.warnings))

    def test_preview_audit_radius_covers_trusted_polygon(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _write_preview_pois(workspace, BOUNDARY_PROFILE, [])
            config = json.loads((workspace / "heatmap_config.json").read_text(encoding="utf-8"))
            campus = config["campuses"]["示例校区"]
            self.assertGreater(campus["auditRadiusMeters"], 1500)
            self.assertEqual(campus["boundary"], TRUSTED_BOUNDARY)
            self.assertEqual(campus["coordinateSystem"], "GCJ-02")

    def test_preview_rejects_tampered_outside_candidate_before_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "campus_profile.json").write_text(
                json.dumps(BOUNDARY_PROFILE, ensure_ascii=False), encoding="utf-8",
            )
            base = {
                "category": "scene", "subCategory": "地点", "campus": "示例校区",
                "coordinateSystem": "GCJ-02", "tags": [], "confidence": 0.8,
            }
            candidates = [
                dict(base, id="inside-1", name="校内一", locationName="校内一",
                     lng=121.0, lat=31.0, text="校内一"),
                dict(base, id="inside-2", name="校内二", locationName="校内二",
                     lng=121.001, lat=31.0, text="校内二"),
                dict(base, id="outside", name="校外", locationName="校外",
                     lng=121.0, lat=31.002, text="校外"),
            ]
            (root / "approved_pois.json").write_text(
                json.dumps(candidates, ensure_ascii=False), encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "outside campus boundary"):
                build_preview(root)

    def test_preview_rejects_non_gcj_coordinate_system(self):
        profile = json.loads(json.dumps(PROFILE))
        profile["campus"]["coordinateSystem"] = "WGS84"
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "requires GCJ-02"):
                _write_preview_pois(Path(directory), profile, [])

    def test_preview_rejects_explicit_wgs_candidate_for_legacy_campus(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "campus_profile.json").write_text(
                json.dumps(PROFILE, ensure_ascii=False), encoding="utf-8",
            )
            base = {
                "category": "scene", "subCategory": "地点", "campus": "示例校区",
                "coordinateSystem": "WGS84", "tags": [], "confidence": 0.8,
            }
            candidates = [
                dict(base, id=f"wgs-{index}", name=f"网页点{index}",
                     locationName=f"网页点{index}", lng=121.0 + index * 0.0001,
                     lat=31.0, text=f"网页点{index}")
                for index in range(3)
            ]
            (root / "approved_pois.json").write_text(
                json.dumps(candidates, ensure_ascii=False), encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "does not match campus spatial policy"):
                build_preview(root)

    def test_public_web_harvester_extracts_json_ld_without_network(self):
        html = '<script type="application/ld+json">{"@type":"Place","name":"思源湖","geo":{"latitude":31.0,"longitude":121.0}}</script>'.encode("utf-8")
        result = PublicWebHarvester(["https://example.com/place"], fetch=lambda _: html).collect()
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0]["name"], "思源湖")
        self.assertEqual(result.candidates[0]["confidence"], 0.82)
        self.assertEqual(result.candidates[0]["coordinateSystem"], "WGS84")

    def test_build_job_manager_reaches_completed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "profile.json"
            profile.write_text(json.dumps(PROFILE, ensure_ascii=False), encoding="utf-8")
            (root / "candidates.json").write_text(json.dumps([{"name":"湖", "lng":121, "lat":31}]), encoding="utf-8")
            manager = BuildJobManager(root / "jobs")
            try:
                job_id = manager.submit(profile, [JsonHarvester(root / "candidates.json")])
                for _ in range(100):
                    status = manager.get(job_id)
                    if status["status"] in {"completed", "failed", "blocked"}:
                        break
                    time.sleep(0.01)
                self.assertEqual(status["status"], "completed")
                self.assertTrue(Path(status["output"]).joinpath("build_report.json").exists())
            finally:
                manager.shutdown()

    def test_build_job_manager_passes_discovery_theme_to_quality_score(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidates_path = root / "candidates.json"
            candidates_path.write_text(json.dumps([{
                "name": "拍照草坪", "lng": 121.0, "lat": 31.0,
                "scenes": ["photo"], "source": "json", "confidence": 0.6,
            }], ensure_ascii=False), encoding="utf-8")
            manager = BuildJobManager(root / "jobs")
            try:
                job_id = manager.submit_profile(
                    PROFILE, [JsonHarvester(candidates_path)], metadata={"theme": "photo"},
                )
                for _ in range(100):
                    status = manager.get(job_id)
                    if status["status"] in {"completed", "failed", "blocked"}:
                        break
                    time.sleep(0.01)
                self.assertEqual(status["status"], "completed")
                normalized = json.loads(
                    Path(status["output"]).joinpath("normalized_pois.json").read_text(
                        encoding="utf-8",
                    )
                )
                self.assertEqual(normalized[0]["sourceConfidence"], 0.6)
                self.assertEqual(
                    normalized[0]["quality"]["scores"]["themeRelevance"], 1.0,
                )
            finally:
                manager.shutdown()

    def test_build_api_rejects_paths_outside_project(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "webapp" / "backend"))
        import server

        with patch.dict(os.environ, {"USERDATA_REVIEW_TOKEN": REVIEW_TOKEN}):
            response = server.app.test_client().post(
                "/api/campus/build", headers=REVIEW_HEADERS, json={
                    "profile": "C:/outside/profile.json",
                    "harvest": {"candidates": "C:/outside/candidates.json"},
                },
            )
        self.assertEqual(response.status_code, 400)

    def test_build_mutations_require_review_token(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "webapp" / "backend"))
        import server

        with patch.dict(os.environ, {"USERDATA_REVIEW_TOKEN": ""}):
            response = server.app.test_client().post("/api/campus/build", json={})
        self.assertEqual(response.status_code, 403)
        self.assertIn("USERDATA_REVIEW_TOKEN", response.get_json()["message"])

    def test_build_api_submits_and_reports_job(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "webapp" / "backend"))
        import server

        class FakeManager:
            def submit(self, profile, harvesters, existing_config=None):
                self.harvesters = harvesters
                return "a" * 32

            def get(self, job_id):
                return {
                    "id": job_id,
                    "status": "completed",
                    "output": "private/path",
                    "discovery": {
                        "method": "amap_geocode",
                        "resolvedSchool": "示例大学",
                        "resolvedCampus": "示例校区",
                    },
                    "discoveryWarnings": ["discovery warning"],
                    "warnings": ["harvest warning"],
                }

        profile_path = Path(__file__).resolve().parents[2] / "build" / "api-test-profile.json"
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(json.dumps(PROFILE, ensure_ascii=False), encoding="utf-8")
        original_manager = server._CAMPUS_BUILD_MANAGER
        server._CAMPUS_BUILD_MANAGER = FakeManager()
        try:
            with patch.dict(os.environ, {"USERDATA_REVIEW_TOKEN": REVIEW_TOKEN}):
                response = server.app.test_client().post(
                    "/api/campus/build", headers=REVIEW_HEADERS, json={
                        "profile": str(profile_path),
                        "harvest": {"candidates": str(profile_path)},
                    },
                )
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.get_json()["jobId"], "a" * 32)
            status = server.app.test_client().get("/api/campus/build/" + "a" * 32)
            self.assertEqual(status.status_code, 200)
            self.assertEqual(status.get_json()["output"], "a" * 32)
            self.assertNotIn("profile", status.get_json())
            self.assertEqual(status.get_json()["discovery"]["resolvedSchool"], "示例大学")
            self.assertEqual(status.get_json()["discoveryWarnings"], ["discovery warning"])
            self.assertEqual(status.get_json()["warnings"], ["harvest warning"])
        finally:
            server._CAMPUS_BUILD_MANAGER = original_manager
            profile_path.unlink(missing_ok=True)

    def test_discovery_resolves_known_campus_and_theme_keywords(self):
        resolver = CampusProfileResolver()
        profile = resolver.resolve("帮我做华东师范大学闵行校区的约会热力图")
        self.assertEqual(profile["campus"]["name"], "闵行")
        self.assertEqual(profile["school"]["name"], "华东师范大学")
        plan = DiscoveryOrchestrator(resolver).plan(
            "帮我做华东师范大学闵行校区的约会热力图",
            web_urls=["https://example.edu/campus"],
        )
        self.assertEqual(plan.theme, "date")
        self.assertIn("植物", plan.keywords)
        self.assertIn("河岸", plan.keywords)
        self.assertIn("亭子", plan.keywords)
        self.assertEqual(plan.targetCategory, "mixed")
        self.assertEqual(plan.sources, ["amap", "public_web"])

    def test_discovery_preserves_registered_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {
                "schools": {"demo": BOUNDARY_PROFILE["school"]},
                "defaultCampus": "示例校区",
                "campuses": [dict(BOUNDARY_PROFILE["campus"], school="demo", aliases=[])],
            }
            path = Path(directory) / "campuses.json"
            path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            profile = CampusProfileResolver(path).resolve("示例大学示例校区")
            self.assertEqual(profile["campus"]["boundary"], TRUSTED_BOUNDARY)
            self.assertEqual(profile["campus"]["boundaryConfidence"], 0.95)
            self.assertEqual(profile["campus"]["coordinateSystem"], "GCJ-02")

    def test_discovery_prefers_school_identity_for_same_named_campuses(self):
        resolver = CampusProfileResolver()
        sjtu = resolver.resolve("帮我做上海交通大学闵行校区适合拍照的热力图")
        ecnu = resolver.resolve("帮我做华东师范大学闵行校区适合拍照的热力图")
        self.assertEqual(sjtu["school"]["id"], "sjtu")
        self.assertEqual(sjtu["campus"]["slug"], "sjtu_minhang")
        self.assertEqual(ecnu["school"]["id"], "ecnu")
        self.assertEqual(ecnu["campus"]["slug"], "minhang")

    def test_amap_keyword_uses_official_school_and_campus_phrase(self):
        profile = CampusProfileResolver().resolve("上海交通大学闵行校区")
        self.assertEqual(_scoped_keyword(profile, "湖泊"), "上海交通大学 闵行校区 湖泊")

    def test_sjtu_registry_loads_campus_specific_photo_scenes(self):
        profile = CampusProfileResolver().resolve("上海交通大学闵行校区")
        result = CampusSceneHarvester(profile, "photo").collect()
        names = {item["locationName"] for item in result.candidates}
        self.assertGreaterEqual(len(result.candidates), 10)
        self.assertIn("涵泽湖", names)
        self.assertIn("上海交通大学闵行校区李政道图书馆", names)
        self.assertNotIn("东湖面馆(龙坡路)", names)
        self.assertFalse(any("充电站" in name for name in names))
        self.assertTrue(all(item["source"] == "scene_registry" for item in result.candidates))

    def test_known_campus_uses_plant_and_scene_sources(self):
        resolver = CampusProfileResolver()
        plan = DiscoveryOrchestrator(resolver).plan("华东师范大学闵行校区的约会热力图", include_web=False)
        harvesters = DiscoveryOrchestrator(resolver).harvesters(plan, include_web=False)
        self.assertIsInstance(harvesters[0], CampusPlantHarvester)
        self.assertIsInstance(harvesters[1], CampusSceneHarvester)
        self.assertIsInstance(harvesters[2], PlantSupplementHarvester)
        self.assertIsInstance(harvesters[3], SceneSupplementHarvester)
        registry = harvesters[0].collect()
        scene_registry = harvesters[1].collect()
        self.assertGreater(len(registry.candidates), 1000)
        self.assertTrue(all(item["category"] == "plant" for item in registry.candidates))
        self.assertTrue(all(item["coordinateSystem"] == "GCJ-02"
                            for item in registry.candidates))
        self.assertTrue(scene_registry.candidates)
        self.assertTrue(all(item["category"] == "scene" for item in scene_registry.candidates))
        self.assertTrue(all(item["coordinateSystem"] == "GCJ-02"
                            for item in scene_registry.candidates))
        self.assertTrue(all("date" in item["scenes"] for item in scene_registry.candidates))
        supplement = harvesters[2]
        supplement.api_key = "key"
        supplement.request_json = lambda _: {"status": "1", "pois": [
            {"id": "lake", "name": "涵泽湖", "type": "风景名胜", "location": "121.45,31.03"},
            {"id": "tree", "name": "樱花园", "type": "绿地", "location": "121.45,31.03",
             "address": "华东师范大学闵行校区"},
            {"id": "shop", "name": "一路繁花鲜花店", "type": "购物服务", "location": "121.45,31.03"},
        ]}
        result = supplement.collect()
        self.assertTrue(result.candidates)
        self.assertTrue(all(item["category"] == "plant" for item in result.candidates))
        self.assertTrue(all(item["name"] != "涵泽湖" for item in result.candidates))
        self.assertTrue(all("鲜花店" not in item["name"] for item in result.candidates))
        self.assertIn("樱花园", [item["name"] for item in result.candidates])

        scene_supplement = harvesters[3]
        scene_supplement.api_key = "key"
        scene_supplement.request_json = lambda _: {"status": "1", "pois": [
            {"id": "river", "name": "涵泽湖河岸", "type": "风景名胜", "location": "121.45,31.03",
             "address": "华东师范大学闵行校区"},
            {"id": "pavilion", "name": "校园亭子", "type": "风景名胜", "location": "121.45,31.03"},
            {"id": "mall", "name": "校外商场", "type": "购物服务", "location": "121.45,31.03"},
        ]}
        scenes = scene_supplement.collect().candidates
        self.assertEqual({item["name"] for item in scenes}, {"涵泽湖河岸", "校园亭子"})
        self.assertTrue(all(item["scenes"] == ["date"] for item in scenes))

    def test_plant_web_harvester_keeps_only_explicit_noncommercial_plants(self):
        html = """<script type="application/ld+json">[
          {"@type":"Place","name":"校园樱花园","geo":{"latitude":31.0,"longitude":121.0}},
          {"@type":"Place","name":"涵泽湖","geo":{"latitude":31.0,"longitude":121.0}},
          {"@type":"Store","name":"一路繁花鲜花店","geo":{"latitude":31.0,"longitude":121.0}}
        ]</script>""".encode("utf-8")
        harvester = PlantWebHarvester(["https://example.com/plants"])
        harvester.delegate.fetch = lambda _: html
        harvester.delegate._custom_fetch = True
        result = harvester.collect()
        self.assertEqual([item["name"] for item in result.candidates], ["校园樱花园"])
        self.assertEqual(result.candidates[0]["category"], "plant")

    def test_walk_scene_harvester_keeps_sports_and_riverside_places(self):
        profile = {"school": {"name": "示例大学"}, "campus": {
            "name": "示例校区", "slug": "demo", "center": [121.0, 31.0], "trustRadiusM": 1000}}
        harvester = SceneSupplementHarvester(profile, "walk", ["运动场", "体育馆", "河岸"], api_key="key",
                                              max_pages=1)
        harvester.request_json = lambda _: {"status": "1", "pois": [
            {"id": "stadium", "name": "示例大学体育馆", "type": "体育场馆", "location": "121.0,31.0"},
            {"id": "river", "name": "校园河岸步道", "type": "风景名胜", "location": "121.0,31.0"},
            {"id": "hotel", "name": "河岸宾馆", "type": "住宿服务", "location": "121.0,31.0"},
        ]}
        result = harvester.collect()
        self.assertEqual({item["name"] for item in result.candidates},
                         {"示例大学体育馆", "校园河岸步道"})
        self.assertTrue(all(item["scenes"] == ["walk"] for item in result.candidates))

    def test_scene_filter_uses_amap_keyword_and_trusted_distance(self):
        profile = {"school": {"name": "示例大学"}, "campus": {
            "name": "示例校区", "slug": "demo", "center": [121.0, 31.0], "trustRadiusM": 1000}}
        harvester = SceneSupplementHarvester(profile, "photo", ["湖泊"], api_key="key", max_pages=1)
        harvester.request_json = lambda _: {"status": "1", "pois": [
            {"id": "lake", "name": "思源湖", "type": "风景名胜", "location": "121.0,31.0",
             "address": "示例大学示例校区"},
            {"id": "mall", "name": "湖泊广场购物中心", "type": "购物服务", "location": "121.0,31.0",
             "address": "闵行区"},
        ]}
        result = harvester.collect()
        self.assertEqual([item["name"] for item in result.candidates], ["思源湖"])

    def test_scene_web_harvester_uses_candidate_fields_not_page_prose(self):
        html = """<p>页面介绍了校园河岸和亭子</p><script type="application/ld+json">[
          {"@type":"Place","name":"校园河岸","geo":{"latitude":31.0,"longitude":121.0}},
          {"@type":"Place","name":"行政楼","geo":{"latitude":31.0,"longitude":121.0}},
          {"@type":"Store","name":"校外购物中心","geo":{"latitude":31.0,"longitude":121.0}}
        ]</script>""".encode("utf-8")
        harvester = SceneWebHarvester(["https://example.com/campus"], "date")
        harvester.delegate.fetch = lambda _: html
        harvester.delegate._custom_fetch = True
        result = harvester.collect()
        self.assertEqual([item["name"] for item in result.candidates], ["校园河岸"])
        self.assertEqual(result.candidates[0]["scenes"], ["date"])

    def test_publish_plan_rejects_unregistered_campus_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = {"school": PROFILE["school"], "campus": dict(PROFILE["campus"], name="未登记校区")}
            (root / "campus_profile.json").write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
            (root / "approved_pois.json").write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "configuration review"):
                build_publish_plan(root)

    def test_publish_plan_splits_plant_and_scene_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            formal = root / "formal"
            formal.mkdir()
            campuses = formal / "campuses.json"
            plants = formal / "campus_pois.json"
            scenes = formal / "scene_pois.json"
            campuses.write_text(json.dumps({
                "campuses": [dict(PROFILE["campus"], school="demo",
                                  coordinateSystem="GCJ-02")],
            }, ensure_ascii=False), encoding="utf-8")
            plants.write_text("[]", encoding="utf-8")
            scenes.write_text("[]", encoding="utf-8")
            (root / "campus_profile.json").write_text(json.dumps(PROFILE, ensure_ascii=False), encoding="utf-8")
            base = {"locationName": "校内", "lng": 121.0, "lat": 31.0, "campus": "示例校区",
                    "coordinateSystem": "GCJ-02", "text": "校内地点", "tags": [],
                    "confidence": 0.8, "verified": True}
            approved = [
                dict(base, id="plant_new", category="plant", subCategory="樱花", name="樱花园"),
                dict(base, id="scene_new", category="scene", subCategory="运动场地", name="运动场",
                     scenes=["walk"]),
            ]
            (root / "approved_pois.json").write_text(json.dumps(approved, ensure_ascii=False), encoding="utf-8")
            with patch.object(publish_module, "FORMAL_CAMPUSES_PATH", campuses), \
                    patch.object(publish_module, "FORMAL_POI_PATH", plants), \
                    patch.object(publish_module, "FORMAL_SCENE_PATH", scenes):
                plan = build_publish_plan(root)
                wgs_candidate = dict(approved[1], coordinateSystem="WGS84")
                (root / "approved_pois.json").write_text(
                    json.dumps([wgs_candidate], ensure_ascii=False), encoding="utf-8",
                )
                wgs_plan = build_publish_plan(root)
            self.assertEqual([item["id"] for item in plan["plantAdded"]], ["plant_new"])
            self.assertEqual([item["id"] for item in plan["sceneAdded"]], ["scene_new"])
            self.assertEqual(len(plan["added"]), 2)
            self.assertTrue(plan["campusPolicyHash"])
            self.assertTrue(any("does not match campus spatial policy" in item
                                for item in wgs_plan["validationProblems"]))

    def test_publish_uses_formal_boundary_and_hashes_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            formal = root / "formal"
            formal.mkdir()
            campuses = formal / "campuses.json"
            plants = formal / "campus_pois.json"
            scenes = formal / "scene_pois.json"
            formal_campus = json.loads(json.dumps(BOUNDARY_PROFILE["campus"]))
            formal_campus["school"] = "demo"
            campuses.write_text(
                json.dumps({"campuses": [formal_campus]}, ensure_ascii=False),
                encoding="utf-8",
            )
            plants.write_text("[]", encoding="utf-8")
            # The job profile is intentionally wider. Publication must trust
            # the formal campus policy instead of this task-local profile.
            (root / "campus_profile.json").write_text(
                json.dumps(PROFILE, ensure_ascii=False), encoding="utf-8",
            )
            outside = {
                "id": "outside", "category": "scene", "subCategory": "地点",
                "name": "校外近点", "locationName": "校外近点",
                "lng": 121.0, "lat": 31.002, "campus": "示例校区",
                "coordinateSystem": "GCJ-02", "text": "校外近点", "tags": [],
                "confidence": 0.8, "verified": True,
            }
            scenes.write_text(json.dumps([
                dict(outside, name="校内旧点", locationName="校内旧点",
                     lat=31.0, text="校内旧点"),
            ], ensure_ascii=False), encoding="utf-8")
            (root / "approved_pois.json").write_text(
                json.dumps([outside], ensure_ascii=False), encoding="utf-8",
            )
            with patch.object(publish_module, "FORMAL_CAMPUSES_PATH", campuses), \
                    patch.object(publish_module, "FORMAL_POI_PATH", plants), \
                    patch.object(publish_module, "FORMAL_SCENE_PATH", scenes):
                blocked = build_publish_plan(root)
                self.assertTrue(any("outside campus boundary" in item
                                    for item in blocked["validationProblems"]))
                self.assertEqual([item["id"] for item in blocked["updated"]], ["outside"])
                first_hash = blocked["afterHash"]
                first_assets = blocked["afterHashes"]
                formal_campus["boundary"]["coordinates"][0][1][0] = 121.021
                campuses.write_text(
                    json.dumps({"campuses": [formal_campus]}, ensure_ascii=False),
                    encoding="utf-8",
                )
                changed_policy = build_publish_plan(root)
            self.assertEqual(changed_policy["afterHashes"], first_assets)
            self.assertNotEqual(changed_policy["afterHash"], first_hash)
            self.assertNotEqual(
                changed_policy["campusPolicyHash"], blocked["campusPolicyHash"],
            )

    def test_publish_rechecks_formal_policy_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            formal = root / "formal"
            formal.mkdir()
            campuses = formal / "campuses.json"
            plants = formal / "campus_pois.json"
            scenes = formal / "scene_pois.json"
            backup = formal / "backups"
            campuses.write_text(
                json.dumps({"campuses": [dict(PROFILE["campus"], school="demo")]},
                           ensure_ascii=False),
                encoding="utf-8",
            )
            plants.write_text("[]", encoding="utf-8")
            scenes.write_text("[]", encoding="utf-8")
            (root / "campus_profile.json").write_text(
                json.dumps(PROFILE, ensure_ascii=False), encoding="utf-8",
            )
            approved = {
                "id": "new", "category": "scene", "subCategory": "地点",
                "name": "校内点", "locationName": "校内点", "lng": 121.0,
                "lat": 31.0, "campus": "示例校区", "text": "校内点", "tags": [],
                "confidence": 0.8, "verified": True,
            }
            (root / "approved_pois.json").write_text(
                json.dumps([approved], ensure_ascii=False), encoding="utf-8",
            )
            with patch.object(publish_module, "FORMAL_CAMPUSES_PATH", campuses), \
                    patch.object(publish_module, "FORMAL_POI_PATH", plants), \
                    patch.object(publish_module, "FORMAL_SCENE_PATH", scenes), \
                    patch.object(publish_module, "BACKUP_ROOT", backup):
                with self.assertRaisesRegex(ValueError, "requires expectedAfterHash"):
                    publish_module.publish(root, confirm=True)
                expected = build_publish_plan(root)
                planned_policy = expected["campusPolicyHash"]
                with patch.object(publish_module, "_campus_policy_hash",
                                  side_effect=[planned_policy, "changed-policy"]):
                    with self.assertRaisesRegex(ValueError, "formal campus policy changed"):
                        publish_module.publish(
                            root, confirm=True, expected_after_hash=expected["afterHash"],
                        )
            self.assertEqual(json.loads(scenes.read_text(encoding="utf-8")), [])
            self.assertFalse(backup.exists())

    def test_publish_rolls_back_if_campus_policy_changes_during_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            formal = root / "formal"
            formal.mkdir()
            campuses = formal / "campuses.json"
            plants = formal / "campus_pois.json"
            scenes = formal / "scene_pois.json"
            backups = formal / "backups"
            transaction = formal / ".campus_publish_transaction.json"
            campuses.write_text(json.dumps({
                "campuses": [dict(PROFILE["campus"], school="demo",
                                  coordinateSystem="GCJ-02")],
            }, ensure_ascii=False), encoding="utf-8")
            plants.write_text("[]", encoding="utf-8")
            scenes.write_text("[]", encoding="utf-8")
            (root / "campus_profile.json").write_text(
                json.dumps(PROFILE, ensure_ascii=False), encoding="utf-8",
            )
            approved = {
                "id": "new-scene", "category": "scene", "subCategory": "place",
                "name": "Scene", "locationName": "Campus point", "lng": 121.0,
                "lat": 31.0, "campus": PROFILE["campus"]["name"],
                "coordinateSystem": "GCJ-02", "text": "Campus point", "tags": [],
                "confidence": 0.8, "verified": True,
            }
            (root / "approved_pois.json").write_text(
                json.dumps([approved], ensure_ascii=False), encoding="utf-8",
            )
            with patch.object(publish_module, "FORMAL_CAMPUSES_PATH", campuses), \
                    patch.object(publish_module, "FORMAL_POI_PATH", plants), \
                    patch.object(publish_module, "FORMAL_SCENE_PATH", scenes), \
                    patch.object(publish_module, "BACKUP_ROOT", backups):
                plan = build_publish_plan(root)
                with patch.object(
                        publish_module, "_current_campus_policy_hash",
                        side_effect=[plan["campusPolicyHash"], "changed-policy"],
                ):
                    with self.assertRaisesRegex(ValueError, "changed during publication"):
                        publish_module.publish(
                            root, confirm=True, expected_after_hash=plan["afterHash"],
                        )
            self.assertEqual(json.loads(plants.read_text(encoding="utf-8")), [])
            self.assertEqual(json.loads(scenes.read_text(encoding="utf-8")), [])
            self.assertFalse(transaction.exists())

    def test_publish_atomically_writes_confirmed_plan_and_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            formal = root / "formal"
            formal.mkdir()
            campuses = formal / "campuses.json"
            plants = formal / "campus_pois.json"
            scenes = formal / "scene_pois.json"
            backups = formal / "backups"
            transaction = formal / ".campus_publish_transaction.json"
            campuses.write_text(json.dumps({
                "campuses": [dict(PROFILE["campus"], school="demo")],
            }, ensure_ascii=False), encoding="utf-8")
            plants.write_text("[]", encoding="utf-8")
            scenes.write_text("[]", encoding="utf-8")
            scene_mode = stat.S_IMODE(scenes.stat().st_mode)
            (root / "campus_profile.json").write_text(
                json.dumps(PROFILE, ensure_ascii=False), encoding="utf-8",
            )
            approved = {
                "id": "new-scene", "category": "scene", "subCategory": "地点",
                "name": "校内点", "locationName": "校内点", "lng": 121.0,
                "lat": 31.0, "campus": "示例校区", "text": "校内点", "tags": [],
                "confidence": 0.8, "verified": True,
            }
            (root / "approved_pois.json").write_text(
                json.dumps([approved], ensure_ascii=False), encoding="utf-8",
            )
            with patch.object(publish_module, "FORMAL_CAMPUSES_PATH", campuses), \
                    patch.object(publish_module, "FORMAL_POI_PATH", plants), \
                    patch.object(publish_module, "FORMAL_SCENE_PATH", scenes), \
                    patch.object(publish_module, "BACKUP_ROOT", backups):
                plan = build_publish_plan(root)
                result = publish_module.publish(
                    root, confirm=True, expected_after_hash=plan["afterHash"],
                )
            self.assertTrue(result["published"])
            self.assertEqual([item["id"] for item in json.loads(
                scenes.read_text(encoding="utf-8"))], ["new-scene"])
            self.assertEqual(stat.S_IMODE(scenes.stat().st_mode), scene_mode)
            backup_dirs = [path for path in backups.iterdir() if path.is_dir()]
            self.assertEqual(len(backup_dirs), 1)
            self.assertEqual(
                json.loads((backup_dirs[0] / "scene_pois.json").read_text(encoding="utf-8")),
                [],
            )
            self.assertTrue((backup_dirs[0] / "publish_plan.json").exists())
            self.assertFalse(transaction.exists())

    def test_publish_plan_fails_closed_for_corrupt_formal_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            formal = root / "formal"
            formal.mkdir()
            campuses = formal / "campuses.json"
            plants = formal / "campus_pois.json"
            scenes = formal / "scene_pois.json"
            campuses.write_text(json.dumps({
                "campuses": [dict(PROFILE["campus"], school="demo")],
            }, ensure_ascii=False), encoding="utf-8")
            plants.write_text("{broken", encoding="utf-8")
            scenes.write_text("[]", encoding="utf-8")
            (root / "campus_profile.json").write_text(
                json.dumps(PROFILE, ensure_ascii=False), encoding="utf-8",
            )
            (root / "approved_pois.json").write_text("[]", encoding="utf-8")
            with patch.object(publish_module, "FORMAL_CAMPUSES_PATH", campuses), \
                    patch.object(publish_module, "FORMAL_POI_PATH", plants), \
                    patch.object(publish_module, "FORMAL_SCENE_PATH", scenes):
                with self.assertRaisesRegex(ValueError, "not valid JSON"):
                    build_publish_plan(root)

    def test_publish_rolls_back_both_assets_when_second_write_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            formal = root / "formal"
            formal.mkdir()
            campuses = formal / "campuses.json"
            plants = formal / "campus_pois.json"
            scenes = formal / "scene_pois.json"
            backups = formal / "backups"
            transaction = formal / ".campus_publish_transaction.json"
            campuses.write_text(json.dumps({
                "campuses": [dict(PROFILE["campus"], school="demo",
                                  coordinateSystem="GCJ-02")],
            }, ensure_ascii=False), encoding="utf-8")
            plants.write_text("[]", encoding="utf-8")
            scenes.write_text("[]", encoding="utf-8")
            (root / "campus_profile.json").write_text(
                json.dumps(PROFILE, ensure_ascii=False), encoding="utf-8",
            )
            base = {
                "subCategory": "place", "locationName": "Campus point",
                "lng": 121.0, "lat": 31.0, "campus": PROFILE["campus"]["name"],
                "coordinateSystem": "GCJ-02", "text": "Campus point", "tags": [],
                "confidence": 0.8, "verified": True,
            }
            approved = [
                dict(base, id="new-plant", category="plant", name="Plant"),
                dict(base, id="new-scene", category="scene", name="Scene"),
            ]
            (root / "approved_pois.json").write_text(
                json.dumps(approved, ensure_ascii=False), encoding="utf-8",
            )
            with patch.object(publish_module, "FORMAL_CAMPUSES_PATH", campuses), \
                    patch.object(publish_module, "FORMAL_POI_PATH", plants), \
                    patch.object(publish_module, "FORMAL_SCENE_PATH", scenes), \
                    patch.object(publish_module, "BACKUP_ROOT", backups):
                plan = build_publish_plan(root)
                real_write = publish_module.atomic_write_json

                def fail_scene_write(path, value):
                    if Path(path).resolve() == scenes.resolve():
                        raise OSError("simulated second asset write failure")
                    return real_write(path, value)

                with patch.object(publish_module, "atomic_write_json",
                                  side_effect=fail_scene_write):
                    with self.assertRaisesRegex(OSError, "simulated"):
                        publish_module.publish(
                            root, confirm=True, expected_after_hash=plan["afterHash"],
                        )
            self.assertEqual(json.loads(plants.read_text(encoding="utf-8")), [])
            self.assertEqual(json.loads(scenes.read_text(encoding="utf-8")), [])
            self.assertFalse(transaction.exists())

    def test_publish_recovers_interrupted_mixed_asset_transaction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            formal = root / "formal"
            formal.mkdir()
            campuses = formal / "campuses.json"
            plants = formal / "campus_pois.json"
            scenes = formal / "scene_pois.json"
            backups = formal / "backups"
            backup = backups / "prepared"
            backup.mkdir(parents=True)
            transaction = formal / ".campus_publish_transaction.json"
            campuses.write_text(json.dumps({
                "campuses": [dict(PROFILE["campus"], school="demo",
                                  coordinateSystem="GCJ-02")],
            }, ensure_ascii=False), encoding="utf-8")
            plants.write_text("[]", encoding="utf-8")
            scenes.write_text("[]", encoding="utf-8")
            (backup / plants.name).write_text("[]", encoding="utf-8")
            (backup / scenes.name).write_text("[]", encoding="utf-8")
            (root / "campus_profile.json").write_text(
                json.dumps(PROFILE, ensure_ascii=False), encoding="utf-8",
            )
            base = {
                "subCategory": "place", "locationName": "Campus point",
                "lng": 121.0, "lat": 31.0, "campus": PROFILE["campus"]["name"],
                "coordinateSystem": "GCJ-02", "text": "Campus point", "tags": [],
                "confidence": 0.8, "verified": True,
            }
            approved = [
                dict(base, id="new-plant", category="plant", name="Plant"),
                dict(base, id="new-scene", category="scene", name="Scene"),
            ]
            (root / "approved_pois.json").write_text(
                json.dumps(approved, ensure_ascii=False), encoding="utf-8",
            )
            with patch.object(publish_module, "FORMAL_CAMPUSES_PATH", campuses), \
                    patch.object(publish_module, "FORMAL_POI_PATH", plants), \
                    patch.object(publish_module, "FORMAL_SCENE_PATH", scenes), \
                    patch.object(publish_module, "BACKUP_ROOT", backups):
                plan = build_publish_plan(root)
                plants.write_text(
                    json.dumps(plan["plantAdded"], ensure_ascii=False), encoding="utf-8",
                )
                journal = {
                    "version": 2,
                    "state": "prepared",
                    "backup": str(backup.resolve()),
                    "campus": plan["campus"],
                    "campusPolicyHash": plan["campusPolicyHash"],
                    "targets": {
                        "plants": str(plants.resolve()),
                        "scenes": str(scenes.resolve()),
                        "campuses": str(campuses.resolve()),
                    },
                    "beforeHashes": plan["beforeHashes"],
                    "afterHashes": plan["afterHashes"],
                }
                transaction.write_text(
                    json.dumps(journal, ensure_ascii=False), encoding="utf-8",
                )
                mixed_plants = plants.read_text(encoding="utf-8")
                (backup / plants.name).write_text("{broken", encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "backup is invalid"):
                    with publish_module.formal_assets_guard():
                        pass
                self.assertEqual(plants.read_text(encoding="utf-8"), mixed_plants)
                self.assertTrue(transaction.exists())
                (backup / plants.name).write_text("[]", encoding="utf-8")
                with publish_module.formal_assets_guard() as recovered:
                    pass
                self.assertEqual(recovered, "rolled-back")

                plants.write_text(
                    json.dumps(plan["plantAdded"], ensure_ascii=False), encoding="utf-8",
                )
                scenes.write_text(
                    json.dumps(plan["sceneAdded"], ensure_ascii=False), encoding="utf-8",
                )
                changed_campus = dict(
                    PROFILE["campus"], school="demo", coordinateSystem="GCJ-02",
                    trustRadiusM=900,
                )
                campuses.write_text(
                    json.dumps({"campuses": [changed_campus]}, ensure_ascii=False),
                    encoding="utf-8",
                )
                transaction.write_text(
                    json.dumps(journal, ensure_ascii=False), encoding="utf-8",
                )
                with publish_module.formal_assets_guard() as policy_recovery:
                    pass
            self.assertEqual(policy_recovery, "rolled-back")
            self.assertEqual(json.loads(plants.read_text(encoding="utf-8")), [])
            self.assertEqual(json.loads(scenes.read_text(encoding="utf-8")), [])
            self.assertFalse(transaction.exists())

    def test_publish_rejects_school_and_cross_campus_id_conflicts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            formal = root / "formal"
            formal.mkdir()
            campuses = formal / "campuses.json"
            plants = formal / "campus_pois.json"
            scenes = formal / "scene_pois.json"
            campuses.write_text(json.dumps({
                "campuses": [dict(PROFILE["campus"], school="other-school")],
            }, ensure_ascii=False), encoding="utf-8")
            plants.write_text("[]", encoding="utf-8")
            scenes.write_text("[]", encoding="utf-8")
            (root / "campus_profile.json").write_text(
                json.dumps(PROFILE, ensure_ascii=False), encoding="utf-8",
            )
            approved = {
                "id": "shared", "category": "scene", "subCategory": "地点",
                "name": "校内点", "locationName": "校内点", "lng": 121.0,
                "lat": 31.0, "campus": "示例校区", "text": "校内点", "tags": [],
                "confidence": 0.8,
            }
            (root / "approved_pois.json").write_text(
                json.dumps([approved], ensure_ascii=False), encoding="utf-8",
            )
            with patch.object(publish_module, "FORMAL_CAMPUSES_PATH", campuses), \
                    patch.object(publish_module, "FORMAL_POI_PATH", plants), \
                    patch.object(publish_module, "FORMAL_SCENE_PATH", scenes):
                with self.assertRaisesRegex(ValueError, "belongs to school"):
                    build_publish_plan(root)
                campuses.write_text(json.dumps({
                    "campuses": [dict(PROFILE["campus"], school="demo")],
                }, ensure_ascii=False), encoding="utf-8")
                scenes.write_text(json.dumps([
                    dict(approved, campus="另一个校区", name="原地点",
                         locationName="原地点", text="原地点"),
                ], ensure_ascii=False), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "already owned by campus"):
                    build_publish_plan(root)
                scenes.write_text("[]", encoding="utf-8")
                plants.write_text(json.dumps([
                    dict(approved, category="plant"),
                ], ensure_ascii=False), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "owned by the plant asset"):
                    build_publish_plan(root)

    def test_discovery_geocodes_unknown_campus(self):
        calls = []

        def fake_request(params):
            calls.append(params)
            return {"status": "1", "geocodes": [{
                "location": "120.1,30.2", "formatted_address": "示例大学新校区"
            }]}

        resolver = CampusProfileResolver(api_key="key", request_json=fake_request)
        profile = resolver.resolve("示例大学新校区")
        self.assertEqual(profile["campus"]["center"], [120.1, 30.2])
        self.assertEqual(profile["campus"]["coordinateSystem"], "GCJ-02")
        self.assertEqual(profile["discovery"]["method"], "amap_geocode")
        self.assertEqual(len(calls), 1)

    def test_infer_theme_defaults_to_general(self):
        self.assertEqual(infer_theme("做校园地点热力图"), "general")

    def test_review_approves_and_revalidates_candidate_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "campus_profile.json").write_text(json.dumps(PROFILE, ensure_ascii=False), encoding="utf-8")
            (root / "build_report.json").write_text(
                json.dumps({"theme": "photo"}, ensure_ascii=False), encoding="utf-8",
            )
            (root / "normalized_pois.json").write_text(json.dumps([
                {"id": "p1", "name": "湖", "locationName": "湖", "lng": 121.0, "lat": 31.0,
                 "campus": "示例校区", "category": "scene", "subCategory": "湖泊",
                 "text": "适合拍照的湖景", "tags": ["拍照"], "confidence": 0.8,
                 "source": "amap"}
            ], ensure_ascii=False), encoding="utf-8")
            result = apply_review(root, [{"id": "p1", "action": "approve",
                                          "patch": {"lat": 31.0001, "name": "新湖"}}])
            self.assertEqual(result["approvedCount"], 1)
            approved = json.loads((root / "approved_pois.json").read_text(encoding="utf-8"))
            self.assertEqual(approved[0]["name"], "新湖")
            self.assertTrue(approved[0]["verified"])
            self.assertEqual(approved[0]["quality"]["scores"]["themeRelevance"], 0.82)
            self.assertGreaterEqual(approved[0]["quality"]["scores"]["campusEvidence"], 0.9)
            reviewed = load_review(root)["candidates"][0]
            self.assertEqual(reviewed["reviewStatus"], "approved")
            self.assertEqual(reviewed["name"], "新湖")
            self.assertEqual(reviewed["lat"], 31.0001)
            apply_review(root, [{"id": "p1", "action": "approve"}])
            approved_again = json.loads(
                (root / "approved_pois.json").read_text(encoding="utf-8")
            )
            self.assertEqual(approved_again[0]["name"], "新湖")
            self.assertEqual(approved_again[0]["lat"], 31.0001)

    def test_review_rejects_outside_boundary_edit_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "campus_profile.json").write_text(
                json.dumps(BOUNDARY_PROFILE, ensure_ascii=False), encoding="utf-8",
            )
            candidate = {
                "id": "p1", "name": "校内点", "locationName": "校内点",
                "lng": 121.0, "lat": 31.0, "coordinateSystem": "GCJ-02",
                "campus": "示例校区", "category": "scene", "subCategory": "地点",
                "text": "校内点", "tags": [], "confidence": 0.8, "source": "amap",
            }
            (root / "normalized_pois.json").write_text(
                json.dumps([candidate], ensure_ascii=False), encoding="utf-8",
            )
            approved_path = root / "approved_pois.json"
            state_path = root / "review_state.json"
            approved_path.write_text("[]\n", encoding="utf-8")
            state_path.write_text('{"decisions": {}}\n', encoding="utf-8")
            before_approved = approved_path.read_text(encoding="utf-8")
            before_state = state_path.read_text(encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "outside campus boundary"):
                apply_review(root, [{"id": "p1", "action": "approve",
                                     "patch": {"lat": 31.002}}])
            self.assertEqual(approved_path.read_text(encoding="utf-8"), before_approved)
            self.assertEqual(state_path.read_text(encoding="utf-8"), before_state)

    def test_build_api_review_endpoint(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "webapp" / "backend"))
        import server

        class FakeManager:
            def __init__(self, output):
                self.output = output
                self.preview_job = None
            def output_path(self, job_id):
                return self.output if job_id == "b" * 32 else None
            def submit_preview(self, job_id):
                self.preview_job = job_id

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "campus_profile.json").write_text(json.dumps(PROFILE, ensure_ascii=False), encoding="utf-8")
            (root / "normalized_pois.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")
            old = server._CAMPUS_BUILD_MANAGER
            server._CAMPUS_BUILD_MANAGER = FakeManager(root)
            try:
                client = server.app.test_client()
                response = client.get("/api/campus/build/" + "b" * 32 + "/review")
                self.assertEqual(response.status_code, 200)
                with patch.dict(os.environ, {"USERDATA_REVIEW_TOKEN": REVIEW_TOKEN}):
                    response = client.post(
                        "/api/campus/build/" + "b" * 32 + "/review",
                        headers=REVIEW_HEADERS, json={"decisions": []},
                    )
                    self.assertEqual(response.status_code, 200)
                    response = client.post(
                        "/api/campus/build/" + "b" * 32 + "/preview",
                        headers=REVIEW_HEADERS,
                    )
                    self.assertEqual(response.status_code, 202)
                self.assertEqual(server._CAMPUS_BUILD_MANAGER.preview_job, "b" * 32)
                cache = root / "preview_heatmap_cache"
                cache.mkdir()
                (cache / "demo_main_walk.json").write_text(json.dumps({"available": True}), encoding="utf-8")
                response = client.get("/api/campus/build/" + "b" * 32 + "/preview/walk")
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.get_json()["preview"]["available"])
            finally:
                server._CAMPUS_BUILD_MANAGER = old

    def test_discovery_plan_merges_auto_search_and_manual_urls_with_audit(self):
        class StaticResolver:
            @staticmethod
            def resolve(_query):
                return PROFILE

        class FakeSearch:
            provider = "brave"

            def __init__(self):
                self.queries = []

            def search(self, queries):
                self.queries = list(queries)
                query = self.queries[0]
                return SearchResult([
                    SearchHit(
                        "https://manual.example/page", "Manual duplicate", "duplicate",
                        "brave", query, 1,
                    ),
                    SearchHit(
                        "https://search.example/campus", "Campus guide", "useful result",
                        "brave", query, 2,
                    ),
                ], "brave", self.queries, [])

        search = FakeSearch()
        plan = DiscoveryOrchestrator(
            StaticResolver(), web_search=search,
        ).plan(
            "demo campus photo",
            web_urls=["https://manual.example/page"],
            include_amap=False,
            include_web=True,
            auto_search=True,
        )

        self.assertTrue(search.queries)
        self.assertEqual(plan.web_urls, [
            "https://manual.example/page",
            "https://search.example/campus",
        ])
        self.assertEqual([item["url"] for item in plan.searchResults], [
            "https://manual.example/page",
            "https://search.example/campus",
        ])
        self.assertEqual(plan.sources, ["web_search", "public_web"])
        self.assertEqual(plan.webSearch["provider"], "brave")
        self.assertEqual(plan.webSearch["status"], "completed")
        self.assertEqual(plan.webSearch["queries"], search.queries)
        self.assertEqual(plan.webSearch["acceptedUrlCount"], 2)
        self.assertEqual(plan.webSearch["results"], [
            {
                "url": "https://manual.example/page",
                "title": "Manual duplicate",
                "query": search.queries[0],
                "rank": 1,
            },
            {
                "url": "https://search.example/campus",
                "title": "Campus guide",
                "query": search.queries[0],
                "rank": 2,
            },
        ])

    def test_search_evidence_enricher_preserves_gcj02_and_merges_sources(self):
        candidate = {
            "name": "Mirror Lake",
            "locationName": "Mirror Lake",
            "lng": 121.123,
            "lat": 31.456,
            "coordinateSystem": "GCJ-02",
            "source": "amap_scene",
            "sources": ["amap_scene"],
            "sourceUrls": ["https://official.example/map"],
            "evidence": {"provider": "Amap", "address": "Demo campus"},
        }

        class Delegate:
            source = "amap_scene"

            @staticmethod
            def collect():
                return HarvestResult([candidate], "amap_scene", ["fixture warning"])

        hit = {
            "provider": "brave",
            "query": "demo campus Mirror Lake",
            "rank": 1,
            "url": "https://guide.example/mirror-lake",
            "title": "Mirror Lake campus guide",
            "snippet": "Photo guide for Mirror Lake",
        }
        result = SearchEvidenceEnricher(Delegate(), [hit]).collect()
        enriched = result.candidates[0]
        before_agreement = assess_candidate_quality(candidate)["scores"]["crossSourceAgreement"]
        after_agreement = assess_candidate_quality(enriched)["scores"]["crossSourceAgreement"]

        self.assertEqual((enriched["lng"], enriched["lat"]), (121.123, 31.456))
        self.assertEqual(enriched["coordinateSystem"], "GCJ-02")
        self.assertEqual(enriched["source"], "amap_scene")
        self.assertEqual(enriched["sources"], ["amap_scene"])
        self.assertEqual(enriched["sourceUrls"], ["https://official.example/map"])
        self.assertEqual(enriched["evidence"]["provider"], "Amap")
        self.assertEqual(enriched["evidence"]["searchLeads"], [hit])
        self.assertEqual(result.warnings, ["fixture warning"])
        self.assertNotIn("searchLeads", candidate["evidence"])
        self.assertEqual(after_agreement, before_agreement)

    def test_discovery_api_degrades_when_search_key_is_missing(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "webapp" / "backend"))
        import server

        profile = json.loads(json.dumps(PROFILE))
        profile["discovery"] = {"method": "local_config", "query": "demo campus"}

        class StaticResolver:
            @staticmethod
            def resolve(_query):
                return profile

        class FakeManager:
            metadata = None

            @staticmethod
            def reserve_capacity():
                return "reservation"

            @staticmethod
            def release_capacity(_reservation):
                pass

            def submit_profile(self, _profile, _harvesters, _existing=None, *,
                               metadata=None, reservation=None):
                self.assert_reservation = reservation
                self.metadata = metadata
                return "c" * 32

        manager = FakeManager()
        server._rate_limit_buckets.clear()
        with patch.object(server, "_CAMPUS_BUILD_MANAGER", manager), \
                patch.object(server, "CampusProfileResolver", return_value=StaticResolver()), \
                patch.dict(os.environ, {
                    "USERDATA_REVIEW_TOKEN": REVIEW_TOKEN,
                    "WEB_SEARCH_PROVIDER": "brave",
                    "BRAVE_SEARCH_API_KEY": "",
                }):
            response = server.app.test_client().post(
                "/api/campus/discover", headers=REVIEW_HEADERS, json={
                    "query": "demo campus",
                    "includeAmap": False,
                    "includeWeb": True,
                    "autoSearch": True,
                },
            )

        self.assertEqual(response.status_code, 202)
        payload = response.get_json()
        self.assertEqual(payload["webSearch"]["provider"], "brave")
        self.assertEqual(payload["webSearch"]["status"], "unconfigured")
        self.assertTrue(payload["webSearch"]["requested"])
        self.assertIn("BRAVE_SEARCH_API_KEY", " ".join(payload["warnings"]))
        self.assertEqual(manager.metadata["webSearch"], payload["webSearch"])
        self.assertEqual(manager.metadata["discoveryWarnings"], payload["warnings"])
        self.assertEqual(manager.assert_reservation, "reservation")

    def test_discovery_api_rejects_unknown_search_provider(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "webapp" / "backend"))
        import server

        class FakeManager:
            called = False

            def submit_profile(self, *_args, **_kwargs):
                self.called = True
                return "d" * 32

        manager = FakeManager()
        server._rate_limit_buckets.clear()
        with patch.object(server, "_CAMPUS_BUILD_MANAGER", manager), patch.dict(os.environ, {
            "USERDATA_REVIEW_TOKEN": REVIEW_TOKEN,
            "WEB_SEARCH_PROVIDER": "bogus-provider",
        }):
            response = server.app.test_client().post(
                "/api/campus/discover", headers=REVIEW_HEADERS, json={
                    "query": "demo campus",
                    "includeAmap": False,
                    "includeWeb": True,
                    "autoSearch": True,
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("WEB_SEARCH_PROVIDER", response.get_json()["message"])
        self.assertIn("bogus-provider", response.get_json()["message"])
        self.assertFalse(manager.called)

    def test_discovery_api_checks_capacity_before_metered_search(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "webapp" / "backend"))
        import server

        class FullManager:
            @staticmethod
            def reserve_capacity():
                raise server.JobCapacityError("campus build queue is full")

            @staticmethod
            def release_capacity(_reservation):
                raise AssertionError("no reservation was created")

        class SearchMustNotRun:
            provider = "tavily"
            called = False

            def search(self, _queries):
                self.called = True
                raise AssertionError("metered search must not run")

        search = SearchMustNotRun()
        server._rate_limit_buckets.clear()
        with patch.object(server, "_CAMPUS_BUILD_MANAGER", FullManager()), \
                patch.object(server, "WebSearchClient", return_value=search), \
                patch.dict(os.environ, {
                    "USERDATA_REVIEW_TOKEN": REVIEW_TOKEN,
                    "WEB_SEARCH_PROVIDER": "tavily",
                    "TAVILY_API_KEY": "configured-test-key",
                }):
            response = server.app.test_client().post(
                "/api/campus/discover", headers=REVIEW_HEADERS, json={
                    "query": "华东师范大学闵行校区拍照热力图",
                    "includeAmap": False,
                    "includeWeb": True,
                    "autoSearch": True,
                },
            )

        self.assertEqual(response.status_code, 503)
        self.assertFalse(search.called)

    def test_discovery_api_reports_missing_amap_key_before_queueing(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "webapp" / "backend"))
        import server
        old_key = os.environ.pop("AMAP_WEB_SERVICE_KEY", None)
        try:
            with patch.dict(os.environ, {"USERDATA_REVIEW_TOKEN": REVIEW_TOKEN}):
                response = server.app.test_client().post(
                    "/api/campus/discover", headers=REVIEW_HEADERS, json={
                        "query": "华东师范大学闵行校区的约会热力图",
                    },
                )
            self.assertEqual(response.status_code, 400)
            self.assertIn("AMAP_WEB_SERVICE_KEY", response.get_json()["message"])
        finally:
            if old_key is not None:
                os.environ["AMAP_WEB_SERVICE_KEY"] = old_key


if __name__ == "__main__":
    unittest.main()
