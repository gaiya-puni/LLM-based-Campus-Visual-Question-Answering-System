"""In-process asynchronous campus build jobs.

Jobs are deliberately bounded and write only to an isolated output directory.
The manager is suitable for a local Flask integration or a CLI worker; a
durable queue can replace it later without changing the build callable.
"""

from __future__ import annotations

import copy
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .harvest import Harvester
from .normalize import normalize_candidates
from .profile import load_profile, runtime_config, write_json
from .validate import validate_bundle


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BuildJobManager:
    """Submit, inspect and run isolated candidate-to-bundle builds."""

    def __init__(self, root: str | Path, *, max_workers: int = 2):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.executor = ThreadPoolExecutor(max_workers=max(1, min(int(max_workers), 4)))
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}

    def submit(self, profile_path: str | Path, harvesters: list[Harvester],
               existing_config: str | Path | None = None) -> str:
        if not harvesters:
            raise ValueError("at least one harvester is required")
        job_id = uuid.uuid4().hex
        record = {
            "id": job_id,
            "status": "queued",
            "createdAt": _now(),
            "updatedAt": _now(),
            "profile": str(profile_path),
            "sources": [getattr(item, "source", item.__class__.__name__) for item in harvesters],
            "candidateCount": 0,
            "normalizedCount": 0,
            "pendingCount": 0,
            "warnings": [],
            "error": None,
            "output": None,
        }
        with self._lock:
            self._jobs[job_id] = record
        self.executor.submit(self._run, job_id, Path(profile_path), harvesters, existing_config)
        return job_id

    def submit_profile(self, profile: dict, harvesters: list[Harvester],
                       existing_config: str | Path | None = None,
                       metadata: dict | None = None) -> str:
        """Submit a generated profile without exposing a caller-controlled path."""
        if not isinstance(profile, dict):
            raise ValueError("profile must be an object")
        if not harvesters:
            raise ValueError("at least one harvester is required")
        job_id = uuid.uuid4().hex
        profile_path = self.root / job_id / "input_profile.json"
        write_json(profile_path, profile)
        record = {
            "id": job_id,
            "status": "queued",
            "createdAt": _now(),
            "updatedAt": _now(),
            "profile": str(profile_path),
            "sources": [getattr(item, "source", item.__class__.__name__) for item in harvesters],
            "candidateCount": 0,
            "normalizedCount": 0,
            "pendingCount": 0,
            "warnings": list((metadata or {}).get("warnings") or []),
            "error": None,
            "output": None,
        }
        record.update({key: value for key, value in (metadata or {}).items()
                       if key not in {"warnings", "profile"}})
        with self._lock:
            self._jobs[job_id] = record
        self.executor.submit(self._run, job_id, profile_path, harvesters, existing_config)
        return job_id

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            value = self._jobs.get(job_id)
            return copy.deepcopy(value) if value else None

    def output_path(self, job_id: str) -> Path | None:
        with self._lock:
            if job_id not in self._jobs:
                return None
        return self.root / job_id

    def submit_preview(self, job_id: str) -> None:
        output = self.output_path(job_id)
        if output is None:
            raise ValueError("job not found")
        with self._lock:
            record = self._jobs[job_id]
            if record.get("previewStatus") in {"queued", "running"}:
                raise ValueError("preview build is already running")
            record.update(previewStatus="queued", previewError=None, updatedAt=_now())
        self.executor.submit(self._run_preview, job_id, output)

    def _run_preview(self, job_id: str, output: Path) -> None:
        try:
            self._update(job_id, previewStatus="running", previewStage="prepare")
            from .preview import build_preview
            result = build_preview(output)
            self._update(job_id, previewStatus="completed", previewStage="complete",
                         preview=result, previewError=None)
        except Exception as exc:
            self._update(job_id, previewStatus="failed", previewStage="failed",
                         previewError=f"{type(exc).__name__}: {exc}")

    def _update(self, job_id: str, **values) -> None:
        with self._lock:
            record = self._jobs[job_id]
            record.update(values, updatedAt=_now())

    def _run(self, job_id: str, profile_path: Path, harvesters: list[Harvester], existing_config):
        output = self.root / job_id
        try:
            self._update(job_id, status="running", stage="harvest")
            profile = load_profile(profile_path)
            candidates = []
            warnings = []
            for harvester in harvesters:
                result = harvester.collect()
                candidates.extend(result.candidates)
                warnings.extend(result.warnings)
            self._update(job_id, candidateCount=len(candidates), warnings=warnings, stage="normalize")
            normalized, pending = normalize_candidates(candidates, profile["campus"]["name"])
            existing = None
            if existing_config:
                existing = json.loads(Path(existing_config).read_text(encoding="utf-8"))
            runtime = runtime_config(profile, existing)
            problems = validate_bundle(profile, runtime, normalized)
            output.mkdir(parents=True, exist_ok=True)
            write_json(output / "campus_profile.json", profile)
            write_json(output / "runtime_config.json", runtime)
            write_json(output / "normalized_pois.json", normalized)
            write_json(output / "pending_pois.json", pending)
            auto_approved = [item for item in normalized
                             if item.get("autoApprove") or item.get("source") in
                             {"campus_registry", "scene_registry"}]
            write_json(output / "approved_pois.json", auto_approved)
            write_json(output / "review_state.json", {
                "updatedAt": _now(),
                "decisions": {str(item["id"]): "approved" for item in auto_approved},
                "approvedCount": len(auto_approved),
                "validationProblems": validate_bundle(profile, runtime, auto_approved),
            })
            report = {
                "generatedAt": _now(),
                "campus": profile["campus"]["name"],
                "school": profile["school"]["name"],
                "normalizedCount": len(normalized),
                "pendingCount": len(pending),
                "validationProblems": problems,
                "warnings": warnings,
                "status": "ready_for_asset_build" if not problems else "blocked",
            }
            with self._lock:
                metadata = dict(self._jobs[job_id])
            for key in ("query", "theme", "keywords", "discovery", "discoveryWarnings"):
                if key in metadata:
                    report[key] = metadata[key]
            write_json(output / "build_report.json", report)
            self._update(job_id, status="completed" if not problems else "blocked",
                         stage="complete", normalizedCount=len(normalized),
                         pendingCount=len(pending), output=str(output), error=None)
        except Exception as exc:  # job failures are returned through status, not raised in a worker
            self._update(job_id, status="failed", stage="failed", error=f"{type(exc).__name__}: {exc}",
                         output=str(output))

    def shutdown(self, wait: bool = True) -> None:
        self.executor.shutdown(wait=wait)
