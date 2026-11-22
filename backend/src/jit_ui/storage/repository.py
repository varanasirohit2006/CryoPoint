"""SQLite authority with immutable, checksum-verified workflow artifacts."""

import hashlib
import json
import os
import re
import threading
from pathlib import Path
from uuid import uuid4

from sqlalchemy import JSON, Column, Integer, MetaData, String, Table, create_engine, func, insert, select, update

from jit_ui.config import Settings
from jit_ui.domain.models import RunEvent, RunRecord, RunStatus, WorkflowIR, utc_now


metadata = MetaData()
runs = Table("runs", metadata, Column("run_id", String, primary_key=True), Column("record", JSON, nullable=False))
events = Table("events", metadata, Column("run_id", String, primary_key=True),
               Column("sequence", Integer, primary_key=True), Column("record", JSON, nullable=False))
workflows = Table("workflows", metadata, Column("workflow_id", String, primary_key=True),
                  Column("site_id", String, nullable=False), Column("goal_id", String, nullable=False),
                  Column("active_version", Integer, nullable=False))
versions = Table("workflow_versions", metadata, Column("workflow_id", String, primary_key=True),
                 Column("version", Integer, primary_key=True), Column("ir", JSON, nullable=False),
                 Column("artifact_path", String, nullable=False), Column("checksum", String, nullable=False))
automations = Table("automations", metadata, Column("automation_id", String, primary_key=True),
                    Column("signature", String, unique=True, nullable=True), Column("record", JSON, nullable=False))
automation_workflows = Table("automation_workflows", metadata, Column("workflow_id", String, primary_key=True),
                             Column("automation_id", String, nullable=False))


class VersionConflict(RuntimeError):
    pass


class Repository:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.data_dir = settings.data_dir.resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
        self._lock = threading.RLock()
        metadata.create_all(self.engine)

    def create_automation(self, name, site_id, goal_id, signature=None):
        with self._lock, self.engine.begin() as connection:
            if signature:
                existing = connection.execute(select(automations.c.record).where(automations.c.signature == signature)).scalar_one_or_none()
                if existing:
                    return existing
            record = {"automation_id": uuid4().hex, "name": name, "site_id": site_id,
                      "goal_id": goal_id, "created_at": utc_now().isoformat()}
            connection.execute(insert(automations).values(automation_id=record["automation_id"], signature=signature, record=record))
        return record

    def get_automation(self, identifier):
        with self.engine.connect() as connection:
            return connection.execute(select(automations.c.record).where(automations.c.automation_id == identifier)).scalar_one_or_none()

    def list_automations(self):
        with self.engine.connect() as connection:
            return connection.execute(select(automations.c.record)).scalars().all()

    def workflow_automation(self, identifier):
        with self.engine.connect() as connection:
            return connection.execute(select(automation_workflows.c.automation_id).where(
                automation_workflows.c.workflow_id == identifier)).scalar_one_or_none()

    def associate_workflow(self, workflow_id, automation_id):
        with self._lock, self.engine.begin() as connection:
            previous = connection.execute(select(automation_workflows.c.automation_id).where(
                automation_workflows.c.workflow_id == workflow_id)).scalar_one_or_none()
            if previous and previous != automation_id:
                raise ValueError("Workflow already belongs to another automation set.")
            if previous is None:
                connection.execute(insert(automation_workflows).values(workflow_id=workflow_id, automation_id=automation_id))

    def save_run(self, run: RunRecord):
        value = run.model_dump(mode="json")
        with self._lock, self.engine.begin() as connection:
            existing = connection.execute(select(runs.c.run_id).where(runs.c.run_id == run.run_id)).first()
            if existing:
                connection.execute(update(runs).where(runs.c.run_id == run.run_id).values(record=value))
            else:
                connection.execute(insert(runs).values(run_id=run.run_id, record=value))

    def get_run(self, run_id: str) -> RunRecord | None:
        with self.engine.connect() as connection:
            value = connection.execute(select(runs.c.record).where(runs.c.run_id == run_id)).scalar_one_or_none()
        return RunRecord.model_validate(value) if value else None

    def list_runs(self, limit=200) -> list[RunRecord]:
        with self.engine.connect() as connection:
            records = [RunRecord.model_validate(row[0]) for row in connection.execute(select(runs.c.record))]
        return sorted(records, key=lambda run: run.created_at, reverse=True)[:limit]

    def add_event(self, run_id: str, kind: str, data: dict) -> RunEvent:
        with self._lock, self.engine.begin() as connection:
            sequence = (connection.execute(select(func.max(events.c.sequence)).where(events.c.run_id == run_id)).scalar() or 0) + 1
            event = RunEvent(run_id=run_id, sequence=sequence, kind=kind, data=data)
            connection.execute(insert(events).values(run_id=run_id, sequence=sequence, record=event.model_dump(mode="json")))
        return event

    def list_events(self, run_id: str, after_seq=0) -> list[RunEvent]:
        with self.engine.connect() as connection:
            rows = connection.execute(select(events.c.record).where(
                events.c.run_id == run_id, events.c.sequence > after_seq).order_by(events.c.sequence)).all()
        return [RunEvent.model_validate(row[0]) for row in rows]

    def recover_interrupted(self):
        for run in self.list_runs(limit=10000):
            if run.status in (RunStatus.RUNNING, RunStatus.CANCELLING, RunStatus.AWAITING_CONFIRMATION):
                run.status = RunStatus.INTERRUPTED
                run.pending_confirmation = None
                run.error = "The previous execution process ended before completion."
                run.error_code = "PROCESS_INTERRUPTED"
                run.finished_at = utc_now()
                self.save_run(run)
                self.add_event(run.run_id, "status", {"status": run.status, "error": run.error})

    def publish(self, workflow: WorkflowIR, code: str, expected_version: int | None = None):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", workflow.workflow_id):
            raise ValueError("Invalid workflow artifact identifier.")
        with self._lock:
            active = self.get_workflow(workflow.workflow_id)
            actual = active.version if active else None
            if actual != expected_version:
                raise VersionConflict("The active workflow changed while this version was being prepared.")
            if workflow.version < self.next_version(workflow.workflow_id):
                raise VersionConflict("Workflow versions must be immutable and advance monotonically.")
            parent = self.data_dir / "workflows" / workflow.workflow_id
            parent.mkdir(parents=True, exist_ok=True)
            staged = parent / (".staging-" + uuid4().hex)
            staged.mkdir()
            serialized = workflow.model_dump_json(indent=2)
            checksum = hashlib.sha256(code.encode("utf-8")).hexdigest()
            (staged / "ir.json").write_bytes(serialized.encode("utf-8"))
            (staged / "workflow.py").write_bytes(code.encode("utf-8"))
            (staged / "manifest.json").write_text(json.dumps({"code_sha256": checksum,
                "ir_sha256": hashlib.sha256(serialized.encode()).hexdigest()}, indent=2), encoding="utf-8")
            final = parent / ("v" + str(workflow.version))
            if final.exists():
                raise VersionConflict("An immutable artifact directory already exists for this version.")
            os.replace(staged, final)
            with self.engine.begin() as connection:
                connection.execute(insert(versions).values(workflow_id=workflow.workflow_id, version=workflow.version,
                    ir=workflow.model_dump(mode="json"), artifact_path=str(final.relative_to(self.data_dir)), checksum=checksum))
                if actual is None:
                    connection.execute(insert(workflows).values(workflow_id=workflow.workflow_id,
                        site_id=workflow.site_id, goal_id=workflow.goal_id, active_version=workflow.version))
                else:
                    changed = connection.execute(update(workflows).where(workflows.c.workflow_id == workflow.workflow_id,
                        workflows.c.active_version == expected_version).values(active_version=workflow.version))
                    if changed.rowcount != 1:
                        raise VersionConflict("A newer workflow version was activated concurrently.")

    def get_workflow(self, workflow_id: str, version: int | None = None) -> WorkflowIR | None:
        with self.engine.connect() as connection:
            if version is None:
                version = connection.execute(select(workflows.c.active_version).where(
                    workflows.c.workflow_id == workflow_id)).scalar_one_or_none()
            if version is None:
                return None
            row = connection.execute(select(versions).where(versions.c.workflow_id == workflow_id,
                                                          versions.c.version == version)).mappings().first()
        if not row:
            return None
        directory = (self.data_dir / row["artifact_path"]).resolve()
        if not directory.is_relative_to(self.data_dir):
            return None
        try:
            code = (directory / "workflow.py").read_bytes()
            serialized = (directory / "ir.json").read_bytes()
            manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
            if (hashlib.sha256(code).hexdigest() != row["checksum"]
                    or manifest.get("code_sha256") != row["checksum"]
                    or hashlib.sha256(serialized).hexdigest() != manifest.get("ir_sha256")
                    or json.loads(serialized) != row["ir"]):
                return None
            return WorkflowIR.model_validate(row["ir"])
        except (OSError, ValueError):
            return None

    def list_workflows(self) -> list[WorkflowIR]:
        with self.engine.connect() as connection:
            identifiers = connection.execute(select(workflows.c.workflow_id)).scalars().all()
        return [workflow for identifier in identifiers if (workflow := self.get_workflow(identifier)) is not None]

    def list_versions(self, workflow_id: str) -> list[WorkflowIR]:
        with self.engine.connect() as connection:
            rows = connection.execute(select(versions.c.ir).where(versions.c.workflow_id == workflow_id).order_by(
                versions.c.version.desc())).scalars().all()
        return [WorkflowIR.model_validate(row) for row in rows]

    def next_version(self, workflow_id: str) -> int:
        existing = [version.version for version in self.list_versions(workflow_id)]
        parent = self.data_dir / "workflows" / workflow_id
        if parent.is_dir():
            existing.extend(int(path.name[1:]) for path in parent.iterdir()
                            if path.is_dir() and re.fullmatch(r"v\d+", path.name))
        return max(existing, default=0) + 1

    def activate(self, workflow_id: str, version: int):
        if self.get_workflow(workflow_id, version) is None:
            raise ValueError("The requested workflow version is missing or its artifact is invalid.")
        with self._lock, self.engine.begin() as connection:
            connection.execute(update(workflows).where(workflows.c.workflow_id == workflow_id).values(active_version=version))

    def artifact(self, workflow_id: str, version: int, kind: str) -> Path | None:
        if self.get_workflow(workflow_id, version) is None:
            return None
        return self.data_dir / "workflows" / workflow_id / ("v" + str(version)) / (
            "workflow.py" if kind == "python" else "ir.json")

    def close(self):
        self.engine.dispose()
