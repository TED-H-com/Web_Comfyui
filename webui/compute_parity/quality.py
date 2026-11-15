"""Calibration criteria compare output with source framing/time, not an ideal crop."""
import math

BODY_POINTS=(0,11,12,23,24,25,26,27,28,29,30,31,32)

def visible(point):
    return len(point)>=3 and point[2]>=0.4 and 0.01<point[0]<0.99 and 0.01<point[1]<0.99

def source_framing(source,output):
    if len(source)<33 or len(output)<33:return None
    required=[i for i in BODY_POINTS if visible(source[i])]
    if not all(i in required for i in (0,11,12)):return None
    return all(visible(output[i]) for i in required)

def source_duration(source_seconds,output_seconds,source_fps):
    if not all(math.isfinite(x) and x>0 for x in (source_seconds,output_seconds,source_fps)):return False
    return abs(output_seconds-source_seconds)<=1/source_fps+1e-6

def speaker_cosine(reference,output,model):
    # Embeddings only: no speech recognizer, transcription, prompt or reference
    # text is loaded. Call inside the isolated voice Python environment.
    from speaker_similarity import embedding
    import onnxruntime
    import numpy as np
    session=onnxruntime.InferenceSession(str(model),providers=['CPUExecutionProvider'])
    score=float(np.dot(embedding(session,reference),embedding(session,output)))
    if not math.isfinite(score):raise ValueError('speaker_embedding_unavailable')
    return score
