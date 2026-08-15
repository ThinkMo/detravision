from backend.engine import Detection
from backend.rules import RuleEngine


def test_no_helmet_is_associated_with_head_region_and_debounced():
    engine = RuleEngine(required_hits=3, window_seconds=5, cooldown_seconds=30)
    detections = [
        Detection("person", 0.9, (0, 0, 100, 200)),
        Detection("no_helmet", 0.8, (25, 10, 75, 60)),
    ]
    assert engine.evaluate(1, detections, now=0) == []
    assert engine.evaluate(1, detections, now=1) == []
    result = engine.evaluate(1, detections, now=2)
    assert len(result) == 1
    assert result[0].event_type == "no_helmet"
    assert engine.evaluate(1, detections, now=3) == []


def test_roi_filters_fire_outside_polygon():
    engine = RuleEngine(required_hits=1)
    fire = [Detection("fire", 0.95, (200, 200, 220, 220))]
    result = engine.evaluate(1, fire, roi=[[0, 0], [100, 0], [100, 100], [0, 100]], now=0)
    assert result == []
