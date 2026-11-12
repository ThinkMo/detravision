import json
from types import SimpleNamespace

import torch

from training.runtime import RuntimeRecorder


def test_observed_lr_and_amp_override_request_even_without_scheduler_metadata(tmp_path):
    """Catches storing requested auto lr/AMP when the runtime changed them."""
    trainer = SimpleNamespace(
        args=SimpleNamespace(lr0=0.01, amp=True, data=str(tmp_path)),
        optimizer=torch.optim.SGD([torch.nn.Parameter(torch.zeros(1))], lr=0.002, momentum=0.9),
        amp=False, device=torch.device("cpu"), batch_size=4, epoch=0,
    )
    path = tmp_path / "effective_config.json"
    recorder = RuntimeRecorder(path, {"advanced_config": {"lr0": 0.01, "amp": True}})
    recorder.capture(trainer)
    initial = json.loads(path.read_text("utf-8"))
    assert initial["runtime"]["advanced_config"]["lr0"] == 0.002
    assert initial["runtime"]["advanced_config"]["amp"] is False
    trainer.optimizer.param_groups[0]["lr"] = 0.0005
    trainer.optimizer.param_groups[0]["params"][0].data.fill_(float("nan"))
    trainer.epoch = 2
    recorder.capture(trainer)
    latest = json.loads(path.read_text("utf-8"))
    assert latest["runtime"]["advanced_config"]["lr0"] == 0.002
    assert latest["runtime"]["optimizer"]["param_groups"][0]["lr"] == 0.0005
    assert latest["runtime"]["epoch"] == 3
    assert str(tmp_path) not in json.dumps(latest)
    assert "params" not in latest["runtime"]["optimizer"]["param_groups"][0]
