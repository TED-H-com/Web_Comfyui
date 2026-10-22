#!/usr/bin/env python3
"""Speaker similarity helper for local voice gates.

Runs outside the web server, usually via ~/.local/share/web-comfyui/integrations/voice/.venv, because the CAMPPlus
embedding dependencies are already installed there.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np
import onnxruntime
import soundfile as sf
import torch
import torchaudio.compliance.kaldi as kaldi
import torchaudio.functional as AF


DEFAULT_CAMPPLUS = (
    Path.home()
    / ".cache/huggingface/hub/models--MediaTek-Research--BreezyVoice-300M/"
    "snapshots/e33b502e0ac21c16b0ee0d00df66ac3fa737393d/campplus.onnx"
)


def resample(audio: torch.Tensor, sample_rate: int, target: int = 16000) -> torch.Tensor:
    if sample_rate == target:
        return audio
    return AF.resample(audio, sample_rate, target)


def embedding(session: onnxruntime.InferenceSession, wav_path: Path) -> np.ndarray:
    audio, sample_rate = sf.read(str(wav_path), dtype="float32", always_2d=True)
    tensor = torch.from_numpy(audio.T.copy())
    if tensor.shape[0] > 1:
        tensor = tensor.mean(0, keepdim=True)
    tensor = resample(tensor, sample_rate)
    feat = kaldi.fbank(tensor, num_mel_bins=80, dither=0, sample_frequency=16000)
    feat = feat - feat.mean(dim=0, keepdim=True)
    output = session.run(None, {session.get_inputs()[0].name: feat.unsqueeze(0).cpu().numpy()})[0]
    vector = output.flatten()
    return vector / (np.linalg.norm(vector) + 1e-9)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument(
        "--model",
        type=Path,
        default=Path(os.environ.get("CASTLE_CAMPPLUS_ONNX", DEFAULT_CAMPPLUS)),
    )
    args = parser.parse_args()

    if not args.ref.is_file():
        raise FileNotFoundError(f"reference not found: {args.ref}")
    if not args.candidate.is_file():
        raise FileNotFoundError(f"candidate not found: {args.candidate}")
    if not args.model.is_file():
        raise FileNotFoundError(f"CAMPPlus model not found: {args.model}")

    session = onnxruntime.InferenceSession(str(args.model), providers=["CPUExecutionProvider"])
    ref_embedding = embedding(session, args.ref)
    candidate_embedding = embedding(session, args.candidate)
    similarity = float(np.dot(ref_embedding, candidate_embedding))
    print(json.dumps({
        "ok": True,
        "similarity": similarity,
        "reference": str(args.ref),
        "candidate": str(args.candidate),
        "model": str(args.model),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
