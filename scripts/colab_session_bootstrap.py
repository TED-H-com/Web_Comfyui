"""Import-safe launch shim. No runner import precedes the initial status."""
import os
from pathlib import Path
import re
import sys
import time
import uuid
from colab_session_diagnostics import atomic_status, failure_diagnostics

def main(argv=None):
    args=list(sys.argv[1:] if argv is None else argv)
    # The shim itself lives in the trusted remote session directory. Even bad
    # argv must leave a status there; no test path override exists in product.
    session=Path(__file__).resolve().parent
    status_root=session
    if len(args)>2 and re.fullmatch('[a-f0-9]{64}',args[2]):status_root=session/'plans'/args[2]
    path=status_root/'bootstrap-status.json'
    atomic_status(path,{'phase':'running','progress_at':time.time(),'diagnostics':{'stage':'session_bootstrap','substage':'argv'}})
    step='argv'
    try:
        assert len(args) in {2,3} and args[0]=='bootstrap'
        assert Path(args[1])==session and session.parent==Path('/content/castle-sessions')
        assert str(uuid.UUID(session.name))==session.name and not session.is_symlink()
        if len(args)==3:assert re.fullmatch('[a-f0-9]{64}',args[2])
        step='import'
        atomic_status(path,{'phase':'running','progress_at':time.time(),'diagnostics':{'stage':'session_bootstrap','substage':step}})
        import session_worker
        return session_worker.bootstrap_session(session,args[2] if len(args)==3 else None)
    except BaseException as error:
        import traceback
        traceback.print_exc(file=sys.stderr)
        atomic_status(path,{'phase':'failed','progress_at':time.time(),'diagnostics':failure_diagnostics(error,step)})
        return 1

if __name__=='__main__':raise SystemExit(main())
