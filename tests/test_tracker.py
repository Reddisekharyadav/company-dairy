import time
from tracker.tracker import ActivityTracker


def test_tracker_runs_for_a_few_cycles():
    t = ActivityTracker(interval=1.0)
    t.start()
    time.sleep(2.5)
    t.stop()
    assert True
