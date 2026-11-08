"""Tests for gpu_lamp state machine: classification and debounced transitions."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import gpu_lamp


def test_classify_thresholds():
    assert gpu_lamp.classify(0, 50) == "calm"
    assert gpu_lamp.classify(50, 50) == "calm"      # strictly above flips
    assert gpu_lamp.classify(51, 50) == "busy"
    assert gpu_lamp.classify(99, 84) == "busy"
    assert gpu_lamp.classify(10, 86) == "hot"       # temp overrides util
    assert gpu_lamp.classify(99, 86) == "hot"
    assert gpu_lamp.classify(10, 85) == "calm"      # strictly above 85


def test_happy_path_mode_change_only_on_flip():
    """write_lamp fires once per committed mode change, not per sample."""
    writes = []
    gpu_lamp.write_lamp = lambda cmd, tries=3: (writes.append(cmd), True)[1]

    samples = [(10, 50)] * 10 + [(90, 50)] * 4 + [(10, 50)] * 4
    times = iter([float(i) for i in range(len(samples))])  # 1 sample/sec
    mode, cand, cand_since = None, None, 0.0
    committed = []
    for u, t in samples:
        now = next(times)
        want = gpu_lamp.classify(u, t)
        if want != cand:
            cand, cand_since = want, now
        elif want != mode and now - cand_since >= gpu_lamp.DEBOUNCE_S:
            if gpu_lamp.write_lamp(gpu_lamp.CMD[want]):
                committed.append(want)
                mode = want
    assert committed == ["calm", "busy", "calm"]        # exactly one write per flip
    assert writes == [gpu_lamp.CMD["calm"], gpu_lamp.CMD["busy"], gpu_lamp.CMD["calm"]]


def test_brief_spike_below_debounce_never_commits():
    writes = []
    gpu_lamp.write_lamp = lambda cmd, tries=3: (writes.append(cmd), True)[1]
    # one 90% spike sample between long calm stretches — never held 2s
    samples = [(10, 50)] * 5 + [(90, 50)] + [(10, 50)] * 5
    mode, cand, cand_since = None, None, 0.0
    for i, (u, t) in enumerate(samples):
        want = gpu_lamp.classify(u, t)
        if want != cand:
            cand, cand_since = want, float(i)
        elif want != mode and i - cand_since >= gpu_lamp.DEBOUNCE_S:
            mode = want
    assert mode in (None, "calm")                       # spike ignored


def test_hot_overrides_after_debounce():
    samples = [(90, 50)] * 3 + [(90, 90)] * 5
    mode, cand, cand_since = None, None, 0.0
    committed = []
    for i, (u, t) in enumerate(samples):
        want = gpu_lamp.classify(u, t)
        if want != cand:
            cand, cand_since = want, float(i)
        elif want != mode and i - cand_since >= gpu_lamp.DEBOUNCE_S:
            committed.append(want); mode = want
    assert committed[-1] == "hot"
