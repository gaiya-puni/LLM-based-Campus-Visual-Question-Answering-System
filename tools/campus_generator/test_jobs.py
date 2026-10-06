"""Lifecycle, capacity and restart tests for campus build jobs."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tools.campus_generator.harvest import HarvestResult, JsonHarvester
from tools.campus_generator.jobs import (BuildJobManager, JobCapacityError,
                                         JobNotReadyError)


PROFILE = {
    "school": {"id": "demo", "name": "示例大学", "enName": "DEMO"},
    "campus": {
        "name": "示例校区",
        "slug": "demo_main",
        "center": [121.0, 31.0],
        "trustRadiusM": 1200,
    },
}


def _write_minimum_bundle(root: Path, *, report: dict | None = None) -> None:
    values = {
        "campus_profile.json": PROFILE,
        "runtime_config.json": {
            "schools": {"demo": PROFILE["school"]},
            "defaultCampus": "示例校区",
            "campuses": [dict(PROFILE["campus"], school="demo")],
        },
        "normalized_pois.json": [],
        "pending_pois.json": [],
        "approved_pois.json": [],
        "review_state.json": {"decisions": {}},
        "build_report.json": report or {
            "generatedAt": "2026-10-06T00:00:00+00:00",
            "status": "ready_for_asset_build",
            "normalizedCount": 0,
            "pendingCount": 0,
            "validationProblems": [],
            "warnings": [],
        },
    }
    root.mkdir(parents=True, exist_ok=True)
    for filename, value in values.items():
        (root / filename).write_text(
            json.dumps(value, ensure_ascii=False), encoding="utf-8",
        )


def _wait_for_terminal(manager: BuildJobManager, job_id: str) -> dict:
    for _ in range(300):
        record = manager.get(job_id)
        if record and record["status"] in {"completed", "blocked", "failed"}:
            return record
        time.sleep(0.01)
    raise AssertionError("job did not finish")


class BlockingHarvester:
    source = "blocking"

    def __init__(self, release: threading.Event):
        self.release = release

    def collect(self) -> HarvestResult:
        self.release.wait(5)
        return HarvestResult([], self.source, [])


class BuildJobPersistenceTests(unittest.TestCase):
    def test_completed_job_survives_manager_restart_with_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "profile.json"
            candidates = root / "candidates.json"
            profile.write_text(json.dumps(PROFILE, ensure_ascii=False), encoding="utf-8")
            candidates.write_text(
                json.dumps([{"name": "湖", "lng": 121.0, "lat": 31.0}],
                           ensure_ascii=False),
                encoding="utf-8",
            )
            jobs = root / "jobs"
            first = BuildJobManager(jobs, max_workers=1)
            job_id = first.submit(profile, [JsonHarvester(candidates)])
            try:
                record = _wait_for_terminal(first, job_id)
                self.assertEqual(record["status"], "completed")
                self.assertEqual(record["schemaVersion"], 1)
            finally:
                first.shutdown()

            restored = BuildJobManager(jobs, max_workers=1)
            try:
                record = restored.get(job_id)
                self.assertIsNotNone(record)
                self.assertEqual(record["status"], "completed")
                self.assertEqual(restored.output_path(job_id), jobs / job_id)
            finally:
                restored.shutdown()

    def test_interrupted_job_is_recovered_as_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = Path(directory) / "jobs"
            job_id = "a" * 32
            job_root = jobs / job_id
            job_root.mkdir(parents=True)
            (job_root / "job_state.json").write_text(json.dumps({
                "schemaVersion": 1,
                "id": job_id,
                "status": "running",
                "stage": "normalize",
                "activeOperation": "build",
                "previewStatus": "running",
                "createdAt": "2026-10-06T00:00:00+00:00",
                "updatedAt": "2026-10-06T00:00:00+00:00",
            }), encoding="utf-8")

            manager = BuildJobManager(jobs, max_workers=1)
            try:
                record = manager.get(job_id)
                self.assertEqual(record["status"], "failed")
                self.assertEqual(record["stage"], "interrupted")
                self.assertIn("backend restart", record["error"])
                self.assertEqual(record["previewStatus"], "failed")
                self.assertEqual(record["previewStage"], "interrupted")
                self.assertNotIn("activeOperation", record)
            finally:
                manager.shutdown()

    def test_interrupted_read_lease_keeps_stable_job_recoverable(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = Path(directory) / "jobs"
            job_id = "1" * 32
            job_root = jobs / job_id
            _write_minimum_bundle(job_root)
            (job_root / "job_state.json").write_text(json.dumps({
                "schemaVersion": 1,
                "id": job_id,
                "status": "completed",
                "stage": "complete",
                "activeOperation": "review-read",
                "createdAt": "2026-10-06T00:00:00+00:00",
                "updatedAt": "2026-10-06T00:00:00+00:00",
            }), encoding="utf-8")

            manager = BuildJobManager(jobs, max_workers=1)
            try:
                record = manager.get(job_id)
                self.assertEqual(record["status"], "completed")
                self.assertNotIn("activeOperation", record)
                with manager.operation(job_id, "review-read") as output:
                    self.assertEqual(output, job_root)
            finally:
                manager.shutdown()

    def test_legacy_completed_bundle_is_validated_and_discovered(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = Path(directory) / "jobs"
            job_id = "b" * 32
            job_root = jobs / job_id
            _write_minimum_bundle(job_root, report={
                "generatedAt": "2026-10-06T00:00:00+00:00",
                "status": "ready_for_asset_build",
                "normalizedCount": 4,
                "pendingCount": 1,
                "validationProblems": [],
                "warnings": ["fixture"],
                "theme": "photo",
            })

            manager = BuildJobManager(jobs, max_workers=1)
            try:
                record = manager.get(job_id)
                self.assertEqual(record["status"], "completed")
                self.assertEqual(record["candidateCount"], 5)
                self.assertEqual(record["theme"], "photo")
                self.assertEqual(record["schemaVersion"], 1)
            finally:
                manager.shutdown()
            self.assertTrue((job_root / "job_state.json").is_file())

    def test_incomplete_legacy_and_malformed_state_are_visible_as_corrupt(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = Path(directory) / "jobs"
            incomplete_id = "c" * 32
            incomplete = jobs / incomplete_id
            incomplete.mkdir(parents=True)
            (incomplete / "build_report.json").write_text("{}", encoding="utf-8")
            malformed_id = "d" * 32
            malformed = jobs / malformed_id
            malformed.mkdir(parents=True)
            (malformed / "job_state.json").write_text("{broken", encoding="utf-8")

            manager = BuildJobManager(jobs)
            try:
                for job_id in (incomplete_id, malformed_id):
                    record = manager.get(job_id)
                    self.assertIsNotNone(record)
                    self.assertEqual(record["status"], "failed")
                    self.assertEqual(record["stage"], "corrupt")
                    self.assertTrue(record["corrupt"])
                    with self.assertRaises(JobNotReadyError):
                        with manager.operation(job_id, "review"):
                            pass
            finally:
                manager.shutdown()

    def test_state_symlink_is_rejected_when_platform_allows_it(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = Path(directory) / "jobs"
            job_id = "e" * 32
            job_root = jobs / job_id
            job_root.mkdir(parents=True)
            target = Path(directory) / "outside.json"
            target.write_text(json.dumps({"id": job_id, "status": "completed"}), encoding="utf-8")
            try:
                (job_root / "job_state.json").symlink_to(target)
            except OSError:
                self.skipTest("symbolic links are unavailable")
            manager = BuildJobManager(jobs)
            try:
                record = manager.get(job_id)
                self.assertEqual(record["stage"], "corrupt")
                self.assertIn("symbolic link", record["error"])
            finally:
                manager.shutdown()


class BuildJobSafetyTests(unittest.TestCase):
    def test_capacity_reservation_blocks_competitors_and_is_consumed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidates = root / "candidates.json"
            candidates.write_text("[]", encoding="utf-8")
            manager = BuildJobManager(
                root / "jobs", max_workers=1, max_pending_tasks=1, max_jobs=2,
            )
            try:
                reservation = manager.reserve_capacity()
                with self.assertRaisesRegex(JobCapacityError, "queue is full"):
                    manager.reserve_capacity()
                with self.assertRaisesRegex(JobCapacityError, "queue is full"):
                    manager.submit_profile(PROFILE, [JsonHarvester(candidates)])
                job_id = manager.submit_profile(
                    PROFILE, [JsonHarvester(candidates)], reservation=reservation,
                )
                self.assertEqual(_wait_for_terminal(manager, job_id)["status"], "completed")
                manager.release_capacity(reservation)  # consumed tokens are safe no-ops
            finally:
                manager.shutdown()

    def test_metadata_allowlist_rejects_identity_and_status_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidates = root / "candidates.json"
            candidates.write_text("[]", encoding="utf-8")
            manager = BuildJobManager(root / "jobs")
            try:
                with self.assertRaisesRegex(ValueError, "unsupported metadata fields"):
                    manager.submit_profile(
                        PROFILE, [JsonHarvester(candidates)],
                        metadata={"id": "attacker", "status": "completed"},
                    )
                self.assertEqual(len(manager._jobs), 0)
            finally:
                manager.shutdown()

    def test_theme_metadata_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidates = root / "candidates.json"
            candidates.write_text("[]", encoding="utf-8")
            manager = BuildJobManager(root / "jobs")
            try:
                job_id = manager.submit_profile(
                    PROFILE, [JsonHarvester(candidates)], metadata={"theme": "photo"},
                )
                record = _wait_for_terminal(manager, job_id)
                self.assertEqual(record["theme"], "photo")
                report = json.loads(
                    (Path(record["output"]) / "build_report.json").read_text(encoding="utf-8")
                )
                self.assertEqual(report["theme"], "photo")
            finally:
                manager.shutdown()

    def test_web_search_metadata_is_persisted_reported_and_restored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = root / "jobs"
            candidates = root / "candidates.json"
            candidates.write_text("[]", encoding="utf-8")
            audit = {
                "schemaVersion": 1,
                "requested": True,
                "provider": "brave",
                "status": "completed",
                "queries": ["demo campus guide"],
                "results": [{
                    "url": "https://example.edu/guide",
                    "title": "Campus guide",
                    "query": "demo campus guide",
                    "rank": 1,
                }],
                "acceptedUrlCount": 1,
            }
            expected = json.loads(json.dumps(audit))
            manager = BuildJobManager(jobs)
            try:
                job_id = manager.submit_profile(
                    PROFILE,
                    [JsonHarvester(candidates)],
                    metadata={"webSearch": audit},
                )
                audit["status"] = "mutated-after-submit"
                record = _wait_for_terminal(manager, job_id)
                self.assertEqual(record["webSearch"], expected)
                state = json.loads(
                    (jobs / job_id / "job_state.json").read_text(encoding="utf-8")
                )
                report = json.loads(
                    (jobs / job_id / "build_report.json").read_text(encoding="utf-8")
                )
                self.assertEqual(state["webSearch"], expected)
                self.assertEqual(report["webSearch"], expected)
            finally:
                manager.shutdown()

            restored = BuildJobManager(jobs)
            try:
                self.assertEqual(restored.get(job_id)["webSearch"], expected)
            finally:
                restored.shutdown()

    def test_pending_queue_is_bounded_without_adding_rejected_job(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "profile.json"
            profile.write_text(json.dumps(PROFILE, ensure_ascii=False), encoding="utf-8")
            release = threading.Event()
            manager = BuildJobManager(
                root / "jobs", max_workers=1, max_pending_tasks=1, max_jobs=5,
            )
            try:
                manager.submit(profile, [BlockingHarvester(release)])
                with self.assertRaises(JobCapacityError):
                    manager.submit(profile, [BlockingHarvester(release)])
                self.assertEqual(len(manager._jobs), 1)
            finally:
                release.set()
                manager.shutdown()

    def test_total_job_count_is_bounded_without_deleting_existing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "profile.json"
            profile.write_text(json.dumps(PROFILE, ensure_ascii=False), encoding="utf-8")
            candidates = root / "candidates.json"
            candidates.write_text("[]", encoding="utf-8")
            manager = BuildJobManager(root / "jobs", max_jobs=1)
            try:
                first_id = manager.submit(profile, [JsonHarvester(candidates)])
                _wait_for_terminal(manager, first_id)
                with self.assertRaisesRegex(JobCapacityError, "job limit"):
                    manager.submit(profile, [JsonHarvester(candidates)])
                self.assertIsNotNone(manager.get(first_id))
                self.assertTrue((root / "jobs" / first_id).is_dir())
            finally:
                manager.shutdown()

    def test_executor_submission_failure_does_not_leave_memory_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "profile.json"
            profile.write_text(json.dumps(PROFILE, ensure_ascii=False), encoding="utf-8")
            candidates = root / "candidates.json"
            candidates.write_text("[]", encoding="utf-8")
            manager = BuildJobManager(root / "jobs")
            manager.shutdown()
            with self.assertRaises(JobCapacityError):
                manager.submit(profile, [JsonHarvester(candidates)])
            self.assertEqual(len(manager._jobs), 0)

    def test_unstable_job_rejects_downstream_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "profile.json"
            profile.write_text(json.dumps(PROFILE, ensure_ascii=False), encoding="utf-8")
            release = threading.Event()
            manager = BuildJobManager(root / "jobs", max_workers=1)
            try:
                job_id = manager.submit(profile, [BlockingHarvester(release)])
                with self.assertRaises(JobNotReadyError):
                    with manager.operation(job_id, "review"):
                        pass
            finally:
                release.set()
                manager.shutdown()

    def test_stable_job_operations_are_mutually_exclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = Path(directory) / "jobs"
            job_id = "f" * 32
            _write_minimum_bundle(jobs / job_id)
            manager = BuildJobManager(jobs)
            entered = threading.Event()
            finished = threading.Event()

            def contender():
                try:
                    with manager.operation(job_id, "publish"):
                        entered.set()
                except JobNotReadyError:
                    # A busy response is also a safe mutual-exclusion outcome.
                    pass
                finally:
                    finished.set()

            try:
                with manager.operation(job_id, "review"):
                    thread = threading.Thread(target=contender)
                    thread.start()
                    time.sleep(0.05)
                    self.assertFalse(entered.is_set())
                thread.join(1)
                self.assertTrue(finished.is_set())
            finally:
                manager.shutdown()

    def test_review_change_invalidates_completed_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            jobs = Path(directory) / "jobs"
            job_id = "2" * 32
            job_root = jobs / job_id
            _write_minimum_bundle(job_root)
            (job_root / "preview_report.json").write_text("{}", encoding="utf-8")
            manager = BuildJobManager(jobs)
            try:
                manager.assert_preview_ready(job_id)
                manager.invalidate_preview(job_id)
                record = manager.get(job_id)
                self.assertEqual(record["previewStatus"], "stale")
                with self.assertRaises(JobNotReadyError):
                    manager.assert_preview_ready(job_id)
            finally:
                manager.shutdown()


if __name__ == "__main__":
    unittest.main()
