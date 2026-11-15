"""Run the exact product VoxCPM worker and speaker gate; never log private text."""
import json,os,runpy,sys
from pathlib import Path

def arguments(cache,export,output):
    settings=export['settings']
    return ['voxcpm_tts.py','--mode','lora','--text',export['text'],'--output',str(output),'--job-id','parity',
        '--model-path',str(cache/'voxcpm-model'),'--lora-path',str(cache/'voxcpm-lora'),
        '--reference-audio',str(cache/'ComfyUI/input'/export['reference_name']),'--prompt-text',export['reference_text'],
        '--profile',settings['voiceProfile'],'--language',settings.get('language',''),'--accent',settings.get('accent',''),
        '--cfg',str(settings.get('cfg',2.0)),'--steps',str(settings.get('steps',10)),'--seed',str(settings.get('seed',42)),
        '--speaker-gate','--speaker-threshold',str(export['speaker_threshold']),'--speaker-max-attempts',str(export['speaker_max_attempts']),
        '--speaker-python',str(cache/'voice-venv/bin/python'),'--speaker-script',str(cache/'voxcpm-worker/speaker_similarity.py')]

def main(path):
    request=json.loads(Path(path).read_text());cache=Path(request['cache'])
    export=json.loads((cache/'voice-export.json').read_text())
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',CASTLE_CAMPPLUS_ONNX=str(cache/'breezy_model/campplus.onnx'))
    sys.path.insert(0,str(cache/'voxcpm-code'))
    sys.argv=arguments(cache,export,Path(request['output']))
    runpy.run_path(str(cache/'voxcpm-worker/voxcpm_tts.py'),run_name='__main__')
if __name__=='__main__':main(sys.argv[1])
