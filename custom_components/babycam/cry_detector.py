"""Local cry detection: ffmpeg pulls audio from the camera stream, YAMNet (ONNX) classifies it."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
import contextlib
import logging
from pathlib import Path
from typing import Any

import numpy as np

_LOGGER = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME_SAMPLES = 15600  # 0.975 s, YAMNet's native window
# AudioSet ontology indices used by YAMNet.
CRY_CLASS_IDS = {19: "Crying, sobbing", 20: "Baby cry, infant cry", 21: "Whimper"}
RESTART_DELAY = 10


class CryClassifier:
    """Runs YAMNet on 16 kHz mono PCM windows."""

    def __init__(self, model_path: Path) -> None:
        self._model_path = model_path
        self._session = None
        self._input_name = ""
        self._input_rank = 1
        self._scores_index = 0

    @property
    def model_path(self) -> Path:
        return self._model_path

    @property
    def loaded(self) -> bool:
        return self._session is not None

    def load(self) -> None:
        """Load the model (blocking)."""
        if not self._model_path.is_file():
            raise FileNotFoundError(f"Cry model not found: {self._model_path}")
        import onnxruntime as ort  # noqa: PLC0415

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(self._model_path), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        inp = self._session.get_inputs()[0]
        self._input_name = inp.name
        self._input_rank = len(inp.shape)
        # Pick the output carrying the 521 AudioSet class scores.
        for idx, out in enumerate(self._session.get_outputs()):
            if out.shape and out.shape[-1] == 521:
                self._scores_index = idx
                break
        _LOGGER.debug("Loaded cry model %s (input rank %s)", self._model_path, self._input_rank)

    def classify(self, pcm: np.ndarray) -> float:
        """Return the combined cry probability (0-1) for a float32 waveform."""
        if self._session is None:
            self.load()
        assert self._session is not None
        waveform = pcm.astype(np.float32)
        if self._input_rank == 2:
            waveform = waveform[np.newaxis, :]
        scores = self._session.run(None, {self._input_name: waveform})[self._scores_index]
        scores = np.asarray(scores)
        if scores.ndim > 1:
            scores = scores.reshape(-1, scores.shape[-1]).mean(axis=0)
        return float(np.clip(sum(scores[i] for i in CRY_CLASS_IDS if i < len(scores)), 0.0, 1.0))


class CryMonitor:
    """Background task that feeds stream audio through the classifier."""

    def __init__(
        self,
        classifier: CryClassifier,
        ffmpeg_binary: str,
        on_score: Callable[[float], Coroutine[Any, Any, None]],
    ) -> None:
        self._classifier = classifier
        self._ffmpeg = ffmpeg_binary
        self._on_score = on_score
        self._task: asyncio.Task | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._source: str | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def async_start(self, source: str) -> None:
        if self.running and self._source == source:
            return
        await self.async_stop()
        self._source = source
        self._task = asyncio.create_task(self._run(), name="babycam-cry-monitor")

    async def async_stop(self) -> None:
        task, self._task = self._task, None
        await self._kill_process()
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _kill_process(self) -> None:
        proc, self._process = self._process, None
        if proc is not None and proc.returncode is None:
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except TimeoutError:
                _LOGGER.warning("ffmpeg did not exit after kill")

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                await self._stream_once(loop)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Cry monitor audio stream error: %s", err)
            await self._kill_process()
            await asyncio.sleep(RESTART_DELAY)

    async def _stream_once(self, loop: asyncio.AbstractEventLoop) -> None:
        assert self._source is not None
        cmd = [
            self._ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-rtsp_transport",
            "tcp",
            "-i",
            self._source,
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "s16le",
            "pipe:1",
        ]
        self._process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        assert self._process.stdout is not None
        bytes_per_frame = FRAME_SAMPLES * 2
        while True:
            chunk = await self._process.stdout.readexactly(bytes_per_frame)
            pcm = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
            score = await loop.run_in_executor(None, self._classifier.classify, pcm)
            await self._on_score(score)
