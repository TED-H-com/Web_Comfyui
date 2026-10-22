#!/usr/bin/env python3
"""Validate the pinned SageAttention native extensions on an SM120 GPU."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import math
from pathlib import Path
import sys
import time


EXTENSIONS = (
    "sageattention._qattn_sm80",
    "sageattention._qattn_sm89",
    "sageattention._fused",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--imports-only",
        action="store_true",
        help="Import all three native extensions without allocating a CUDA tensor.",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def import_extensions() -> dict[str, str]:
    # Import torch first so its shared libraries are in the loader namespace.
    import torch  # noqa: F401

    paths: dict[str, str] = {}
    for name in EXTENSIONS:
        module = importlib.import_module(name)
        module_path = Path(module.__file__).resolve()
        if not module_path.is_file():
            raise RuntimeError(f"native extension has no file: {name}")
        paths[name] = str(module_path)
    return paths


def gpu_validation() -> dict[str, object]:
    import torch
    import torch.nn.functional as functional
    from sageattention import sageattn

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is not available")
    device = torch.device("cuda:0")
    capability = tuple(torch.cuda.get_device_capability(device))
    if capability != (12, 0):
        raise RuntimeError(f"expected SM120, found compute capability {capability}")

    torch.manual_seed(20260814)
    shape = (1, 4, 256, 64)
    q = torch.randn(shape, device=device, dtype=torch.bfloat16)
    k = torch.randn(shape, device=device, dtype=torch.bfloat16)
    v = torch.randn(shape, device=device, dtype=torch.bfloat16)

    torch.cuda.synchronize(device)
    started = time.perf_counter()
    output = sageattn(q, k, v, tensor_layout="HND", is_causal=False)
    torch.cuda.synchronize(device)
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    scores = torch.matmul(q.float(), k.float().transpose(-2, -1)) / math.sqrt(
        shape[-1]
    )
    reference = torch.matmul(torch.softmax(scores, dim=-1), v.float())
    output_float = output.float()
    difference = output_float - reference
    cosine = functional.cosine_similarity(
        output_float.flatten(), reference.flatten(), dim=0
    ).item()
    mean_absolute_error = difference.abs().mean().item()
    maximum_absolute_error = difference.abs().max().item()
    relative_l2 = (
        torch.linalg.vector_norm(difference)
        / torch.linalg.vector_norm(reference)
    ).item()
    finite = bool(torch.isfinite(output).all().item())

    if tuple(output.shape) != shape:
        raise RuntimeError(f"unexpected output shape: {tuple(output.shape)}")
    if output.dtype != torch.bfloat16:
        raise RuntimeError(f"unexpected output dtype: {output.dtype}")
    if not finite:
        raise RuntimeError("SageAttention output contains non-finite values")
    if cosine <= 0.99:
        raise RuntimeError(f"cosine similarity below threshold: {cosine}")
    if mean_absolute_error >= 0.02:
        raise RuntimeError(f"mean absolute error above threshold: {mean_absolute_error}")
    if relative_l2 >= 0.10:
        raise RuntimeError(f"relative L2 error above threshold: {relative_l2}")

    return {
        "gpu_name": torch.cuda.get_device_name(device),
        "compute_capability": list(capability),
        "torch": torch.__version__,
        "torch_cuda": torch.version.cuda,
        "input_dtype": str(q.dtype),
        "output_dtype": str(output.dtype),
        "shape": list(shape),
        "finite": finite,
        "elapsed_ms": round(elapsed_ms, 6),
        "cosine_similarity": round(cosine, 9),
        "mean_absolute_error": round(mean_absolute_error, 9),
        "maximum_absolute_error": round(maximum_absolute_error, 9),
        "relative_l2": round(relative_l2, 9),
        "status": "PASS",
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report: dict[str, object] = {
            "sageattention": importlib.metadata.version("sageattention"),
            "extensions": import_extensions(),
            "status": "PASS",
        }
        if not args.imports_only:
            report["gpu_validation"] = gpu_validation()
    except Exception as exc:
        if args.json:
            print(json.dumps({"status": "FAIL", "error": str(exc)}, sort_keys=True))
        else:
            print(f"SageAttention SM120 validation: FAIL: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print("SageAttention native extension imports: PASS")
        for name, path in report["extensions"].items():
            print(f"  {name}: {path}")
        if not args.imports_only:
            validation = report["gpu_validation"]
            print(
                "SageAttention SM120 BF16 validation: PASS "
                f"(cosine={validation['cosine_similarity']}, "
                f"MAE={validation['mean_absolute_error']}, "
                f"relative_l2={validation['relative_l2']})"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
