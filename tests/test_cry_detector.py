"""Tests for the cry classifier post-processing."""

from __future__ import annotations

import numpy as np

from custom_components.babycam.cry_detector import CRY_CLASS_IDS, CryClassifier


class _FakeSession:
    def __init__(self, frames: int = 2) -> None:
        self._frames = frames

    def run(self, _outputs, feeds):
        waveform = next(iter(feeds.values()))
        assert waveform.dtype == np.float32
        scores = np.zeros((self._frames, 521), dtype=np.float32)
        for idx in CRY_CLASS_IDS:
            scores[:, idx] = 0.2
        scores[:, 0] = 0.9  # speech, must be ignored
        return [scores]


def test_classify_sums_cry_classes(tmp_path) -> None:
    clf = CryClassifier(tmp_path / "yamnet.onnx")
    clf._session = _FakeSession()
    clf._input_name = "waveform"
    clf._input_rank = 1
    clf._scores_index = 0
    score = clf.classify(np.zeros(15600, dtype=np.float32))
    assert score == np.float32(0.6)


def test_classify_handles_batched_input(tmp_path) -> None:
    clf = CryClassifier(tmp_path / "yamnet.onnx")

    class _Session(_FakeSession):
        def run(self, outputs, feeds):
            assert next(iter(feeds.values())).shape == (1, 15600)
            return super().run(outputs, feeds)

    clf._session = _Session()
    clf._input_name = "waveform"
    clf._input_rank = 2
    assert clf.classify(np.zeros(15600, dtype=np.float32)) > 0
