"""Canonical object and local paths for a pipeline job."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class JobPaths:
    job_id: str

    @property
    def prefix(self) -> str:
        return f"jobs/{self.job_id}"

    def raw(self, name: str) -> str:
        return f"{self.prefix}/raw/{name}"

    def merged(self) -> str:
        return f"{self.prefix}/merged/merged.csv"

    def normalized(self, part: str = "0") -> str:
        return f"{self.prefix}/normalized/part={part}.parquet"

    def matching(self) -> str:
        return f"{self.prefix}/matching/candidates.jsonl"

    def result(self, name: str = "result.jsonl") -> str:
        return f"{self.prefix}/result/{name}"

    def success(self, stage: str) -> str:
        return f"{self.prefix}/{stage}/_SUCCESS"

    def local_root(self, work_dir: str | Path) -> Path:
        return Path(work_dir) / self.job_id

    def local_stage(self, work_dir: str | Path, stage: str) -> Path:
        return self.local_root(work_dir) / stage
