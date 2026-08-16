from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class PendingImage:
    display_name: str
    source_key: str

    def __post_init__(self) -> None:
        if not self.display_name.strip():
            raise ValueError("display_name must not be empty")
        if not self.source_key.strip():
            raise ValueError("source_key must not be empty")


@dataclass(frozen=True, slots=True)
class CompletedImage:
    annotated_key: str
    model_version_id: int | None
    detections: list[dict[str, Any]]

    def __post_init__(self) -> None:
        if not self.annotated_key.strip():
            raise ValueError("annotated_key must not be empty")


@dataclass(frozen=True, slots=True)
class BatchJobView:
    id: int
    user_id: int
    status: str
    image_total: int
    success_count: int
    failure_count: int
    expires_at: datetime
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class BatchImageView:
    id: int
    job_id: int
    display_name: str
    source_key: str
    annotated_key: str | None
    status: str
    error_code: str | None
    error_message: str | None
    model_version_id: int | None
    detections: list[dict[str, Any]]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ClaimedImage:
    id: int
    job_id: int
    display_name: str
    source_key: str


@dataclass(frozen=True, slots=True)
class BatchJobPage:
    items: list[BatchJobView]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True, slots=True)
class ExpiredImage:
    id: int
    source_key: str
    annotated_key: str | None


@dataclass(frozen=True, slots=True)
class ExpiredJob:
    id: int
    images: list[ExpiredImage]
