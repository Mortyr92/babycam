"""Local presence detection with a YOLOv8 ONNX model (runs fully offline)."""

from __future__ import annotations

from dataclasses import dataclass
import io
import logging
from pathlib import Path

import numpy as np
from PIL import Image

_LOGGER = logging.getLogger(__name__)

PERSON_CLASS_ID = 0  # COCO "person" - babies are detected as persons


@dataclass(slots=True)
class Detection:
    """A single detected object with normalised (0-1) coordinates."""

    class_id: int
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)


@dataclass(slots=True)
class PresenceResult:
    """Outcome of a presence check."""

    present: bool
    confidence: float
    detections: list[Detection]


def parse_roi(value: str) -> tuple[float, float, float, float]:
    """Parse 'x1,y1,x2,y2' fractions into a clamped tuple."""
    try:
        parts = [float(p) for p in value.split(",")]
    except (ValueError, AttributeError) as err:
        raise ValueError("ROI must be 'x1,y1,x2,y2'") from err
    if len(parts) != 4:
        raise ValueError("ROI must have exactly four values")
    x1, y1, x2, y2 = (min(max(p, 0.0), 1.0) for p in parts)
    if x2 <= x1 or y2 <= y1:
        raise ValueError("ROI must have x2 > x1 and y2 > y1")
    return (x1, y1, x2, y2)


def _letterbox(image: Image.Image, size: int) -> tuple[np.ndarray, float, int, int]:
    """Resize keeping the aspect ratio and pad to a square canvas."""
    width, height = image.size
    scale = size / max(width, height)
    new_w, new_h = max(1, round(width * scale)), max(1, round(height * scale))
    resized = image.resize((new_w, new_h), Image.BILINEAR)
    canvas = Image.new("RGB", (size, size), (114, 114, 114))
    pad_x, pad_y = (size - new_w) // 2, (size - new_h) // 2
    canvas.paste(resized, (pad_x, pad_y))
    arr = np.asarray(canvas, dtype=np.float32) / 255.0
    arr = np.transpose(arr, (2, 0, 1))[np.newaxis, ...]
    return np.ascontiguousarray(arr), scale, pad_x, pad_y


def _nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float = 0.45) -> list[int]:
    """Plain numpy non-maximum suppression."""
    if boxes.size == 0:
        return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
        order = order[1:][iou <= iou_threshold]
    return keep


class PresenceDetector:
    """Wraps an ONNX Runtime session around a YOLOv8 detection model."""

    def __init__(self, model_path: Path, threshold: float = 0.4) -> None:
        self._model_path = model_path
        self.threshold = threshold
        self._session = None
        self._input_name = ""
        self._input_size = 640

    @property
    def model_path(self) -> Path:
        return self._model_path

    @property
    def loaded(self) -> bool:
        return self._session is not None

    def load(self) -> None:
        """Load the model (blocking; call from an executor)."""
        if not self._model_path.is_file():
            raise FileNotFoundError(f"Presence model not found: {self._model_path}")
        import onnxruntime as ort  # noqa: PLC0415 - heavy import, keep it lazy

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._session = ort.InferenceSession(
            str(self._model_path), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        inp = self._session.get_inputs()[0]
        self._input_name = inp.name
        if isinstance(inp.shape[-1], int):
            self._input_size = inp.shape[-1]
        _LOGGER.debug("Loaded presence model %s (input %sx%s)", self._model_path, self._input_size, self._input_size)

    def detect(self, jpeg: bytes, roi: tuple[float, float, float, float] = (0, 0, 1, 1)) -> PresenceResult:
        """Run detection on JPEG bytes (blocking; call from an executor)."""
        if self._session is None:
            self.load()
        assert self._session is not None

        image = Image.open(io.BytesIO(jpeg)).convert("RGB")
        width, height = image.size
        tensor, scale, pad_x, pad_y = _letterbox(image, self._input_size)
        raw = self._session.run(None, {self._input_name: tensor})[0]
        detections = self._postprocess(raw, scale, pad_x, pad_y, width, height)

        rx1, ry1, rx2, ry2 = roi
        in_roi = [
            d
            for d in detections
            if d.class_id == PERSON_CLASS_ID and rx1 <= d.center[0] <= rx2 and ry1 <= d.center[1] <= ry2
        ]
        best = max((d.confidence for d in in_roi), default=0.0)
        return PresenceResult(present=best >= self.threshold, confidence=best, detections=detections)

    def _postprocess(
        self, raw: np.ndarray, scale: float, pad_x: int, pad_y: int, width: int, height: int
    ) -> list[Detection]:
        out = np.squeeze(raw)
        if out.ndim != 2:
            return []
        # Ultralytics exports [84, 8400]; some converters emit [8400, 84].
        if out.shape[0] < out.shape[1]:
            out = out.T
        boxes_xywh = out[:, :4]
        class_scores = out[:, 4:]
        class_ids = class_scores.argmax(axis=1)
        scores = class_scores[np.arange(len(class_ids)), class_ids]
        mask = scores >= min(self.threshold, 0.25)
        boxes_xywh, scores, class_ids = boxes_xywh[mask], scores[mask], class_ids[mask]
        if boxes_xywh.size == 0:
            return []

        cx, cy, w, h = boxes_xywh.T
        boxes = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1)
        keep = _nms(boxes, scores)

        detections: list[Detection] = []
        for i in keep:
            x1 = (boxes[i, 0] - pad_x) / scale / width
            y1 = (boxes[i, 1] - pad_y) / scale / height
            x2 = (boxes[i, 2] - pad_x) / scale / width
            y2 = (boxes[i, 3] - pad_y) / scale / height
            detections.append(
                Detection(
                    class_id=int(class_ids[i]),
                    confidence=float(scores[i]),
                    x1=float(np.clip(x1, 0, 1)),
                    y1=float(np.clip(y1, 0, 1)),
                    x2=float(np.clip(x2, 0, 1)),
                    y2=float(np.clip(y2, 0, 1)),
                )
            )
        return detections
