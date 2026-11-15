"""One owner-consented, ephemeral voice request. No telemetry text."""
import json
import os
from pathlib import Path
import sys

def main(path):
    request=json.loads(Path(path).read_text());cache=Path(request['cache']);voice=cache/'BreezyVoice'
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
    sys.path[:0]=[str(voice),str(voice/'third_party/Matcha-TTS')]
    os.chdir(voice)
    # upstream WeText otherwise calls ModelScope on every construction.
    import wetext.wetext as wetext
    def pinned(model_id,*args,**kwargs):
        if model_id!='pengzhendong/wetext':raise ValueError('Unpinned normalizer requested')
        return str(cache/'wetext')
    wetext.snapshot_download=pinned
    from g2pw import G2PWConverter
    from single_inference import CustomCosyVoice,single_inference
    cosy=CustomCosyVoice(str(cache/'breezy_model'))
    if request.get('private_weight'):
        import torch
        weight=(cache/request['private_weight']).resolve()
        if not weight.is_relative_to(cache.resolve()):raise ValueError('Weight path escaped')
        device=cosy.model.device
        cosy.model.llm.load_state_dict(torch.load(weight,map_location=device))
        cosy.model.llm.to(device).eval()
    bopo=G2PWConverter(model_dir=str(cache/'g2pw'),model_source=str(cache/'bert'))
    single_inference(request['reference'],request['text'],request['output'],cosy,bopo,
        speaker_prompt_text_transcription=request['ref_text'])
if __name__=='__main__':main(sys.argv[1])
