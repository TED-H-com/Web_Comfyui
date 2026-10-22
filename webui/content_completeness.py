#!/usr/bin/env python3
"""Persistent Whisper content-completeness gate for Castle presenter audio.

The worker starts this script with the voice-lab Python environment, which has
OpenAI Whisper installed.  In ``--serve`` mode the model is loaded once and
newline-delimited JSON requests are evaluated until shutdown.  The scoring
functions intentionally use only the Python standard library so they can be
unit-tested without Whisper, Torch, or a GPU.
"""

from __future__ import annotations

import argparse
import contextlib
import difflib
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by",
    "for", "from", "had", "has", "have", "he", "her", "hers", "him",
    "his", "i", "if", "in", "is", "it", "its", "me", "my", "not", "of",
    "on", "or", "our", "ours", "she", "so", "than", "that", "the", "their",
    "them", "then", "there", "they", "this", "those", "to", "us", "was",
    "we", "were", "what", "when", "which", "who", "will", "with", "you",
    "your", "yours",
}

NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20",
}

CONTRACTIONS = {
    "can't": "can not", "cannot": "can not", "couldn't": "could not",
    "didn't": "did not", "doesn't": "does not", "don't": "do not",
    "hadn't": "had not", "hasn't": "has not", "haven't": "have not",
    "isn't": "is not", "shouldn't": "should not", "wasn't": "was not",
    "weren't": "were not", "won't": "will not", "wouldn't": "would not",
    "i'll": "i will", "you'll": "you will", "he'll": "he will",
    "she'll": "she will", "it'll": "it will", "we'll": "we will",
    "they'll": "they will", "i'm": "i am", "you're": "you are",
    "he's": "he is", "she's": "she is", "it's": "it is", "we're": "we are",
    "they're": "they are", "i've": "i have", "you've": "you have",
    "we've": "we have", "they've": "they have", "that's": "that is",
    "there's": "there is", "what's": "what is", "who's": "who is",
    "let's": "let us",
}

ASR_TOKEN_EQUIVALENTS = {
    frozenset(("baton", "button")),
}


def normalize_words(value: str) -> list[str]:
    """Normalize expected and ASR text while retaining word order."""
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = value.replace("\u2019", "'").replace("\u2018", "'")
    # A slash between words is commonly spoken and transcribed literally as
    # “slash” (for example, Web/Cloud).  Expand it on both the approved text
    # and ASR sides so the spoken token is required only when the source
    # punctuation is actually present; an unrelated inserted “slash” still
    # fails the strict unexpected-word check.
    value = value.replace("/", " slash ")
    value = re.sub(r"\bpee\s+eye\s+sees\b", " pics ", value, flags=re.IGNORECASE)
    value = re.sub(r"\bpee\s+eye\s+see\b", " pic ", value, flags=re.IGNORECASE)
    value = re.sub(
        r"\bp\s*[. ]\s*i\s*[. ]\s*c(?P<plural>\s*[' ]s)?\b",
        lambda match: " pics " if match.group("plural") else " pic ",
        value,
        flags=re.IGNORECASE,
    )
    value = value.lower().replace("&", " and ")
    for source, target in CONTRACTIONS.items():
        value = re.sub(rf"\b{re.escape(source)}\b", target, value)
    # Apostrophes are inaudible. Whisper can emit spoken “let's” as bare
    # “lets”; normalize both spellings to the contraction's spoken words.
    value = re.sub(r"\blets\b", "let us", value)
    # Normalize compounds that Whisper commonly joins or splits even when the
    # spoken audio is identical.
    compound_patterns = {
        r"\bshare[\s-]*point\b": "sharepoint",
        r"\bone[\s-]*drive\b": "onedrive",
        r"\bcyber[\s-]*security\b": "cybersecurity",
        r"\bmulti[\s-]*factor\b": "multifactor",
        r"\bwork[\s-]*streams\b": "workstreams",
        r"\bwork[\s-]*stream\b": "workstream",
        r"\bwork[\s-]*spaces\b": "workspaces",
        r"\bauto[\s-]*start\b": "autostart",
        # Corpus-confirmed Whisper joins for the permission phrases on the
        # training slide. Keep the audible auxiliary as its own required
        # token instead of accepting its omission.
        r"\bcan[\s-]*edit\b": "can edit",
        r"\bcan[\s-]*view\b": "can view",
        r"\bcan[\s-]*download\b": "can download",
    }
    for pattern, replacement in compound_patterns.items():
        value = re.sub(pattern, replacement, value)
    # The UI's eye icon is often transcribed as the letter I; they are
    # acoustically indistinguishable in this script context.
    value = re.sub(r"\beye\b", "i", value)
    # State abbreviations may be rendered as their spoken names by Whisper.
    value = re.sub(r"\bt\s*x\b", "texas", value)
    value = re.sub(r"\bn\s*j\b", "newjersey", value)
    value = re.sub(r"\bnew\s+jersey\b", "newjersey", value)
    value = re.sub(r"\berp\s+(?P<number>\d+)\b", lambda match: "erp" + match.group("number"), value)
    # Whisper writes the spoken name of the initialism “OK” as “okay”.
    # Canonicalize that one orthographic pair on both sides; this does not
    # make “okay” optional or permit it as an unrelated inserted word.
    value = re.sub(r"\bokay\b", "ok", value)
    value = re.sub(r"\bworkstops\b", "work stops", value)
    value = re.sub(r"\bin\s+to\b", "into", value)
    value = re.sub(r"\ba\s+cross(?=\s+(?:site|sites|team|teams|division|divisions)\b)", "across", value)
    # Whisper may render spoken “number one” as “#1”.  Treat the explicit
    # number marker as formatting, while still requiring the actual numeral.
    number_names = "|".join(map(re.escape, NUMBER_WORDS))
    value = re.sub(
        rf"\bnumber\s+(?P<number>{number_names}|\d+)\b",
        lambda match: NUMBER_WORDS.get(match.group("number"), match.group("number")),
        value,
    )
    # A remaining apostrophe-s is audible and acoustically identical to a
    # plural ending (for example, Teams versus team's).
    value = re.sub(r"\b([a-z0-9]+)'s\b", r"\1s", value)
    value = re.sub(r"\b([a-z]+)n't\b", r"\1 not", value)
    words = re.findall(r"[a-z0-9]+", value)
    return [NUMBER_WORDS.get(word, word) for word in words]


def simple_stem(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith("es"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    if len(token) > 5 and token.endswith("ing"):
        return token[:-3]
    if len(token) > 4 and token.endswith("ed"):
        return token[:-2]
    return token


def soundex(token: str) -> str:
    """Small dependency-free phonetic fallback for ASR spelling variants."""
    if not token:
        return ""
    table = {
        **dict.fromkeys("bfpv", "1"), **dict.fromkeys("cgjkqsxz", "2"),
        **dict.fromkeys("dt", "3"), **dict.fromkeys("l", "4"),
        **dict.fromkeys("mn", "5"), **dict.fromkeys("r", "6"),
    }
    first = token[0]
    encoded: list[str] = []
    previous = table.get(first, "")
    for char in token[1:]:
        code = table.get(char, "")
        if code and code != previous:
            encoded.append(code)
        previous = code
    return (first + "".join(encoded) + "000")[:4]


def token_similarity(expected: str, heard: str) -> float:
    if expected == heard:
        return 1.0
    if simple_stem(expected) == simple_stem(heard):
        return 0.96
    ratio = difflib.SequenceMatcher(None, expected, heard, autojunk=False).ratio()
    if ratio >= 0.84:
        return 0.91
    if ratio >= 0.70:
        return 0.76
    if min(len(expected), len(heard)) >= 4 and soundex(expected) == soundex(heard):
        return 0.70
    return 0.0


def inflection_signature(token: str) -> str:
    """Retain audible endings that must not be hidden by fuzzy ASR matching."""
    if len(token) > 5 and token.endswith("ing"):
        return "ing"
    if len(token) > 4 and token.endswith("ed"):
        return "ed"
    if len(token) > 4 and token.endswith("ies"):
        return "ies"
    if len(token) > 4 and token.endswith("es"):
        return "es"
    if len(token) > 3 and token.endswith("s"):
        return "s"
    return ""


def strict_expected_token_match(expected: str, heard: str) -> bool:
    """Verify an aligned word without accepting missing audible inflections.

    Whisper can produce harmless phonetic spellings such as ``Baton`` versus
    ``Button``.  Those remain acceptable.  A changed spoken word or an omitted
    ending (``meetings`` versus ``meeting``) is not acceptable anywhere in a
    presenter script.
    """
    if expected == heard:
        return True
    # Never let an unknown fuzzy/stem match hide a changed spoken word.  Only
    # corpus-confirmed, acoustically indistinguishable ASR spellings belong in
    # this explicit allowlist.
    return frozenset((expected, heard)) in ASR_TOKEN_EQUIVALENTS


def boundary_token_match(expected: str, heard: str) -> bool:
    """Use a stricter match at audio cuts, including inflection endings.

    A stem match is useful for ordinary ASR substitutions in the middle, but it
    must not allow ``responsibility`` to stand in for ``responsibilities`` at a
    chunk end: that exact failure is audible as a clipped final syllable.
    """
    return strict_expected_token_match(expected, heard)


def align_words(expected: list[str], heard: list[str]) -> list[tuple[int | None, int | None, float]]:
    """Global alignment that distinguishes omissions from ASR substitutions."""
    rows, cols = len(expected) + 1, len(heard) + 1
    gap = -0.62
    scores = [[0.0] * cols for _ in range(rows)]
    moves = [[""] * cols for _ in range(rows)]
    for i in range(1, rows):
        scores[i][0], moves[i][0] = scores[i - 1][0] + gap, "up"
    for j in range(1, cols):
        scores[0][j], moves[0][j] = scores[0][j - 1] + gap, "left"
    for i in range(1, rows):
        for j in range(1, cols):
            similarity = token_similarity(expected[i - 1], heard[j - 1])
            diagonal = scores[i - 1][j - 1] + (similarity if similarity else -0.38)
            up = scores[i - 1][j] + gap
            left = scores[i][j - 1] + gap
            best = max(diagonal, up, left)
            scores[i][j] = best
            # Prefer one-to-one substitutions on ties; this avoids calling an
            # ASR spelling error a missing word plus a hallucinated word.
            moves[i][j] = "diag" if diagonal >= best - 1e-9 else ("up" if up >= left else "left")
    alignment: list[tuple[int | None, int | None, float]] = []
    i, j = len(expected), len(heard)
    while i or j:
        move = moves[i][j]
        if move == "diag":
            alignment.append((i - 1, j - 1, token_similarity(expected[i - 1], heard[j - 1])))
            i -= 1
            j -= 1
        elif move == "up":
            alignment.append((i - 1, None, 0.0))
            i -= 1
        else:
            alignment.append((None, j - 1, 0.0))
            j -= 1
    alignment.reverse()
    return alignment


def evaluate_content(expected_text: str, heard_text: str) -> dict[str, Any]:
    expected = normalize_words(expected_text)
    heard = normalize_words(heard_text)
    if not expected:
        raise ValueError("expected text is empty after normalization")
    alignment = align_words(expected, heard)
    aligned = {i: (j, similarity) for i, j, similarity in alignment if i is not None and j is not None}
    missing = [i for i, j, _ in alignment if i is not None and j is None]
    unexpected = [j for i, j, _ in alignment if i is None and j is not None]
    altered = [
        (i, j)
        for i, j, _ in alignment
        if i is not None
        and j is not None
        and not strict_expected_token_match(expected[i], heard[j])
    ]
    missing_content = [i for i in missing if expected[i] not in STOPWORDS]
    missing_stopwords = [i for i in missing if expected[i] in STOPWORDS]

    weights = [0.35 if token in STOPWORDS else 1.0 for token in expected]
    weighted_match = sum(weights[i] * aligned.get(i, (None, 0.0))[1] for i in range(len(expected)))
    expected_coverage = weighted_match / max(sum(weights), 1e-9)
    expected_chars = " ".join(expected)
    heard_chars = " ".join(heard)
    character_similarity = difflib.SequenceMatcher(
        None, expected_chars, heard_chars, autojunk=False
    ).ratio()
    length_ratio = len(heard_chars.replace(" ", "")) / max(1, len(expected_chars.replace(" ", "")))

    boundary_size = min(3, len(expected))
    prefix_indexes = range(0, boundary_size)
    suffix_indexes = range(len(expected) - boundary_size, len(expected))
    prefix_coverage = sum(aligned.get(i, (None, 0.0))[1] >= 0.70 for i in prefix_indexes) / boundary_size
    suffix_coverage = sum(aligned.get(i, (None, 0.0))[1] >= 0.70 for i in suffix_indexes) / boundary_size
    first_pair = aligned.get(0)
    final_pair = aligned.get(len(expected) - 1)
    first_match = bool(
        first_pair is not None and boundary_token_match(expected[0], heard[first_pair[0]])
    )
    final_match = bool(
        final_pair is not None
        and boundary_token_match(expected[-1], heard[final_pair[0]])
    )

    failures: list[str] = []
    if not heard:
        failures.append("empty transcript")
    if missing:
        failures.append("missing expected words: " + ", ".join(expected[i] for i in missing))
    if altered:
        failures.append(
            "altered expected words: "
            + ", ".join(f"{expected[i]}->{heard[j]}" for i, j in altered)
        )
    if unexpected:
        failures.append(
            "unexpected heard words: " + ", ".join(heard[j] for j in unexpected)
        )
    if not first_match:
        failures.append("first word not verified")
    if not final_match:
        failures.append("final word not verified")
    if prefix_coverage < 2 / 3:
        failures.append("opening boundary incomplete")
    if suffix_coverage < 2 / 3:
        failures.append("closing boundary incomplete")
    if expected_coverage < 0.86:
        failures.append("expected-word coverage below 0.86")
    if character_similarity < 0.72:
        failures.append("transcript similarity below 0.72")
    if not 0.72 <= length_ratio <= 1.45:
        failures.append("transcript length outside safe range")

    return {
        "passed": not failures,
        "expected": expected_text,
        "heard": heard_text,
        "expectedWords": expected,
        "heardWords": heard,
        "expectedCoverage": round(expected_coverage, 4),
        "characterSimilarity": round(character_similarity, 4),
        "lengthRatio": round(length_ratio, 4),
        "prefixCoverage": round(prefix_coverage, 4),
        "suffixCoverage": round(suffix_coverage, 4),
        "firstTokenMatched": first_match,
        "finalTokenMatched": final_match,
        "missingExpectedWords": [expected[i] for i in missing],
        "alteredExpectedWords": [
            {"expected": expected[i], "heard": heard[j]} for i, j in altered
        ],
        "unexpectedHeardWords": [heard[j] for j in unexpected],
        "missingContentWords": [expected[i] for i in missing_content],
        "missingStopwords": [expected[i] for i in missing_stopwords],
        "failures": failures,
    }


def transcribe(model: Any, audio_path: str) -> tuple[str, dict[str, Any]]:
    # Whisper can print progress/model messages.  Keep stdout clean because it
    # is the worker protocol; diagnostics continue to stderr.
    with contextlib.redirect_stdout(sys.stderr):
        result = model.transcribe(
            audio_path,
            language="en",
            fp16=False,
            condition_on_previous_text=False,
            temperature=0.0,
            word_timestamps=True,
            verbose=False,
        )
    segments = result.get("segments") or []
    logprobs = [float(item["avg_logprob"]) for item in segments if item.get("avg_logprob") is not None]
    words = [word for segment in segments for word in (segment.get("words") or [])]
    final_probability = None
    if words and words[-1].get("probability") is not None:
        final_probability = round(float(words[-1]["probability"]), 4)
    return str(result.get("text") or "").strip(), {
        "avgLogprob": round(sum(logprobs) / len(logprobs), 4) if logprobs else None,
        "finalWordProbability": final_probability,
    }


def serve(model_name: str, model_cache: Path) -> int:
    import whisper

    with contextlib.redirect_stdout(sys.stderr):
        model = whisper.load_model(model_name, device="cpu", download_root=str(model_cache))
    print(json.dumps({"ready": True, "model": model_name}), flush=True)
    for raw in sys.stdin:
        request: Any = None
        try:
            request = json.loads(raw)
            if request.get("op") == "shutdown":
                return 0
            expected = str(request.get("expected") or "")
            audio_path = str(request.get("audioPath") or "")
            if not expected or not audio_path:
                raise ValueError("expected and audioPath are required")
            heard, diagnostics = transcribe(model, audio_path)
            response = evaluate_content(expected, heard)
            response.update(diagnostics)
            response["id"] = request.get("id")
            print(json.dumps(response, ensure_ascii=False), flush=True)
        except Exception as error:
            print(json.dumps({
                "id": request.get("id") if isinstance(request, dict) else None,
                "error": f"{type(error).__name__}: {error}",
            }), flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--model", default="small")
    parser.add_argument("--model-cache", type=Path, default=Path.home() / ".cache/whisper")
    parser.add_argument("--expected")
    parser.add_argument("--heard")
    args = parser.parse_args()
    if args.serve:
        return serve(args.model, args.model_cache)
    if args.expected is None or args.heard is None:
        parser.error("--expected and --heard are required outside --serve mode")
    print(json.dumps(evaluate_content(args.expected, args.heard), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
