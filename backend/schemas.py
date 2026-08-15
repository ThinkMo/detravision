from __future__ import annotations

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


class EventUpdate(BaseModel):
    status: Literal["new", "ack", "closed"]
    ack_by: int | None = None


class ModelCreate(BaseModel):
    name: str
    file_path: str
    runtime: Literal["pt", "onnx-fp32", "tensorrt-fp16"] = "onnx-fp32"
    classes: list[str]
    map50: float | None = Field(default=None, ge=0, le=1)
