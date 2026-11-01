#!/usr/bin/env python3
"""Export the two ONNX models BabyCam needs, entirely on your own machine.

Run this on any computer (it does NOT need to be the Home Assistant host), then copy the
resulting files into ``<ha-config>/babycam/models/``:

    pip install ultralytics tensorflow tensorflow-hub tf2onnx
    python scripts/export_models.py --out ./models

Models produced:
  * yolov8n.onnx  - YOLOv8 nano (COCO), used to detect a person/baby in the bed region
  * yamnet.onnx   - YAMNet (AudioSet), used to detect crying in the camera audio

Both exports are done locally from the upstream weights that the libraries ship/download; nothing
from this repository or from Home Assistant ever leaves your network at runtime.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys


def export_yolo(out: Path) -> None:
    from ultralytics import YOLO  # noqa: PLC0415

    model = YOLO("yolov8n.pt")
    exported = model.export(format="onnx", imgsz=640, opset=12, simplify=True, dynamic=False)
    shutil.move(str(exported), out / "yolov8n.onnx")
    print(f"wrote {out / 'yolov8n.onnx'}")


def export_yamnet(out: Path) -> None:
    import tensorflow as tf  # noqa: PLC0415
    import tensorflow_hub as hub  # noqa: PLC0415
    import tf2onnx  # noqa: PLC0415

    yamnet = hub.load("https://tfhub.dev/google/yamnet/1")

    class Wrapper(tf.Module):
        @tf.function(input_signature=[tf.TensorSpec([None], tf.float32, name="waveform")])
        def __call__(self, waveform):
            scores, _embeddings, _spectrogram = yamnet(waveform)
            return {"scores": scores}

    tf2onnx.convert.from_function(
        Wrapper().__call__,
        input_signature=[tf.TensorSpec([None], tf.float32, name="waveform")],
        opset=13,
        output_path=str(out / "yamnet.onnx"),
    )
    print(f"wrote {out / 'yamnet.onnx'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=Path("models"))
    parser.add_argument("--skip-yolo", action="store_true")
    parser.add_argument("--skip-yamnet", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    if not args.skip_yolo:
        export_yolo(args.out)
    if not args.skip_yamnet:
        export_yamnet(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
