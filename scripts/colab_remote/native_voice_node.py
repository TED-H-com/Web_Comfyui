"""Portable owner-consented BreezyVoice nodes.

Preserves the local node's PCM16 boundary before single_inference, in a separate
voice environment. Does not start or contact Castle's localhost voice daemon.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import wave
import numpy as np
import torch
import json

class CastleBreezyVoiceClone:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'ref_audio':('AUDIO',),'ref_text':('STRING',{'multiline':True}),'text':('STRING',{'multiline':True})}}
    RETURN_TYPES=('AUDIO',);FUNCTION='clone';CATEGORY='Castle/audio'
    @classmethod
    def IS_CHANGED(cls,**kwargs):return float('nan')
    def clone(self,ref_audio,ref_text,text,daemon_url=None):
        cache=Path(os.environ['CASTLE_PARITY_CACHE'])
        if not ref_text.strip() or not text.strip():raise ValueError('Reference and approved speech text required')
        with tempfile.TemporaryDirectory(prefix='parity-voice-',dir=str(cache.parent)) as td:
            td=Path(td);waveform=ref_audio['waveform'][0].detach().cpu().numpy()
            pcm=(np.clip(waveform.T,-1,1)*32767).astype('<i2')
            with wave.open(str(td/'reference.wav'),'wb') as w:
                w.setnchannels(waveform.shape[0]);w.setsampwidth(2);w.setframerate(int(ref_audio['sample_rate']));w.writeframes(pcm.tobytes())
            request={'reference':str(td/'reference.wav'),'ref_text':ref_text,'text':text,'output':str(td/'out.wav'),'cache':str(cache)}
            config=td/'request.json';config.write_text(json.dumps(request));config.chmod(0o600)
            subprocess.run([str(cache/'voice-venv/bin/python'),str(Path(__file__).with_name('native_voice_worker.py')),str(config)],check=True,timeout=600,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            with wave.open(str(td/'out.wav'),'rb') as w:
                if w.getsampwidth()!=2:raise ValueError('PCM16 output required')
                rate=w.getframerate();channels=w.getnchannels();raw=w.readframes(w.getnframes())
            tensor=torch.from_numpy((np.frombuffer(raw,dtype='<i2').astype(np.float32)/32768).reshape(-1,channels).T.copy()).unsqueeze(0)
            return ({'waveform':tensor,'sample_rate':rate},)
class CastleBreezyVoiceTTS:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'persona':('STRING',),'text':('STRING',{'multiline':True})}}
    RETURN_TYPES=('AUDIO',);FUNCTION='synth';CATEGORY='Castle/audio'
    @classmethod
    def IS_CHANGED(cls,**kwargs):return float('nan')
    def synth(self,persona,text):
        cache=Path(os.environ['CASTLE_PARITY_CACHE'])
        export=json.loads((cache/'voice-export.json').read_text())
        if persona!=export['persona']:raise ValueError('Unbound persona')
        with tempfile.TemporaryDirectory(prefix='parity-persona-',dir=str(cache.parent)) as td:
            td=Path(td)
            request={'reference':str(cache/'ComfyUI/input'/export['reference_name']),
                'ref_text':export['reference_text'],'text':text,'output':str(td/'out.wav'),
                'cache':str(cache),'private_weight':export.get('weight_target')}
            config=td/'request.json';config.write_text(json.dumps(request));config.chmod(0o600)
            subprocess.run([str(cache/'voice-venv/bin/python'),str(Path(__file__).with_name('native_voice_worker.py')),str(config)],check=True,timeout=600,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            with wave.open(str(td/'out.wav'),'rb') as w:
                if w.getsampwidth()!=2:raise ValueError('PCM16 output required')
                rate=w.getframerate();channels=w.getnchannels();raw=w.readframes(w.getnframes())
            tensor=torch.from_numpy((np.frombuffer(raw,dtype='<i2').astype(np.float32)/32768).reshape(-1,channels).T.copy()).unsqueeze(0)
            return ({'waveform':tensor,'sample_rate':rate},)
NODE_CLASS_MAPPINGS={'CastleBreezyVoiceClone':CastleBreezyVoiceClone,'CastleBreezyVoiceTTS':CastleBreezyVoiceTTS}
NODE_DISPLAY_NAME_MAPPINGS={'CastleBreezyVoiceClone':'BreezyVoice 聲音庫（隔離環境）'}

class CastleVoxCPM:
    @classmethod
    def INPUT_TYPES(cls):return {'required':{'text':('STRING',{'multiline':True})}}
    RETURN_TYPES=('AUDIO',);FUNCTION='synth';CATEGORY='Castle/audio'
    def synth(self,text):
        cache=Path(os.environ['CASTLE_PARITY_CACHE'])
        export=json.loads((cache/'voice-export.json').read_text())
        if text!=export['text'] or export['engine']!='voxcpm':raise ValueError('Unbound voice configuration')
        with tempfile.TemporaryDirectory(prefix='parity-vox-',dir=str(cache.parent)) as td:
            td=Path(td);config=td/'request.json';config.write_text(json.dumps({'cache':str(cache),'output':str(td/'out.wav')}));config.chmod(0o600)
            subprocess.run([str(cache/'voxcpm-venv/bin/python'),str(Path(__file__).with_name('native_voxcpm_worker.py')),str(config)],check=True,timeout=1200,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            import soundfile as sf
            raw,rate=sf.read(td/'out.wav',dtype='float32',always_2d=True)
            return ({'waveform':torch.from_numpy(raw.T.copy()).unsqueeze(0),'sample_rate':rate},)
NODE_CLASS_MAPPINGS['CastleVoxCPM']=CastleVoxCPM
