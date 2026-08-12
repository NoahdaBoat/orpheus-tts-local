"""Lazy, thread-safe SNAC decoding for Orpheus audio tokens."""

from __future__ import annotations

import threading
from typing import Sequence

import numpy as np


class SNACDecoder:
    """Load SNAC on first use and serialize access to the model."""

    def __init__(self, model_name: str = "hubertsiuzdak/snac_24khz") -> None:
        self.model_name = model_name
        self._model = None
        self._torch = None
        self._device = "cpu"
        self._lock = threading.RLock()

    @property
    def device(self) -> str:
        return self._device

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            try:
                import torch
            except ImportError as exc:
                raise RuntimeError(
                    "PyTorch is required for Orpheus speech. Install with: pip install torch"
                ) from exc
            try:
                from snac import SNAC
            except ImportError as exc:
                raise RuntimeError(
                    "The SNAC decoder package is missing (needed for Speak / TTS). "
                    "Install it in the same Python environment that runs the app: "
                    "python3 -m pip install 'snac>=1.2.1'"
                ) from exc

            if torch.cuda.is_available():
                device = "cuda"
            elif torch.backends.mps.is_available():
                device = "mps"
            else:
                device = "cpu"
            self._torch = torch
            self._device = device
            self._model = SNAC.from_pretrained(self.model_name).eval().to(device)

    def decode(self, multiframe: Sequence[int]) -> bytes | None:
        if len(multiframe) < 7:
            return None
        frame = list(multiframe[: (len(multiframe) // 7) * 7])
        if not frame:
            return None

        codes_0: list[int] = []
        codes_1: list[int] = []
        codes_2: list[int] = []
        for index in range(0, len(frame), 7):
            codes_0.append(frame[index])
            codes_1.extend((frame[index + 1], frame[index + 4]))
            codes_2.extend(
                (frame[index + 2], frame[index + 3], frame[index + 5], frame[index + 6])
            )

        if any(code < 0 or code > 4096 for code in codes_0 + codes_1 + codes_2):
            return None

        self._ensure_loaded()
        assert self._torch is not None and self._model is not None
        torch = self._torch

        with self._lock, torch.inference_mode():
            codes = [
                torch.tensor([codes_0], device=self._device, dtype=torch.int32),
                torch.tensor([codes_1], device=self._device, dtype=torch.int32),
                torch.tensor([codes_2], device=self._device, dtype=torch.int32),
            ]
            audio_hat = self._model.decode(codes)
            audio_slice = audio_hat[:, :, 2048:4096]
            audio_np = audio_slice.detach().cpu().numpy()
        return (np.clip(audio_np, -1, 1) * 32767).astype(np.int16).tobytes()


_DECODER = SNACDecoder()


def convert_to_audio(
    multiframe: Sequence[int], count: int | None = None
) -> bytes | None:
    """Compatibility wrapper used by the CLI and older imports."""

    del count
    return _DECODER.decode(multiframe)
