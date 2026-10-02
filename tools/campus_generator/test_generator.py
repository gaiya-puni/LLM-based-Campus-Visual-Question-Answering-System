"""Offline tests for the campus generator's first-stage contracts."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.campus_generator.cli import main
from tools.campus_generator.harvest import JsonHarvester
from tools.campus_generator.harvest import AmapPoiHarvester, HarvestCache, PublicWebHarvester
from tools.campus_generator.jobs import BuildJobManager
from tools.campus_generator.discovery import (CampusProfileResolver,
                                                DiscoveryOrchestrator,
                                                infer_theme, _scoped_keyword)
from tools.campus_generator.discovery import (CampusPlantHarvester, PlantSupplementHarvester,
                                                PlantWebHarvester, CampusSceneHarvester,
                                                SceneSupplementHarvester, SceneWebHarvester)
from tools.campus_generator.publish import build_publish_plan
from tools.campus_generator import publish as publish_module
from tools.campus_generator.review import apply_review, load_review
from tools.campus_generator.normalize import normalize_candidates
from tools.campus_generator.profile import runtime_config, validate_profile
from tools.campus_generator.validate import validate_pois


PROFILE = {
    "school": {"id": "demo", "name": "示例大学", "enName": "DEMO"},
    "campus": {
        "name": "示例校区", "slug": "demo_main", "center": [121.0, 31.0],
        "trustRadiusM": 1200,
    },
}


class GeneratorTests(unittest.TestCase):
    def test_profile_and_runtime_config(self):
        self.assertEqual(validate_profile(PROFILE), [])
        runtime = runtime_config(PROFILE)
        self.assertEqual(runtime["campuses"][0]["school"], "demo")
        self.assertEqual(runtime["campuses"][0]["slug"], "demo_main")

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

    def test_public_web_harvester_extracts_json_ld_without_network(self):
        html = '<script type="application/ld+json">{"@type":"Place","name":"思源湖","geo":{"latitude":31.0,"longitude":121.0}}</script>'.encode("utf-8")
        result = PublicWebHarvester(["https://example.com/place"], fetch=lambda _: html).collect()
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0]["name"], "思源湖")
        self.assertEqual(result.candidates[0]["confidence"], 0.82)

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

    def test_build_api_rejects_paths_outside_project(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "webapp" / "backend"))
        import server

        response = server.app.test_client().post("/api/campus/build", json={
            "profile": "C:/outside/profile.json",
            "harvest": {"candidates": "C:/outside/candidates.json"},
        })
        self.assertEqual(response.status_code, 400)

    def test_build_api_submits_and_reports_job(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "webapp" / "backend"))
        import server

        class FakeManager:
            def submit(self, profile, harvesters, existing_config=None):
                self.harvesters = harvesters
                return "a" * 32

            def get(self, job_id):
                return {"id": job_id, "status": "completed", "output": "private/path"}

        profile_path = Path(__file__).resolve().parents[2] / "build" / "api-test-profile.json"
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(json.dumps(PROFILE, ensure_ascii=False), encoding="utf-8")
        original_manager = server._CAMPUS_BUILD_MANAGER
        server._CAMPUS_BUILD_MANAGER = FakeManager()
        try:
            response = server.app.test_client().post("/api/campus/build", json={
                "profile": str(profile_path),
                "harvest": {"candidates": str(profile_path)},
            })
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.get_json()["jobId"], "a" * 32)
            status = server.app.test_client().get("/api/campus/build/" + "a" * 32)
            self.assertEqual(status.status_code, 200)
            self.assertEqual(status.get_json()["output"], "a" * 32)
            self.assertNotIn("profile", status.get_json())
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
        self.assertTrue(scene_registry.candidates)
        self.assertTrue(all(item["category"] == "scene" for item in scene_registry.candidates))
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
            campuses.write_text(json.dumps({"campuses": [{"name": "示例校区"}]}, ensure_ascii=False), encoding="utf-8")
            plants.write_text("[]", encoding="utf-8")
            scenes.write_text("[]", encoding="utf-8")
            (root / "campus_profile.json").write_text(json.dumps(PROFILE, ensure_ascii=False), encoding="utf-8")
            base = {"locationName": "校内", "lng": 121.0, "lat": 31.0, "campus": "示例校区",
                    "text": "校内地点", "tags": [], "confidence": 0.8, "verified": True}
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
            self.assertEqual([item["id"] for item in plan["plantAdded"]], ["plant_new"])
            self.assertEqual([item["id"] for item in plan["sceneAdded"]], ["scene_new"])
            self.assertEqual(len(plan["added"]), 2)

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
        self.assertEqual(profile["discovery"]["method"], "amap_geocode")
        self.assertEqual(len(calls), 1)

    def test_infer_theme_defaults_to_general(self):
        self.assertEqual(infer_theme("做校园地点热力图"), "general")

    def test_review_approves_and_revalidates_candidate_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "campus_profile.json").write_text(json.dumps(PROFILE, ensure_ascii=False), encoding="utf-8")
            (root / "normalized_pois.json").write_text(json.dumps([
                {"id": "p1", "name": "湖", "locationName": "湖", "lng": 121.0, "lat": 31.0,
                 "campus": "示例校区", "category": "scene", "subCategory": "湖泊",
                 "text": "湖", "tags": [], "confidence": 0.8, "source": "amap"}
            ], ensure_ascii=False), encoding="utf-8")
            result = apply_review(root, [{"id": "p1", "action": "approve",
                                          "patch": {"lat": 31.0001, "name": "新湖"}}])
            self.assertEqual(result["approvedCount"], 1)
            approved = json.loads((root / "approved_pois.json").read_text(encoding="utf-8"))
            self.assertEqual(approved[0]["name"], "新湖")
            self.assertTrue(approved[0]["verified"])
            self.assertEqual(load_review(root)["candidates"][0]["reviewStatus"], "approved")

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
                response = client.post("/api/campus/build/" + "b" * 32 + "/review",
                                       json={"decisions": []})
                self.assertEqual(response.status_code, 200)
                response = client.post("/api/campus/build/" + "b" * 32 + "/preview")
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

    def test_discovery_api_reports_missing_amap_key_before_queueing(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "webapp" / "backend"))
        import server
        old_key = os.environ.pop("AMAP_WEB_SERVICE_KEY", None)
        try:
            response = server.app.test_client().post("/api/campus/discover", json={
                "query": "华东师范大学闵行校区的约会热力图",
            })
            self.assertEqual(response.status_code, 400)
            self.assertIn("AMAP_WEB_SERVICE_KEY", response.get_json()["message"])
        finally:
            if old_key is not None:
                os.environ["AMAP_WEB_SERVICE_KEY"] = old_key


if __name__ == "__main__":
    unittest.main()
