from backend.stream import DropOldestQueue, Frame


def test_full_queue_drops_oldest_frame():
    frames = DropOldestQueue(2)
    frames.put(Frame(1, 1, "old"))
    frames.put(Frame(1, 2, "middle"))
    frames.put(Frame(1, 3, "fresh"))
    assert frames.dropped == 1
    assert frames.get().image == "middle"
    assert frames.get().image == "fresh"
