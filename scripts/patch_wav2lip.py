#!/usr/bin/env python3
"""Apply an offline, hash-pinned Wav2Lip frame-alignment fix; no model execution."""
from __future__ import annotations

import argparse
import ast
import hashlib
import os
from pathlib import Path
import tempfile

UPSTREAM_REVISION = '4ed7fb15812acaf7d364d3a859c1523ad8b8274b'
SOURCE_SHA256 = '9fc3a4297f4c99a5ae25028a27964b756b54b707546f865658610b4ded68056d'
PATCHED_SHA256 = '8e245edc70f41ec29c51732d4e72e7b36de15a86af0ab30401a90eb289ad3a4f'
RELATIVE_SOURCE = Path('Wav2Lip/wav2lip_node.py')

# These replacements use the installed node's existing imports and model API.
# No face image is written when detection fails, and no missing frame is skipped.
FACE_DETECT = '''def face_detect(images, face_detect_batch):
    if not images:
        raise ValueError("對嘴輸入沒有可處理的影片影格。")
    detector = face_detection.FaceAlignment(face_detection.LandmarksType._2D,
                                            flip_input=False, device=device)
    batch_size = face_detect_batch
    try:
        while True:
            predictions = []
            try:
                for i in tqdm(range(0, len(images), batch_size)):
                    predictions.extend(detector.get_detections_for_batch(
                        np.array(images[i:i + batch_size])))
            except RuntimeError:
                if batch_size == 1:
                    raise RuntimeError("臉部偵測失敗；請縮小影片尺寸後再試。") from None
                batch_size = max(1, batch_size // 2)
                continue
            break
        if len(predictions) != len(images):
            raise ValueError("臉部偵測結果與影片影格數不一致，已停止對嘴以避免錯位。")
        boxes = []
        for index, (rect, image) in enumerate(zip(predictions, images)):
            if rect is None:
                raise ValueError(
                    f"第 {index + 1} 個影格未偵測到人臉，已停止對嘴以避免錯位或截短；"
                    "請使用每個影格都能清楚辨識真人臉部的影片。")
            if len(rect) != 4 or not np.isfinite(rect).all():
                raise ValueError(f"第 {index + 1} 個影格的臉部範圍無效，已停止對嘴。")
            x1, y1 = max(0, int(rect[0])), max(0, int(rect[1]))
            x2, y2 = min(image.shape[1], int(rect[2])), min(image.shape[0], int(rect[3]) + 10)
            if x2 <= x1 or y2 <= y1:
                raise ValueError(f"第 {index + 1} 個影格的臉部範圍為空，已停止對嘴。")
            boxes.append([x1, y1, x2, y2])
        boxes = get_smoothened_boxes(np.array(boxes), T=5)
        results = []
        for index, (image, (x1, y1, x2, y2)) in enumerate(zip(images, boxes)):
            crop = image[y1:y2, x1:x2]
            if not crop.size:
                raise ValueError(f"第 {index + 1} 個影格的臉部裁切為空，已停止對嘴。")
            results.append([crop, (y1, y2, x1, x2)])
        return results
    finally:
        del detector
'''

DATAGEN = '''def datagen(frames, mels, face_detect_batch, mode):
    if not frames or not mels:
        raise ValueError("對嘴需要非空的影片影格與語音片段。")
    if mode not in {"sequential", "repetitive"}:
        raise ValueError("不支援的對嘴影格模式。")
    img_size = 96
    img_batch, mel_batch, frame_batch, coords_batch = [], [], [], []
    frame_size = len(frames)
    face_det_results = face_detect(frames, face_detect_batch)
    if len(face_det_results) != frame_size:
        raise ValueError("臉部偵測結果與影片影格數不一致，已停止對嘴以避免截短。")
    repeat_frames = len(mels) / frame_size
    for i, m in enumerate(mels):
        if mode == "sequential":
            face_idx = int(i // repeat_frames)
        else:
            face_idx = i % frame_size
        frame_to_save = frames[face_idx].copy()
        face, coords = face_det_results[face_idx]
        if not face.size:
            raise ValueError(f"第 {face_idx + 1} 個影格的臉部裁切為空，已停止對嘴。")
        face = cv2.resize(face, (img_size, img_size))
        img_batch.append(face)
        mel_batch.append(m)
        frame_batch.append(frame_to_save)
        coords_batch.append(coords)
        if len(img_batch) >= 128:
            img_batch, mel_batch = np.asarray(img_batch), np.asarray(mel_batch)
            img_masked = img_batch.copy()
            img_masked[:, img_size // 2:] = 0
            img_batch = np.concatenate((img_masked, img_batch), axis=3) / 255.
            mel_batch = np.reshape(mel_batch, [len(mel_batch), mel_batch.shape[1], mel_batch.shape[2], 1])
            yield img_batch, mel_batch, frame_batch, coords_batch
            img_batch, mel_batch, frame_batch, coords_batch = [], [], [], []
    if img_batch:
        img_batch, mel_batch = np.asarray(img_batch), np.asarray(mel_batch)
        img_masked = img_batch.copy()
        img_masked[:, img_size // 2:] = 0
        img_batch = np.concatenate((img_masked, img_batch), axis=3) / 255.
        mel_batch = np.reshape(mel_batch, [len(mel_batch), mel_batch.shape[1], mel_batch.shape[2], 1])
        yield img_batch, mel_batch, frame_batch, coords_batch
'''


def digest(source: str) -> str:
    return hashlib.sha256(source.encode('utf-8')).hexdigest()


def patched_source(source: str) -> str:
    """Transform only the exact reviewed source, never execute its imports."""
    if digest(source) == PATCHED_SHA256:
        return source
    if digest(source) != SOURCE_SHA256:
        raise ValueError('Wav2Lip 原始碼版本不符；未修改任何檔案，請先審閱新的節點版本。')
    replacements = {'face_detect': FACE_DETECT, 'datagen': DATAGEN}
    lines = source.splitlines(keepends=True)
    nodes = [node for node in ast.parse(source).body
             if isinstance(node, ast.FunctionDef) and node.name in replacements]
    if len(nodes) != len(replacements):
        raise ValueError('Wav2Lip 函式結構不符，未套用修補。')
    for node in sorted(nodes, key=lambda item: item.lineno, reverse=True):
        lines[node.lineno - 1:node.end_lineno] = [replacements[node.name]]
    result = ''.join(lines)
    anchor = '    return out_images\n'
    if result.count(anchor) != 1:
        raise ValueError('Wav2Lip 輸出結構不符，未套用修補。')
    result = result.replace(anchor,
        '    if len(out_images) != len(mel_chunks):\n'
        '        raise ValueError("對嘴輸出影格數與語音片段不一致，拒絕回傳截短影片。")\n'
        + anchor)
    ast.parse(result)
    return result


def patch_node(node_dir: Path, *, apply: bool = False) -> str:
    target = node_dir / RELATIVE_SOURCE
    if not target.is_file() or target.is_symlink():
        raise ValueError('找不到可修補的 Wav2Lip 原始碼檔案。')
    original = target.read_bytes()
    source = original.decode('utf-8').replace('\r\n', '\n')
    if digest(source) == PATCHED_SHA256:
        return 'already_applied'
    result = patched_source(source)
    if digest(result) != PATCHED_SHA256:
        raise ValueError('Wav2Lip 修補結果雜湊不符，未修改檔案。')
    if not apply:
        return 'ready'
    # Preserve this node's original LF/CRLF convention and permissions.
    content = result.replace('\n', '\r\n').encode() if b'\r\n' in original else result.encode()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.wav2lip-patch-', delete=False) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), target.stat().st_mode & 0o777)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # Do not overwrite a concurrent update made after the initial read.
        if target.read_bytes() != original:
            raise ValueError('Wav2Lip 原始碼同時被修改，未覆寫。')
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return 'applied'


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node-dir', type=Path, required=True)
    parser.add_argument('--apply', action='store_true', help='Write the reviewed patch; default is check only.')
    args = parser.parse_args(argv)
    try:
        print(patch_node(args.node_dir, apply=args.apply))
    except (OSError, UnicodeError, ValueError) as error:
        print(f'Wav2Lip 修補失敗：{error}')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
