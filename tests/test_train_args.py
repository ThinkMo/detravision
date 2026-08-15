import sys

from training.train import parse_args


def test_training_defaults_avoid_windows_worker_spawns_and_ram_cache(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["train"])

    args = parse_args()

    assert args.workers == 0
    assert args.cache is False


def test_training_allows_workers_and_cache_to_be_overridden(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["train", "--workers", "2", "--cache", "ram"])

    args = parse_args()

    assert args.workers == 2
    assert args.cache == "ram"
