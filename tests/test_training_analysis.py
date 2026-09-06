from types import SimpleNamespace

from training.contracts import extract_evaluation_analysis


def test_extracts_class_confidence_f1_false_positive_and_false_negative_counts():
    metrics = SimpleNamespace(
        names={0: "person", 1: "helmet"},
        curves_results=[
            ([0.0, 0.5, 0.9], [[0.1, 0.8, 0.6], [0.2, 0.4, 0.9]], "Confidence", "F1-Confidence(B)"),
        ],
        confusion_matrix=SimpleNamespace(
            matrix=[[8, 1, 2], [1, 9, 3], [4, 5, 0]]
        ),
    )

    assert extract_evaluation_analysis(metrics) == {
        "schema_version": 1,
        "classes": {
            "person": {"recommended_confidence": 0.5, "best_f1": 0.8, "false_positives": 3, "false_negatives": 5},
            "helmet": {"recommended_confidence": 0.9, "best_f1": 0.9, "false_positives": 4, "false_negatives": 6},
        },
    }


def test_analysis_degrades_safely_when_optional_curves_are_missing():
    metrics = SimpleNamespace(names={0: "fire"}, confusion_matrix=None)

    assert extract_evaluation_analysis(metrics) == {
        "schema_version": 1,
        "classes": {
            "fire": {"recommended_confidence": None, "best_f1": None, "false_positives": None, "false_negatives": None},
        },
    }
