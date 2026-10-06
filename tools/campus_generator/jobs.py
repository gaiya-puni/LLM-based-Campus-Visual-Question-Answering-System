"""Bounded, in-process campus build jobs for single-process local use."""

from __future__ import annotations

import copy
import json
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .formal_asset_io import atomic_write_json
from .harvest import Harvester
from .normalize import normalize_candidates
from .profile import load_profile, runtime_config, validate_profile, write_json
from .validate import validate_bundle


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobCapacityError(RuntimeError):
    """Raised when the bounded local executor cannot accept more work."""


class JobNotReadyError(RuntimeError):
    """Raised when a downstream operation targets an unstable build."""


class BuildJobManager:
    """Submit, inspect and run isolated candidate-to-bundle builds.

    This manager intentionally supports one Python process only. A per-job
    mutex keeps build/review/preview/publish operations from mixing reads and
    writes inside one process.
    """

    STATE_FILE = "job_state.json"
    STATE_SCHEMA_VERSION = 1
    STABLE_STATUSES = frozenset({"completed", "blocked"})
    JOB_STATUSES = frozenset({"queued", "running", "completed", "blocked", "failed"})
    READ_ONLY_OPERATIONS = frozenset({"review-read", "publish-plan", "preview-read"})
    METADATA_FIELDS = frozenset({
        "query", "theme", "targetCategory", "keywords", "discovery",
        "discoveryWarnings", "webSearch", "warnings",
    })
    LEGACY_BUNDLE_FILES = {
        "campus_profile.json": dict,
        "runtime_config.json": dict,
        "normalized_pois.json": list,
        "pending_pois.json": list,
        "approved_pois.json": list,
        "review_state.json": dict,
        "build_report.json": dict,
    }

    def __init__(self, root: str | Path, *, max_workers: int = 2,
                 max_pending_tasks: int = 16, max_jobs: int = 256):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_workers = max(1, min(int(max_workers), 4))
        self.max_pending_tasks = max(1, int(max_pending_tasks))
        self.max_jobs = max(1, int(max_jobs))
        self.executor = ThreadPoolExecutor(max_workers=self.max_workers)
        self._lock = threading.RLock()
        self._jobs: dict[str, dict] = {}
        self._job_locks: dict[str, threading.RLock] = {}
        self._pending_tasks = 0
        self._capacity_reservations: set[str] = set()
        self._load_existing_jobs()

    @staticmethod
    def _valid_job_id(value: str) -> bool:
        return len(value) == 32 and all(char in "0123456789abcdef" for char in value)

    def _state_path(self, job_id: str) -> Path:
        return self.root / job_id / self.STATE_FILE

    def _persist_locked(self, job_id: str) -> None:
        state_path = self._state_path(job_id)
        if state_path.parent.is_symlink() or state_path.is_symlink():
            raise OSError("job state path must not contain a symbolic link")
        atomic_write_json(state_path, self._jobs[job_id])

    @staticmethod
    def _bundle_problem(directory: Path) -> str | None:
        for filename, expected_type in BuildJobManager.LEGACY_BUNDLE_FILES.items():
            path = directory / filename
            if path.is_symlink():
                return f"{filename} must not be a symbolic link"
            if not path.is_file():
                return f"missing {filename}"
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                return f"invalid {filename}: {exc}"
            if not isinstance(value, expected_type):
                return f"{filename} must contain a JSON {expected_type.__name__}"
        return None

    def _corrupt_record(self, job_id: str, directory: Path, message: str) -> dict:
        now = _now()
        return {
            "schemaVersion": self.STATE_SCHEMA_VERSION,
            "id": job_id, "status": "failed", "stage": "corrupt",
            "createdAt": now, "updatedAt": now,
            "candidateCount": 0, "normalizedCount": 0, "pendingCount": 0,
            "warnings": [], "error": f"corrupt job state: {message}",
            "output": str(directory), "corrupt": True,
        }

    def _legacy_record(self, job_id: str, directory: Path) -> dict:
        problem = self._bundle_problem(directory)
        if problem:
            raise ValueError(f"legacy bundle is incomplete: {problem}")
        report = json.loads((directory / "build_report.json").read_text(encoding="utf-8"))
        blocked = bool(report.get("validationProblems")) or report.get("status") == "blocked"
        record = {
            "schemaVersion": self.STATE_SCHEMA_VERSION,
            "id": job_id,
            "status": "blocked" if blocked else "completed",
            "stage": "complete",
            "createdAt": str(report.get("generatedAt") or _now()),
            "updatedAt": str(report.get("generatedAt") or _now()),
            "candidateCount": int(report.get("normalizedCount", 0))
                              + int(report.get("pendingCount", 0)),
            "normalizedCount": int(report.get("normalizedCount", 0)),
            "pendingCount": int(report.get("pendingCount", 0)),
            "warnings": list(report.get("warnings") or []),
            "error": None, "output": str(directory),
        }
        for key in self.METADATA_FIELDS - {"warnings"}:
            if key in report:
                record[key] = report[key]
        if (directory / "preview_report.json").is_file():
            record.update(previewStatus="completed", previewStage="complete")
        return record

    def _load_existing_jobs(self) -> None:
        """Restore jobs; expose unsafe state as failed/corrupt instead of 404."""
        for directory in sorted(self.root.iterdir()):
            job_id = directory.name
            if directory.is_symlink() or not directory.is_dir() or not self._valid_job_id(job_id):
                continue
            state_path = directory / self.STATE_FILE
            persist = False
            try:
                if state_path.is_symlink():
                    raise ValueError("job_state.json is a symbolic link")
                if state_path.exists():
                    if not state_path.is_file():
                        raise ValueError("job_state.json is not a regular file")
                    record = json.loads(state_path.read_text(encoding="utf-8"))
                    if not isinstance(record, dict) or record.get("id") != job_id:
                        raise ValueError("job_state.json has an invalid record or id")
                    if record.get("status") not in self.JOB_STATUSES:
                        raise ValueError("job_state.json has an invalid status")
                    version = record.get("schemaVersion")
                    if version not in {None, self.STATE_SCHEMA_VERSION}:
                        raise ValueError(f"unsupported state schema version: {version!r}")
                    if version is None:
                        record["schemaVersion"] = self.STATE_SCHEMA_VERSION
                        persist = True
                else:
                    record = self._legacy_record(job_id, directory)
                    persist = True

                interrupted = record.get("status") in {"queued", "running"}
                active = record.get("activeOperation")
                preview_interrupted = record.get("previewStatus") in {"queued", "running"}
                if interrupted or active == "build":
                    record.update(
                        status="failed", stage="interrupted",
                        error="job was interrupted by a backend restart; submit it again",
                        updatedAt=_now(),
                    )
                    persist = True
                elif active and active not in self.READ_ONLY_OPERATIONS and active != "preview":
                    record.update(
                        status="failed", stage="interrupted",
                        error="a job operation was interrupted; submit a fresh build",
                        updatedAt=_now(),
                    )
                    persist = True
                elif active in self.READ_ONLY_OPERATIONS:
                    # Read leases never mutate the bundle. A crash during a GET or
                    # publish-plan must not destroy an otherwise recoverable job.
                    persist = True
                if preview_interrupted or active == "preview":
                    record.update(
                        previewStatus="failed", previewStage="interrupted",
                        previewError="preview was interrupted by a backend restart; rebuild it",
                        updatedAt=_now(),
                    )
                    persist = True
                record.pop("activeOperation", None)
                if record.get("status") in self.STABLE_STATUSES:
                    problem = self._bundle_problem(directory)
                    if problem:
                        raise ValueError(f"stable job bundle is incomplete: {problem}")
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                record = self._corrupt_record(job_id, directory, str(exc))
                # Never replace an untrusted or malformed state file automatically.
                persist = False

            self._jobs[job_id] = record
            self._job_locks[job_id] = threading.RLock()
            if persist:
                self._persist_locked(job_id)

    def _check_capacity_locked(self, *, new_job: bool,
                               reservation: str | None = None) -> None:
        owns_reservation = reservation in self._capacity_reservations
        reserved_by_others = len(self._capacity_reservations) - int(owns_reservation)
        if self._pending_tasks + reserved_by_others >= self.max_pending_tasks:
            raise JobCapacityError("campus build queue is full; try again later")
        if new_job and len(self._jobs) + reserved_by_others >= self.max_jobs:
            raise JobCapacityError("campus job limit has been reached")

    def reserve_capacity(self) -> str:
        """Atomically reserve one future build slot before paid discovery calls."""
        token = uuid.uuid4().hex
        with self._lock:
            self._check_capacity_locked(new_job=True)
            self._capacity_reservations.add(token)
        return token

    def release_capacity(self, reservation: str) -> None:
        """Release an unused reservation; consuming a token makes this a no-op."""
        with self._lock:
            self._capacity_reservations.discard(str(reservation or ""))

    def _task_done(self, _future: Future) -> None:
        with self._lock:
            self._pending_tasks = max(0, self._pending_tasks - 1)

    def _submit_new_locked(self, job_id: str, record: dict, function, *args,
                           reservation: str | None = None) -> None:
        if reservation is not None and reservation not in self._capacity_reservations:
            raise JobCapacityError("campus build capacity reservation is invalid or expired")
        self._check_capacity_locked(new_job=True, reservation=reservation)
        if reservation is not None:
            self._capacity_reservations.remove(reservation)
        self._jobs[job_id] = record
        self._job_locks[job_id] = threading.RLock()
        self._pending_tasks += 1
        try:
            self._persist_locked(job_id)
            future = self.executor.submit(function, job_id, *args)
            future.add_done_callback(self._task_done)
        except Exception as exc:
            self._pending_tasks -= 1
            self._jobs.pop(job_id, None)
            self._job_locks.pop(job_id, None)
            raise JobCapacityError(f"campus build could not be queued: {exc}") from exc

    def submit(self, profile_path: str | Path, harvesters: list[Harvester],
               existing_config: str | Path | None = None) -> str:
        if not harvesters:
            raise ValueError("at least one harvester is required")
        job_id = uuid.uuid4().hex
        record = {
            "schemaVersion": self.STATE_SCHEMA_VERSION,
            "id": job_id, "status": "queued", "activeOperation": "build",
            "createdAt": _now(), "updatedAt": _now(),
            "profile": str(profile_path),
            "sources": [getattr(item, "source", item.__class__.__name__) for item in harvesters],
            "candidateCount": 0, "normalizedCount": 0, "pendingCount": 0,
            "warnings": [], "error": None, "output": None,
        }
        with self._lock:
            self._submit_new_locked(
                job_id, record, self._run, Path(profile_path), harvesters, existing_config,
            )
        return job_id

    def submit_profile(self, profile: dict, harvesters: list[Harvester],
                       existing_config: str | Path | None = None,
                       metadata: dict | None = None,
                       *, reservation: str | None = None) -> str:
        """Submit a generated profile without exposing a caller-controlled path."""
        if not isinstance(profile, dict):
            raise ValueError("profile must be an object")
        if not harvesters:
            raise ValueError("at least one harvester is required")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        metadata = metadata or {}
        unsupported = set(metadata) - self.METADATA_FIELDS
        if unsupported:
            raise ValueError("unsupported metadata fields: " + ", ".join(sorted(unsupported)))
        job_id = uuid.uuid4().hex
        profile_path = self.root / job_id / "input_profile.json"
        record = {
            "schemaVersion": self.STATE_SCHEMA_VERSION,
            "id": job_id, "status": "queued", "activeOperation": "build",
            "createdAt": _now(), "updatedAt": _now(),
            "profile": str(profile_path),
            "sources": [getattr(item, "source", item.__class__.__name__) for item in harvesters],
            "candidateCount": 0, "normalizedCount": 0, "pendingCount": 0,
            "warnings": copy.deepcopy(metadata.get("warnings") or []),
            "error": None, "output": None,
        }
        record.update({key: copy.deepcopy(value) for key, value in metadata.items()
                       if key != "warnings"})
        with self._lock:
            if reservation is not None and reservation not in self._capacity_reservations:
                raise JobCapacityError("campus build capacity reservation is invalid or expired")
            self._check_capacity_locked(new_job=True, reservation=reservation)
            write_json(profile_path, profile)
            self._submit_new_locked(
                job_id, record, self._run, profile_path, harvesters, existing_config,
                reservation=reservation,
            )
        return job_id

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            value = self._jobs.get(job_id)
            return copy.deepcopy(value) if value else None

    def output_path(self, job_id: str) -> Path | None:
        """Return a known job directory without asserting that its bundle is stable."""
        with self._lock:
            if job_id not in self._jobs:
                return None
        return self.root / job_id

    @contextmanager
    def operation(self, job_id: str, name: str) -> Iterator[Path]:
        """Exclusively lease a stable job bundle for a downstream operation."""
        if not self._valid_job_id(job_id):
            raise ValueError("job id is invalid")
        operation_name = str(name or "").strip()
        if not operation_name:
            raise ValueError("operation name is required")
        with self._lock:
            job_lock = self._job_locks.get(job_id)
            if job_lock is None:
                raise LookupError("job not found")
            record = self._jobs[job_id]
            if record.get("status") not in self.STABLE_STATUSES:
                raise JobNotReadyError(
                    f"job is not ready for {operation_name}: {record.get('status', 'unknown')}"
                )
            if record.get("activeOperation"):
                raise JobNotReadyError(f"job is busy with {record['activeOperation']}")
        with job_lock:
            with self._lock:
                record = self._jobs.get(job_id)
                if record is None:
                    raise LookupError("job not found")
                if record.get("status") not in self.STABLE_STATUSES:
                    raise JobNotReadyError(
                        f"job is not ready for {operation_name}: {record.get('status', 'unknown')}"
                    )
                active = record.get("activeOperation")
                if active:
                    raise JobNotReadyError(f"job is busy with {active}")
                output = self.root / job_id
                problem = self._bundle_problem(output)
                if problem:
                    raise JobNotReadyError(f"job bundle is not usable: {problem}")
                record["activeOperation"] = operation_name
                record["updatedAt"] = _now()
                self._persist_locked(job_id)
            try:
                yield output
            finally:
                with self._lock:
                    current = self._jobs.get(job_id)
                    if current and current.get("activeOperation") == operation_name:
                        current.pop("activeOperation", None)
                        current["updatedAt"] = _now()
                        self._persist_locked(job_id)

    def submit_preview(self, job_id: str) -> None:
        if not self._valid_job_id(job_id):
            raise ValueError("job id is invalid")
        with self._lock:
            job_lock = self._job_locks.get(job_id)
            if job_lock is None:
                raise LookupError("job not found")
        with job_lock:
            with self._lock:
                self._check_capacity_locked(new_job=False)
                record = self._jobs[job_id]
                if record.get("status") not in self.STABLE_STATUSES:
                    raise JobNotReadyError(
                        f"job is not ready for preview: {record.get('status', 'unknown')}"
                    )
                if record.get("activeOperation"):
                    raise JobNotReadyError(f"job is busy with {record['activeOperation']}")
                output = self.root / job_id
                problem = self._bundle_problem(output)
                if problem:
                    raise JobNotReadyError(f"job bundle is not usable: {problem}")
                previous = copy.deepcopy(record)
                record.update(
                    activeOperation="preview", previewStatus="queued",
                    previewStage="queued", previewError=None, updatedAt=_now(),
                )
                self._pending_tasks += 1
                try:
                    self._persist_locked(job_id)
                    future = self.executor.submit(self._run_preview, job_id, output)
                    future.add_done_callback(self._task_done)
                except Exception as exc:
                    self._pending_tasks -= 1
                    self._jobs[job_id] = previous
                    self._persist_locked(job_id)
                    raise JobCapacityError(f"preview could not be queued: {exc}") from exc

    def invalidate_preview(self, job_id: str) -> None:
        """Mark an old preview unusable after approved review data changes."""
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                raise LookupError("job not found")
            record.pop("preview", None)
            record.update(
                previewStatus="stale", previewStage="stale", previewError=None,
                updatedAt=_now(),
            )
            self._persist_locked(job_id)

    def assert_preview_ready(self, job_id: str) -> None:
        """Reject serving stale, interrupted or never-built preview assets."""
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                raise LookupError("job not found")
            if record.get("previewStatus") != "completed":
                raise JobNotReadyError(
                    f"preview is not ready: {record.get('previewStatus', 'not-built')}"
                )

    def _run_preview(self, job_id: str, output: Path) -> None:
        with self._job_locks[job_id]:
            try:
                self._update(job_id, previewStatus="running", previewStage="prepare")
                from .preview import build_preview
                result = build_preview(output)
                self._update(job_id, previewStatus="completed", previewStage="complete",
                             preview=result, previewError=None, activeOperation=None)
            except Exception as exc:
                self._update(job_id, previewStatus="failed", previewStage="failed",
                             previewError=f"{type(exc).__name__}: {exc}", activeOperation=None)

    def _update(self, job_id: str, **values) -> None:
        with self._lock:
            record = self._jobs[job_id]
            if "activeOperation" in values and values["activeOperation"] is None:
                values.pop("activeOperation")
                record.pop("activeOperation", None)
            record.update(values, updatedAt=_now())
            self._persist_locked(job_id)

    def _run(self, job_id: str, profile_path: Path, harvesters: list[Harvester], existing_config):
        output = self.root / job_id
        with self._job_locks[job_id]:
            try:
                self._update(job_id, status="running", stage="harvest")
                profile = load_profile(profile_path)
                profile_problems = validate_profile(profile)
                if profile_problems:
                    raise ValueError("invalid profile: " + "; ".join(profile_problems))
                candidates = []
                warnings = []
                for harvester in harvesters:
                    result = harvester.collect()
                    candidates.extend(result.candidates)
                    warnings.extend(result.warnings)
                self._update(job_id, candidateCount=len(candidates), warnings=warnings,
                             stage="normalize")
                with self._lock:
                    job_theme = self._jobs[job_id].get("theme")
                normalized, pending = normalize_candidates(
                    candidates, profile["campus"]["name"], campus_profile=profile["campus"],
                    theme=str(job_theme).strip() if job_theme else None,
                )
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
                    "generatedAt": _now(), "campus": profile["campus"]["name"],
                    "school": profile["school"]["name"],
                    "normalizedCount": len(normalized), "pendingCount": len(pending),
                    "validationProblems": problems, "warnings": warnings,
                    "status": "ready_for_asset_build" if not problems else "blocked",
                }
                with self._lock:
                    metadata = dict(self._jobs[job_id])
                for key in self.METADATA_FIELDS - {"warnings"}:
                    if key in metadata:
                        report[key] = metadata[key]
                write_json(output / "build_report.json", report)
                self._update(
                    job_id, status="completed" if not problems else "blocked",
                    stage="complete", normalizedCount=len(normalized),
                    pendingCount=len(pending), output=str(output), error=None,
                    activeOperation=None,
                )
            except Exception as exc:
                self._update(
                    job_id, status="failed", stage="failed",
                    error=f"{type(exc).__name__}: {exc}", output=str(output),
                    activeOperation=None,
                )

    def shutdown(self, wait: bool = True) -> None:
        self.executor.shutdown(wait=wait)
