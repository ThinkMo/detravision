from __future__ import annotations

import logging

from backend.core.config import Settings
from backend.pipeline import runtime as runtime_module
from backend.pipeline.runtime import RuntimeManager
from backend.ws import EventHub


class ActiveModelRepository:
    def __init__(self, model: dict[str, object]) -> None:
        self.model = model

    def get_active_model(self) -> dict[str, object]:
        return self.model


def test_model_reconciliation_logs_stable_fields_without_local_path(
    tmp_path,
    monkeypatch,
    caplog,
):
    """Catches model paths or exception messages leaking on failure or success."""
    secret_path = str(tmp_path / "private-model-secret.onnx")
    repository = ActiveModelRepository({"id": 17, "file_path": secret_path})
    manager = RuntimeManager(
        Settings(enable_runtime=False),
        repository,
        object(),
        EventHub(),
    )
    caplog.set_level(logging.INFO, logger=runtime_module.__name__)

    def fail_to_build(_path: str):
        raise RuntimeError(f"detector rejected {secret_path}")

    monkeypatch.setattr(runtime_module, "build_detector", fail_to_build)
    manager._reconcile_model()
    failure = [
        record
        for record in caplog.records
        if record.name == runtime_module.__name__
    ]
    assert [record.getMessage() for record in failure] == [
        "model_activation_failed model_id=17 error_type=RuntimeError"
    ]
    assert failure[0].exc_info is None
    assert secret_path not in caplog.text

    caplog.clear()
    replacement = object()
    monkeypatch.setattr(runtime_module, "build_detector", lambda _path: replacement)
    manager._reconcile_model()
    success = [
        record
        for record in caplog.records
        if record.name == runtime_module.__name__
    ]
    assert [record.getMessage() for record in success] == [
        "model_activation_succeeded model_id=17"
    ]
    assert secret_path not in caplog.text
    assert manager.detector is replacement
