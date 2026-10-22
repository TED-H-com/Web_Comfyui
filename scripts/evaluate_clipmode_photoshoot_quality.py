#!/usr/bin/env python3
"""Fail-closed identity/face gate for one ClipMode photoshoot candidate.

The command is read-only.  It decodes one canonical profile and one candidate,
uses an already-installed InsightFace ``buffalo_l`` bundle on CPU, and emits a
single JSON decision to stdout.  A rejected candidate is a completed evaluation
(exit 0); invalid canonical data or runtime/model failures are infrastructure
errors (structured JSON on stderr, non-zero exit).
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import math
import os
from pathlib import Path
import stat
import sys
from typing import Any, Callable, Iterable, Mapping, Sequence


OUTPUT_SCHEMA = "castle.clipmode-photoshoot-quality.v1"
ERROR_SCHEMA = "castle.clipmode-photoshoot-quality-error.v1"
MODEL_NAME = "buffalo_l"
CPU_PROVIDER = "CPUExecutionProvider"
DETECTION_SIZE = (640, 640)
REQUIRED_MODEL_FILES = frozenset({"det_10g.onnx", "w600k_r50.onnx"})

REQUIRED_FACE_COUNT = 1
MINIMUM_DETECTION_SCORE = 0.50
MINIMUM_SHORTEST_FACE_SIDE_PX = 64.0
DEFAULT_MINIMUM_COSINE_SIMILARITY = 0.875


class QualityInfrastructureError(RuntimeError):
    """A stable terminal failure that is not a candidate quality decision."""


class JsonArgumentParser(argparse.ArgumentParser):
    """Keep command-line validation failures machine-readable."""

    def error(self, message: str) -> None:
        del message
        raise QualityInfrastructureError("arguments_invalid")


def _fail(code: str) -> None:
    raise QualityInfrastructureError(code)


def _path_text(value: str | os.PathLike[str], *, code: str) -> str:
    try:
        text = os.fspath(value)
    except TypeError as error:
        raise QualityInfrastructureError(code) from error
    if not isinstance(text, str) or not text or "\x00" in text:
        _fail(code)
    return text


def _existing_regular_file(
    value: str | os.PathLike[str],
    *,
    code: str,
) -> Path:
    text = _path_text(value, code=code)
    try:
        path = Path(text).expanduser().resolve(strict=True)
        info = path.stat()
    except (OSError, RuntimeError, ValueError) as error:
        raise QualityInfrastructureError(code) from error
    if not stat.S_ISREG(info.st_mode):
        _fail(code)
    return path


def validate_insightface_root(value: str | os.PathLike[str]) -> Path:
    """Require the local detector/recognizer before importing InsightFace.

    ``FaceAnalysis`` may download a named bundle when it is absent.  Requiring
    the two modules used by this evaluator before construction prevents that
    behavior and keeps this helper local-only.
    """

    text = _path_text(value, code="insightface_root_invalid")
    try:
        root = Path(text).expanduser().resolve(strict=True)
        if not stat.S_ISDIR(root.stat().st_mode):
            _fail("insightface_root_invalid")
        model_dir = root / "models" / MODEL_NAME
        if not stat.S_ISDIR(model_dir.stat().st_mode):
            _fail("insightface_buffalo_l_missing")
    except QualityInfrastructureError:
        raise
    except (OSError, RuntimeError, ValueError) as error:
        raise QualityInfrastructureError("insightface_buffalo_l_missing") from error

    try:
        available = {
            item.name
            for item in model_dir.iterdir()
            if item.is_file() and item.suffix.lower() == ".onnx"
        }
    except OSError as error:
        raise QualityInfrastructureError("insightface_buffalo_l_unreadable") from error
    if not REQUIRED_MODEL_FILES.issubset(available):
        _fail("insightface_buffalo_l_incomplete")
    return root


def create_face_analyzer(insightface_root: Path) -> Any:
    """Construct a CPU-only detector/recognizer without third-party chatter."""

    try:
        from insightface.app import FaceAnalysis
    except (ImportError, ModuleNotFoundError) as error:
        raise QualityInfrastructureError("insightface_unavailable") from error

    third_party_output = io.StringIO()
    try:
        with redirect_stdout(third_party_output), redirect_stderr(third_party_output):
            analyzer = FaceAnalysis(
                name=MODEL_NAME,
                root=os.fspath(insightface_root),
                allowed_modules=["detection", "recognition"],
                providers=[CPU_PROVIDER],
            )
            analyzer.prepare(ctx_id=-1, det_size=DETECTION_SIZE)
    except Exception as error:
        raise QualityInfrastructureError("insightface_initialization_failed") from error
    return analyzer


def create_opencv_image_loader() -> Callable[[Path], Any]:
    """Return a read-only OpenCV decoder; import is intentionally lazy."""

    try:
        import cv2
    except (ImportError, ModuleNotFoundError) as error:
        raise QualityInfrastructureError("opencv_unavailable") from error

    def load(path: Path) -> Any:
        return cv2.imread(os.fspath(path), cv2.IMREAD_COLOR)

    return load


def _decode_image(loader: Callable[[Path], Any], path: Path, *, label: str) -> Any:
    try:
        image = loader(path)
    except QualityInfrastructureError:
        raise
    except Exception as error:
        raise QualityInfrastructureError(f"{label}_image_decode_failed") from error
    if image is None:
        _fail(f"{label}_image_decode_failed")
    return image


def _detect_faces(analyzer: Any, image: Any, *, label: str) -> tuple[object, ...]:
    third_party_output = io.StringIO()
    try:
        with redirect_stdout(third_party_output), redirect_stderr(third_party_output):
            raw_faces = analyzer.get(image)
        if raw_faces is None:
            _fail(f"{label}_face_detector_result_invalid")
        faces = tuple(raw_faces)
    except QualityInfrastructureError:
        raise
    except Exception as error:
        raise QualityInfrastructureError(f"{label}_face_detection_failed") from error
    return faces


def _face_field(face: object, name: str) -> object:
    try:
        value = getattr(face, name)
    except (AttributeError, KeyError, TypeError):
        value = None
    if value is None and isinstance(face, Mapping):
        value = face.get(name)
    return value


def _finite_float(value: object, *, code: str) -> float:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError) as error:
        raise QualityInfrastructureError(code) from error
    if not math.isfinite(result):
        _fail(code)
    return result


def _detection_score(face: object, *, label: str) -> float:
    score = _finite_float(
        _face_field(face, "det_score"),
        code=f"{label}_detection_score_invalid",
    )
    if not 0.0 <= score <= 1.0:
        _fail(f"{label}_detection_score_invalid")
    return score


def _bbox(face: object, *, label: str) -> tuple[float, float, float, float]:
    raw = _face_field(face, "bbox")
    if raw is None or isinstance(raw, (str, bytes, bytearray)):
        _fail(f"{label}_face_bbox_invalid")
    try:
        values = tuple(float(item) for item in raw)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError) as error:
        raise QualityInfrastructureError(f"{label}_face_bbox_invalid") from error
    if len(values) < 4 or not all(math.isfinite(item) for item in values[:4]):
        _fail(f"{label}_face_bbox_invalid")
    x1, y1, x2, y2 = values[:4]
    if x2 <= x1 or y2 <= y1:
        _fail(f"{label}_face_bbox_invalid")
    return x1, y1, x2, y2


def _shortest_face_side(face: object, *, label: str) -> float:
    x1, y1, x2, y2 = _bbox(face, label=label)
    return min(x2 - x1, y2 - y1)


def _unit_embedding(face: object, *, label: str) -> tuple[float, ...]:
    raw = _face_field(face, "normed_embedding")
    if raw is None or isinstance(raw, (str, bytes, bytearray)):
        _fail(f"{label}_face_embedding_invalid")
    try:
        vector = tuple(float(item) for item in raw)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError) as error:
        raise QualityInfrastructureError(f"{label}_face_embedding_invalid") from error
    if not vector or not all(math.isfinite(item) for item in vector):
        _fail(f"{label}_face_embedding_invalid")
    norm = math.hypot(*vector)
    if not math.isfinite(norm) or norm <= 0.0:
        _fail(f"{label}_face_embedding_invalid")
    return tuple(item / norm for item in vector)


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        _fail("face_embedding_dimension_mismatch")
    value = math.fsum(a * b for a, b in zip(left, right, strict=True))
    return max(-1.0, min(1.0, value))


def _similarity_threshold(value: object) -> float:
    threshold = _finite_float(value, code="minimum_cosine_similarity_invalid")
    if not 0.0 <= threshold <= 1.0:
        _fail("minimum_cosine_similarity_invalid")
    return threshold


def _reported(value: float, digits: int = 6) -> float:
    """Bound report precision and normalize negative zero."""

    rounded = round(value, digits)
    return 0.0 if rounded == 0.0 else rounded


def _report(
    *,
    accepted: bool,
    reason: str,
    canonical_face_count: int,
    candidate_face_count: int,
    canonical_detection_score: float | None,
    candidate_detection_score: float | None,
    canonical_shortest_side: float | None,
    candidate_shortest_side: float | None,
    similarity: float | None,
    minimum_cosine_similarity: float,
) -> dict[str, Any]:
    numeric = lambda value: None if value is None else _reported(value)
    return {
        "schema": OUTPUT_SCHEMA,
        "schemaVersion": 1,
        "accepted": accepted,
        "reason": reason,
        "model": MODEL_NAME,
        "provider": CPU_PROVIDER,
        "metrics": {
            "canonicalFaceCount": canonical_face_count,
            "candidateFaceCount": candidate_face_count,
            "canonicalDetectionScore": numeric(canonical_detection_score),
            "candidateDetectionScore": numeric(candidate_detection_score),
            "canonicalShortestFaceSidePx": numeric(canonical_shortest_side),
            "candidateShortestFaceSidePx": numeric(candidate_shortest_side),
            "identityCosineSimilarity": numeric(similarity),
        },
        "thresholds": {
            "requiredFaceCount": REQUIRED_FACE_COUNT,
            "minimumDetectionScore": MINIMUM_DETECTION_SCORE,
            "minimumShortestFaceSidePx": MINIMUM_SHORTEST_FACE_SIDE_PX,
            "minimumIdentityCosineSimilarity": _reported(
                minimum_cosine_similarity
            ),
        },
    }


def evaluate_face_sets(
    canonical_faces: Iterable[object],
    candidate_faces: Iterable[object],
    *,
    minimum_cosine_similarity: float = DEFAULT_MINIMUM_COSINE_SIMILARITY,
) -> dict[str, Any]:
    """Build one decision from already-detected faces.

    This pure boundary makes every quality branch testable without importing
    OpenCV, InsightFace, ONNX Runtime, or NumPy.
    """

    threshold = _similarity_threshold(minimum_cosine_similarity)
    try:
        canonical = tuple(canonical_faces)
    except TypeError as error:
        raise QualityInfrastructureError("canonical_face_detector_result_invalid") from error
    try:
        candidate = tuple(candidate_faces)
    except TypeError as error:
        raise QualityInfrastructureError("candidate_face_detector_result_invalid") from error

    if len(canonical) != REQUIRED_FACE_COUNT:
        _fail("canonical_face_count_invalid")

    canonical_face = canonical[0]
    canonical_score = _detection_score(canonical_face, label="canonical")
    canonical_side = _shortest_face_side(canonical_face, label="canonical")
    canonical_embedding = _unit_embedding(canonical_face, label="canonical")
    if canonical_score < MINIMUM_DETECTION_SCORE:
        _fail("canonical_detection_confidence_below_threshold")
    if canonical_side < MINIMUM_SHORTEST_FACE_SIDE_PX:
        _fail("canonical_face_too_small")

    candidate_count = len(candidate)
    if candidate_count == 0:
        return _report(
            accepted=False,
            reason="candidate_no_face",
            canonical_face_count=1,
            candidate_face_count=0,
            canonical_detection_score=canonical_score,
            candidate_detection_score=None,
            canonical_shortest_side=canonical_side,
            candidate_shortest_side=None,
            similarity=None,
            minimum_cosine_similarity=threshold,
        )
    if candidate_count != REQUIRED_FACE_COUNT:
        return _report(
            accepted=False,
            reason="candidate_multiple_faces",
            canonical_face_count=1,
            candidate_face_count=candidate_count,
            canonical_detection_score=canonical_score,
            candidate_detection_score=None,
            canonical_shortest_side=canonical_side,
            candidate_shortest_side=None,
            similarity=None,
            minimum_cosine_similarity=threshold,
        )

    candidate_face = candidate[0]
    candidate_score = _detection_score(candidate_face, label="candidate")
    candidate_side = _shortest_face_side(candidate_face, label="candidate")
    candidate_embedding = _unit_embedding(candidate_face, label="candidate")
    similarity = _cosine_similarity(canonical_embedding, candidate_embedding)

    if candidate_score < MINIMUM_DETECTION_SCORE:
        reason = "candidate_detection_confidence_below_threshold"
    elif candidate_side < MINIMUM_SHORTEST_FACE_SIDE_PX:
        reason = "candidate_face_too_small"
    elif similarity < threshold:
        reason = "identity_similarity_below_threshold"
    else:
        reason = "accepted"

    return _report(
        accepted=reason == "accepted",
        reason=reason,
        canonical_face_count=1,
        candidate_face_count=1,
        canonical_detection_score=canonical_score,
        candidate_detection_score=candidate_score,
        canonical_shortest_side=canonical_side,
        candidate_shortest_side=candidate_side,
        similarity=similarity,
        minimum_cosine_similarity=threshold,
    )


def evaluate(
    canonical_profile: str | os.PathLike[str],
    candidate: str | os.PathLike[str],
    insightface_root: str | os.PathLike[str],
    *,
    minimum_cosine_similarity: float = DEFAULT_MINIMUM_COSINE_SIMILARITY,
    analyzer_factory: Callable[[Path], Any] = create_face_analyzer,
    image_loader_factory: Callable[[], Callable[[Path], Any]] = create_opencv_image_loader,
) -> dict[str, Any]:
    """Run the complete local, read-only evaluation."""

    threshold = _similarity_threshold(minimum_cosine_similarity)
    canonical_path = _existing_regular_file(
        canonical_profile, code="canonical_profile_invalid"
    )
    candidate_path = _existing_regular_file(candidate, code="candidate_invalid")
    model_root = validate_insightface_root(insightface_root)

    try:
        analyzer = analyzer_factory(model_root)
    except QualityInfrastructureError:
        raise
    except Exception as error:
        raise QualityInfrastructureError("insightface_initialization_failed") from error
    try:
        image_loader = image_loader_factory()
    except QualityInfrastructureError:
        raise
    except Exception as error:
        raise QualityInfrastructureError("image_loader_initialization_failed") from error

    canonical_image = _decode_image(
        image_loader, canonical_path, label="canonical"
    )
    candidate_image = _decode_image(image_loader, candidate_path, label="candidate")
    canonical_faces = _detect_faces(
        analyzer, canonical_image, label="canonical"
    )
    candidate_faces = _detect_faces(analyzer, candidate_image, label="candidate")
    return evaluate_face_sets(
        canonical_faces,
        candidate_faces,
        minimum_cosine_similarity=threshold,
    )


def _parse_similarity(value: str) -> float:
    try:
        return _similarity_threshold(value)
    except QualityInfrastructureError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def parser() -> argparse.ArgumentParser:
    value = JsonArgumentParser(description=__doc__)
    value.add_argument("--canonical-profile", required=True, type=Path)
    value.add_argument("--candidate", required=True, type=Path)
    value.add_argument("--insightface-root", required=True, type=Path)
    value.add_argument(
        "--minimum-cosine-similarity",
        type=_parse_similarity,
        default=DEFAULT_MINIMUM_COSINE_SIMILARITY,
        help="identity acceptance floor (default: 0.875)",
    )
    return value


def run(
    argv: Sequence[str] | None = None,
    *,
    analyzer_factory: Callable[[Path], Any] = create_face_analyzer,
    image_loader_factory: Callable[[], Callable[[Path], Any]] = create_opencv_image_loader,
) -> int:
    try:
        args = parser().parse_args(argv)
        report = evaluate(
            args.canonical_profile,
            args.candidate,
            args.insightface_root,
            minimum_cosine_similarity=args.minimum_cosine_similarity,
            analyzer_factory=analyzer_factory,
            image_loader_factory=image_loader_factory,
        )
    except QualityInfrastructureError as error:
        print(
            json.dumps(
                {
                    "schema": ERROR_SCHEMA,
                    "schemaVersion": 1,
                    "status": "error",
                    "error": str(error),
                },
                allow_nan=False,
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2

    print(
        json.dumps(
            report,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
