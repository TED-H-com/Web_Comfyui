"""Private server evidence. Never merge this envelope into public diagnostics."""
from pathlib import Path
import re,traceback
ROOT=Path(__file__).resolve().parents[1]
OPS={'account_status','account_identity','remote_exec','runtime_allocate','runtime_lookup','runtime_stop','upload','download'}
def capture(error,operation=None):
    try:from scripts.colab_session_diagnostics import stderr_excerpt
    except ImportError:from colab_session_diagnostics import stderr_excerpt
    name=type(error).__name__
    result={'class':name if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}',name) else 'Exception'}
    if operation in OPS:result['operation']=operation
    code=getattr(error,'code',None)
    try:from scripts.colab_transport_diagnostics import CODES
    except ImportError:from colab_transport_diagnostics import CODES
    if code in CODES:result['bridge_code']=code
    frames=[]
    for frame in traceback.extract_tb(error.__traceback__):
        try:relative=Path(frame.filename).resolve().relative_to(ROOT).as_posix()
        except ValueError:continue
        if re.fullmatch(r'[A-Za-z0-9_./-]+\.py',relative):frames.append(f'{relative}:{frame.lineno}')
    result['frames']=frames[-12:]
    result['line']=' '.join(stderr_excerpt(str(error)).splitlines())[:240]
    return result
