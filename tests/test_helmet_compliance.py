from backend.engine import Detection
from backend.safety import assess_helmet_compliance


def test_person_with_helmet_in_head_region_is_marked_compliant():
    detections = [
        Detection("person", 0.90, (0, 0, 100, 200)),
        Detection("helmet", 0.82, (25, 5, 75, 55)),
    ]

    assert assess_helmet_compliance(detections) == [
        Detection("helmet_worn", 0.82, (0, 0, 100, 200))
    ]


def test_person_without_a_matched_helmet_is_marked_not_wearing():
    detections = [Detection("person", 0.88, (10, 20, 110, 220))]

    assert assess_helmet_compliance(detections) == [
        Detection("no_helmet", 0.88, (10, 20, 110, 220))
    ]


def test_helmet_below_the_head_region_does_not_make_person_compliant():
    detections = [
        Detection("person", 0.91, (0, 0, 100, 200)),
        Detection("helmet", 0.96, (30, 120, 70, 170)),
    ]

    assert assess_helmet_compliance(detections) == [
        Detection("no_helmet", 0.91, (0, 0, 100, 200))
    ]


def test_one_helmet_cannot_be_assigned_to_two_overlapping_people():
    detections = [
        Detection("person", 0.90, (0, 0, 100, 200)),
        Detection("person", 0.85, (40, 0, 140, 200)),
        Detection("helmet", 0.80, (65, 5, 85, 45)),
    ]

    assert assess_helmet_compliance(detections) == [
        Detection("no_helmet", 0.90, (0, 0, 100, 200)),
        Detection("helmet_worn", 0.80, (40, 0, 140, 200)),
    ]


def test_explicit_no_helmet_detection_strengthens_violation_confidence():
    detections = [
        Detection("person", 0.70, (0, 0, 100, 200)),
        Detection("no_helmet", 0.93, (30, 5, 70, 50)),
    ]

    assert assess_helmet_compliance(detections) == [
        Detection("no_helmet", 0.93, (0, 0, 100, 200))
    ]


def test_head_detections_without_a_person_do_not_create_compliance_results():
    detections = [
        Detection("helmet", 0.92, (5, 5, 30, 30)),
        Detection("no_helmet", 0.89, (40, 5, 65, 30)),
    ]

    assert assess_helmet_compliance(detections) == []
