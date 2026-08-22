from __future__ import annotations

from backend.engine import Detection


def _center(detection: Detection) -> tuple[float, float]:
    x1, y1, x2, y2 = detection.bbox
    return ((x1 + x2) / 2, (y1 + y2) / 2)


def _head_region(person: Detection) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = person.bbox
    return (x1, y1, x2, y1 + (y2 - y1) * 0.4)


def _inside(detection: Detection, region: tuple[float, float, float, float]) -> bool:
    x, y = _center(detection)
    return region[0] <= x <= region[2] and region[1] <= y <= region[3]


def _distance_score(person: Detection, head: Detection) -> float:
    x1, y1, x2, y2 = _head_region(person)
    head_x, head_y = _center(head)
    width = max(x2 - x1, 1.0)
    height = max(y2 - y1, 1.0)
    return ((head_x - (x1 + x2) / 2) / width) ** 2 + (
        (head_y - (y1 + y2) / 2) / height
    ) ** 2


def _assign_unique(
    persons: list[Detection], heads: list[Detection]
) -> dict[int, Detection]:
    candidates = [
        (_distance_score(person, head), -head.confidence, person_index, head_index)
        for person_index, person in enumerate(persons)
        for head_index, head in enumerate(heads)
        if _inside(head, _head_region(person))
    ]
    assigned_persons: set[int] = set()
    assigned_heads: set[int] = set()
    assignments: dict[int, Detection] = {}
    for _distance, _confidence, person_index, head_index in sorted(candidates):
        if person_index in assigned_persons or head_index in assigned_heads:
            continue
        assignments[person_index] = heads[head_index]
        assigned_persons.add(person_index)
        assigned_heads.add(head_index)
    return assignments


def assess_helmet_compliance(detections: list[Detection]) -> list[Detection]:
    """Return one person-level helmet compliance result for every detected person."""
    persons = [item for item in detections if item.label.lower() == "person"]
    helmets = [item for item in detections if item.label.lower() == "helmet"]
    bare_heads = [item for item in detections if item.label.lower() == "no_helmet"]
    helmet_assignments = _assign_unique(persons, helmets)
    bare_head_assignments = _assign_unique(persons, bare_heads)

    results = []
    for index, person in enumerate(persons):
        helmet = helmet_assignments.get(index)
        if helmet is not None:
            results.append(Detection("helmet_worn", helmet.confidence, person.bbox))
            continue
        bare_head = bare_head_assignments.get(index)
        confidence = max(
            person.confidence,
            bare_head.confidence if bare_head is not None else 0.0,
        )
        results.append(Detection("no_helmet", confidence, person.bbox))
    return results


def compliance_display_detections(detections: list[Detection]) -> list[Detection]:
    """Replace raw PPE boxes with person-level results while preserving other hazards."""
    compliance = assess_helmet_compliance(detections)
    if not compliance:
        return list(detections)
    context = [
        item
        for item in detections
        if item.label.lower() not in {"person", "helmet", "no_helmet"}
    ]
    return compliance + context
