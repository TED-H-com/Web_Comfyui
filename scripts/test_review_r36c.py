import json,pytest
from scripts.review_r36_support import CANDIDATE,rig,allocated,job

@pytest.mark.parametrize('historical',[False,True])
def test_colab_job_list_still_reports_requested_gpu_as_assignment(rig,historical):
    """Regression: both owner-scoped projections agree, including historical rows."""
    from compute_parity.colab import ColabNative
    from compute_parity.journal import Journal
    s,b,clock,account,initial=rig;st=allocated(rig,'bootstrap')
    s.sessions.save(st['id'],gpu='G4',assigned_gpu='G4',gpu_name=None,
                    phase='closed' if historical else 'bootstrap',released=historical)
    j=job(rig,st['id'],'failed' if historical else 'queued',requested_gpu='A100',released=historical)
    public=s.snapshot('owner')['jobs'][0]
    assert public['gpu']=='G4' and public['assigned_gpu']=='G4' and public['requested_gpu']=='A100'
    journal=Journal(s.root.parent/'compute-parity/journal.sqlite3')
    journal.reserve('owner',j['id'],'colab',account,'dance.anime.chatgpt','offline',{'unit':'CU','amount':15})
    receipt=ColabNative(s,journal).poll('owner',j['id'])
    assert receipt['assigned_gpu']=='G4' and receipt['gpu']=='G4' and receipt['gpu_name'] is None
    assert s.snapshot('owner')['jobs'][0]['gpu']=='G4'  # Poll did not repair this view.
    ui=(CANDIDATE/'webui/static/colab.js').read_text()
    assert '`${job.gpu} · ${new Date(job.created_at' not in ui
    assert '指定 GPU：' in ui and '實際 GPU：' in ui


@pytest.mark.parametrize('tail,expected',[
    ('AKIASYNTHETICKEYError: [redacted]',''),
    ('Exception: [redacted]','Exception: [redacted]'),
    ('TimeoutExpired: [redacted]','TimeoutExpired: [redacted]'),
])
def test_local_tail_filter_disagrees_with_remote_grammar(rig,monkeypatch,tail,expected):
    """Regression: one exact error vocabulary governs both redactors."""
    s,b,clock,account,initial=rig;st=allocated(rig,'bootstrap')
    def remote(st,code):
        if 'bootstrap-status.json' in code:return {'phase':'failed'}
        if 'bootstrap-stderr.log' in code:return {'tail':tail}
        raise AssertionError('unexpected remote operation')
    monkeypatch.setattr(s,'_exec',remote)
    s.sessions.bootstrap(s.sessions.get(st['id']))
    path=s.root/'sessions'/st['id']/'bootstrap.stderr.txt'
    assert path.read_text()==expected
    assert path.stat().st_mode&0o777==0o600
    assert tail not in json.dumps(s.sessions.snapshot('owner'))
