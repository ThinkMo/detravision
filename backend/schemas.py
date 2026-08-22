from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CameraCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    rtsp_url: str = Field(min_length=1)
    enabled: bool = False
    sample_fps: float = Field(default=1.0, gt=0, le=30)
    roi: list[list[float]] = Field(default_factory=list)
    detect_classes: list[str] = Field(default_factory=lambda: ["no_helmet", "fire", "smoke"])
    alarm_group_id: int | None = None


class CameraUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=100)
    rtsp_url: str | None = Field(default=None, min_length=1)
    enabled: bool | None = None
    sample_fps: float | None = Field(default=None, gt=0, le=30)
    roi: list[list[float]] | None = None
    detect_classes: list[str] | None = None
    alarm_group_id: int | None = None


class PersonCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    phone: str | None = None
    feishu_id: str | None = None
    dept: str | None = None


class PersonUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    phone: str | None = None
    feishu_id: str | None = None
    dept: str | None = None


class AlarmGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    person_ids: list[int] = Field(default_factory=list)


class AlarmGroupUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=100)
    person_ids: list[int] | None = None


class EventUpdate(BaseModel):
    status: Literal["new", "ack", "closed"]
    ack_by: int | None = None


class ModelCreate(BaseModel):
    name: str
    file_path: str
    runtime: Literal["pt", "onnx-fp32", "tensorrt-fp16"] = "onnx-fp32"
    classes: list[str]
    map50: float | None = Field(default=None, ge=0, le=1)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=1024)


class UserCreate(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9_.-]{3,100}$")
    password: str = Field(min_length=10, max_length=1024)
    role: Literal["admin", "user"] = "user"


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["admin", "user"] | None = None
    enabled: bool | None = None


class CurrentUserResponse(BaseModel):
    id: int
    username: str
    role: Literal["admin", "user"]
    must_change_password: bool


class UserResponse(CurrentUserResponse):
    enabled: bool
    created_at: datetime
    updated_at: datetime


class TemporaryPasswordResponse(BaseModel):
    temporary_password: str


class BatchJobResponse(BaseModel):
    id: int
    user_id: int
    status: str
    image_total: int
    success_count: int
    failure_count: int
    expires_at: datetime
    created_at: datetime
    updated_at: datetime


class BatchImageResponse(BaseModel):
    id: int
    display_name: str
    status: str
    error_code: str | None
    error_message: str | None
    model_version_id: int | None
    detections: list[dict[str, object]]
    preview_url: str
    result_url: str
    created_at: datetime
    updated_at: datetime


class BatchJobDetailResponse(BatchJobResponse):
    images: list[BatchImageResponse]


class BatchJobPageResponse(BaseModel):
    items: list[BatchJobResponse]
    total: int
    page: int
    page_size: int


class TrainingJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_key: str = Field(min_length=9, max_length=100)
    executor: str = "local"
    base_model: str
    imgsz: Literal[640, 960] = 640
    epochs: int = Field(default=100, ge=1, le=1000)
    batch: Literal["1", "2", "4", "8", "auto"] = "auto"
    device: Literal["auto", "cpu", "0"] = "auto"
    patience: int = Field(default=50, ge=0, le=500)


class TrainingModelRegister(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)


class TrainingDatasetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    root_alias: str = Field(min_length=1, max_length=64)
    directory: str = Field(min_length=1, max_length=512)
    train_split: str = Field(min_length=1, max_length=512)
    val_split: str = Field(min_length=1, max_length=512)
    test_split: str = Field(min_length=1, max_length=512)
    classes: list[str] = Field(min_length=1, max_length=100)


class TrainingDatasetUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    root_alias: str | None = Field(default=None, min_length=1, max_length=64)
    directory: str | None = Field(default=None, min_length=1, max_length=512)
    train_split: str | None = Field(default=None, min_length=1, max_length=512)
    val_split: str | None = Field(default=None, min_length=1, max_length=512)
    test_split: str | None = Field(default=None, min_length=1, max_length=512)
    classes: list[str] | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None


class DatasetRescanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    split: Literal["train", "val", "test"] | None = None


class AnnotationBoxRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    class_id: int = Field(ge=0)
    x_center: float = Field(ge=0, le=1)
    y_center: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class AnnotationSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    boxes: list[AnnotationBoxRequest] = Field(min_length=1, max_length=1000)


class RevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)


class DatasetClassCreateRequest(RevisionRequest):
    name: str = Field(min_length=1, max_length=100)


class DatasetClassRenameRequest(RevisionRequest):
    name: str = Field(min_length=1, max_length=100)


class DatasetPublishRequest(RevisionRequest):
    confirm_warnings: bool = False
