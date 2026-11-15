"""AutoIP character-dance 4n+1 input alignment; output only trims, never pads."""
import hashlib
import json
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path
from .contracts import fail

VERSION='autoip-character-dance-duration-r30'

def quantum_frames(frames):return ((frames-1+3)//4)*4+1

def _run(args):
    try:return subprocess.check_output(args,stderr=subprocess.DEVNULL,timeout=180)
    except Exception:fail('duration_processing_failed','影片時長處理未通過；未重送')

def metadata(path):
    data=json.loads(_run(['ffprobe','-v','error','-count_frames','-show_streams','-of','json',str(path)]))
    video=next(s for s in data['streams'] if s['codec_type']=='video')
    frames=int(video.get('nb_read_frames') or video.get('nb_frames'))
    fps=Fraction(video['avg_frame_rate'])
    if not 0<frames<=4096 or not 0<fps<=120:fail('duration_invalid','來源影片幀數或時基不支援')
    audio=next((s for s in data['streams'] if s['codec_type']=='audio'),None)
    return {'frames':frames,'fps':str(fps),'audio':audio is not None,
            'audio_seconds':float(audio['duration']) if audio and audio.get('duration') else None}

def _directory():
    return tempfile.TemporaryDirectory(prefix='web-comfyui-autoip-')

def prepare(raw,source_contract):
    with _directory() as folder:
        source=Path(folder)/'source.mp4';target=Path(folder)/'aligned.mp4';source.write_bytes(raw)
        meta=metadata(source)
        # Local graph may resample to 16 fps; AutoIP must preserve the uploaded source timebase.
        expected=source_contract.get('source_duration_seconds') if isinstance(source_contract,dict) else None
        if expected is not None and abs(meta['frames']/float(Fraction(meta['fps']))-expected)>1/float(Fraction(meta['fps'])):
            fail('duration_source_mismatch','來源時長與核准契約不一致')
        aligned=quantum_frames(meta['frames']);fps=Fraction(meta['fps'])
        if aligned==meta['frames']:result=raw
        else:
            _run(['ffmpeg','-nostdin','-v','error','-y','-threads','2','-filter_threads','2','-filter_complex_threads','2','-i',str(source),'-map','0:v:0','-map','0:a?',
                  '-vf',f"tpad=stop_mode=clone:stop={aligned-meta['frames']}",'-frames:v',str(aligned),
                  '-af',f'apad,atrim=duration={float(aligned/fps):.12f}',
                  '-c:v','libx264','-threads','2','-crf','18','-pix_fmt','yuv420p','-c:a','aac','-aac_coder','fast','-b:a','192k',str(target)])
            if metadata(target)['frames']!=aligned:fail('duration_padding_failed','送入影片未覆蓋來源幀數')
            result=target.read_bytes()
        return result,{**meta,'input_frames':aligned,'version':VERSION,'source_sha256':hashlib.sha256(raw).hexdigest(),
                       'sent_sha256':hashlib.sha256(result).hexdigest()}

def trim(raw,contract):
    with _directory() as folder:
        source=Path(folder)/'provider.mp4';target=Path(folder)/'trimmed.mp4';source.write_bytes(raw)
        meta=metadata(source);frames=contract['frames'];fps=Fraction(contract['fps'])
        if meta['frames']<frames or Fraction(meta['fps'])!=fps:fail('duration_short','平台成果未覆蓋完整來源；不補凍結尾幀')
        if contract['audio'] and not meta['audio']:fail('duration_audio_missing','平台成果缺少來源音軌')
        _run(['ffmpeg','-nostdin','-v','error','-y','-threads','2','-filter_threads','2','-filter_complex_threads','2','-i',str(source),'-map','0:v:0','-map','0:a?',
              '-vf',f'trim=end_frame={frames},setpts=PTS-STARTPTS','-frames:v',str(frames),
              '-af',f'atrim=duration={float(frames/fps):.12f},asetpts=PTS-STARTPTS',
              '-c:v','libx264','-threads','2','-crf','18','-pix_fmt','yuv420p','-c:a','aac','-aac_coder','fast','-b:a','192k',str(target)])
        checked=metadata(target)
        if checked['frames']!=frames:fail('duration_trim_failed','成果精確幀數驗證失敗')
        if contract['audio'] and (checked['audio_seconds'] is None or abs(checked['audio_seconds']-float(frames/fps))>float(1/fps)):
            fail('duration_audio_mismatch','成果音軌時長超過一幀容差')
        return target.read_bytes()
