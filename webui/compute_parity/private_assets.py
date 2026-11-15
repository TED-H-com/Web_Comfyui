"""Freeze trusted owner weights as bounded chunks for the existing Colab bridge."""
from pathlib import Path
import hashlib
import os
import shutil
from .contracts import fail, safe_relative
CHUNK_BYTES=16*1024*1024

def freeze_private_models(bundle, directory):
    uploads=[]
    for item in bundle['models']:
        if not item.get('private'):continue
        target=item['target']
        if not safe_relative(target):fail('private_model_invalid','私人權重路徑不正確')
        source=Path(item['path']);folder=directory/'private-models'/(target+'.parts')
        folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        digest=hashlib.sha256();size=0;index=0
        with source.open('rb') as src:
            for block in iter(lambda:src.read(CHUNK_BYTES),b''):
                size+=len(block);digest.update(block);dest=folder/str(index);index+=1
                with os.fdopen(os.open(dest,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as out:out.write(block)
                uploads.append({'path':dest.relative_to(directory).as_posix(),'size':len(block),'sha256':hashlib.sha256(block).hexdigest()})
        if size!=item['bytes'] or digest.hexdigest()!=item['sha256']:
            shutil.rmtree(folder);fail('private_model_changed','私人權重與核准套件不一致')
    return uploads
