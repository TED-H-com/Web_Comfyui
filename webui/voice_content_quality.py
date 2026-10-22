#!/usr/bin/env python3
"""Rank presenter voice candidates by complete wording and sentence tails."""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path


def normalized_words(value: str) -> list[str]:
    value = re.sub(r"\bpee\s+eye\s+see\b", "PIC", value, flags=re.IGNORECASE)
    value = re.sub(
        r"\bP\s+I\s+C(?:\s*['’]\s*s|\s+s)\b",
        "PICS",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\bP\s+I\s+C\b", "PIC", value, flags=re.IGNORECASE)
    value = re.sub(r"\bPIC(?:['’]s|s)\b", "PICS", value, flags=re.IGNORECASE)
    words = re.findall(r"[a-z0-9]+", value.lower())
    collapsed: list[str] = []
    index = 0
    while index < len(words):
        if words[index:index + 3] == ["p", "i", "c"]:
            if index + 3 < len(words) and words[index + 3] == "s":
                collapsed.append("pics")
                index += 4
            else:
                collapsed.append("pic")
                index += 3
        else:
            collapsed.append(words[index])
            index += 1
    return collapsed


def matched_expected_indexes(expected: list[str], heard: list[str]) -> set[int]:
    matcher = difflib.SequenceMatcher(None, expected, heard, autojunk=False)
    matched: set[int] = set()
    for block in matcher.get_matching_blocks():
        matched.update(range(block.a, block.a + block.size))
    return matched


def audit_candidate(model, path: Path, chunks: list[str]) -> dict:
    transcript = model.transcribe(
        str(path),
        language="en",
        fp16=False,
        condition_on_previous_text=False,
        verbose=False,
    )["text"].strip()
    expected: list[str] = []
    ranges: list[tuple[int, int]] = []
    for chunk in chunks:
        start = len(expected)
        expected.extend(normalized_words(chunk))
        ranges.append((start, len(expected)))
    heard = normalized_words(transcript)
    matched = matched_expected_indexes(expected, heard)
    similarity = difflib.SequenceMatcher(
        None, " ".join(expected), " ".join(heard), autojunk=False
    ).ratio()
    cer = 1.0 - similarity
    expected_chars = len("".join(expected))
    length_ratio = len("".join(heard)) / expected_chars if expected_chars else 1.0
    complete_chunks = 0
    final_tokens_matched = 0
    tail_coverages: list[float] = []
    for start, end in ranges:
        tail_size = min(4, end - start)
        tail = set(range(end - tail_size, end))
        coverage = len(tail & matched) / tail_size if tail_size else 1.0
        final_matched = end <= start or end - 1 in matched
        tail_coverages.append(coverage)
        final_tokens_matched += int(final_matched)
        complete_chunks += int(final_matched and coverage >= 0.75)
    tail_coverage = sum(tail_coverages) / len(tail_coverages) if tail_coverages else 1.0
    passed = bool(
        complete_chunks == len(chunks)
        and final_tokens_matched == len(chunks)
        and cer <= 0.38
        and 0.72 <= length_ratio <= 1.45
    )
    return {
        "passed": passed,
        "completeChunks": complete_chunks,
        "finalTokensMatched": final_tokens_matched,
        "tailCoverage": round(tail_coverage, 4),
        "cer": round(cer, 4),
        "lengthRatio": round(length_ratio, 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="base")
    parser.add_argument("--model-cache", type=Path, default=Path.home() / ".cache/whisper")
    args = parser.parse_args()
    payload = json.load(sys.stdin)
    chunks = payload.get("chunks") if isinstance(payload, dict) else None
    candidates = payload.get("candidates") if isinstance(payload, dict) else None
    if not isinstance(chunks, list) or not chunks or not all(isinstance(item, str) and item.strip() for item in chunks):
        raise ValueError("chunks must be a non-empty string list")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("candidates must be a non-empty path list")

    import whisper

    model = whisper.load_model(args.model, device="cpu", download_root=str(args.model_cache))
    results = {}
    for raw_path in candidates:
        path = Path(str(raw_path)).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        results[path.name] = audit_candidate(model, path, chunks)
    print(json.dumps({"results": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
