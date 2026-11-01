"""Tests for the pure-python detector helpers (no model needed)."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image
import pytest

from custom_components.babycam.detector import (
    PERSON_CLASS_ID,
    Detection,
    PresenceDetector,
    _letterbox,
    _nms,
    parse_roi,
)


def test_parse_roi_valid() -> None:
    assert parse_roi("0.1,0.2,0.9,0.8") == (0.1, 0.2, 0.9, 0.8)
    assert parse_roi("-1, 0, 2, 1") == (0.0, 0.0, 1.0, 1.0)


@pytest.mark.parametrize("value", ["", "1,2,3", "a,b,c,d", "0.5,0.5,0.4,0.9", "0,0,1"])
def test_parse_roi_invalid(value: str) -> None:
    with pytest.raises(ValueError):
        parse_roi(value)


def test_letterbox_shape() -> None:
    image = Image.new("RGB", (1280, 720), (255, 0, 0))
    tensor, scale, pad_x, pad_y = _letterbox(image, 640)
    assert tensor.shape == (1, 3, 640, 640)
    assert tensor.dtype == np.float32
    assert scale == pytest.approx(0.5)
    assert (pad_x, pad_y) == (0, 140)
    # red pixel in the image area, grey padding outside
    assert tensor[0, 0, 320, 320] == pytest.approx(1.0)
    assert tensor[0, 1, 10, 320] == pytest.approx(114 / 255)


def test_nms_removes_overlaps() -> None:
    boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], dtype=np.float32)
    scores = np.array([0.9, 0.8, 0.7], dtype=np.float32)
    assert _nms(boxes, scores) == [0, 2]


class _FakeSession:
    """Mimics an ONNX Runtime session returning one YOLOv8 [1,84,8400] tensor."""

    def __init__(self, detections: list[tuple[int, float, float, float, float, float]]) -> None:
        self._dets = detections

    def run(self, _outputs, feeds):
        tensor = next(iter(feeds.values()))
        out = np.zeros((1, 84, 8400), dtype=np.float32)
        for i, (cls, score, cx, cy, w, h) in enumerate(self._dets):
            out[0, 0:4, i] = [cx, cy, w, h]
            out[0, 4 + cls, i] = score
        assert tensor.shape == (1, 3, 640, 640)
        return [out]


def _jpeg(width: int = 640, height: int = 640) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height)).save(buf, format="JPEG")
    return buf.getvalue()


def test_detect_person_inside_roi(tmp_path) -> None:
    detector = PresenceDetector(tmp_path / "model.onnx", threshold=0.4)
    detector._session = _FakeSession([(PERSON_CLASS_ID, 0.8, 320, 320, 100, 200)])
    detector._input_name = "images"

    result = detector.detect(_jpeg(), roi=(0.25, 0.25, 0.75, 0.75))
    assert result.present is True
    assert result.confidence == pytest.approx(0.8)
    assert len(result.detections) == 1
    det: Detection = result.detections[0]
    assert det.center == pytest.approx((0.5, 0.5), abs=0.01)


def test_detect_person_outside_roi(tmp_path) -> None:
    detector = PresenceDetector(tmp_path / "model.onnx", threshold=0.4)
    detector._session = _FakeSession([(PERSON_CLASS_ID, 0.8, 50, 50, 40, 40)])
    detector._input_name = "images"

    result = detector.detect(_jpeg(), roi=(0.5, 0.5, 1.0, 1.0))
    assert result.present is False
    assert result.confidence == 0.0
    assert len(result.detections) == 1


def test_detect_ignores_other_classes(tmp_path) -> None:
    detector = PresenceDetector(tmp_path / "model.onnx", threshold=0.4)
    detector._session = _FakeSession([(16, 0.95, 320, 320, 100, 100)])  # dog
    detector._input_name = "images"
    assert detector.detect(_jpeg()).present is False


def test_missing_model_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        PresenceDetector(tmp_path / "nope.onnx").load()
