"""Exact owned completion survives Comfy history loss; all files are synthetic."""
import ast
import hashlib
import json
from pathlib import Path
import stat
import sys
import threading
from types import SimpleNamespace

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'webui'))
from compute_web import ComputeWeb
from compute_routing import ComputeAdapter, ComputeRouter

TASK = '11111111-2222-4333-8444-555555555555'
OWNER = 'owner@example.invalid'


@pytest.fixture
def local(tmp_path):
    output = tmp_path / 'output'; output.mkdir()
    files = []
    for number in range(2):
        path = output / f'synthetic-{number}.png'
        Image.new('RGB', (8, 8), (number * 100, 20, 60)).save(path)
        files.append({'filename': path.name, 'subfolder': '', 'type': 'output'})
    item = {'status': {'completed': True, 'status_str': 'success'},
            'outputs': {'save': {'images': files}}}
    index = {TASK: {'email': OWNER, 'feature': 'text2img'}}
    histories = {TASK: item}
    queue = {'queue_running': [], 'queue_pending': []}
    calls = []
    def resolve(filename, subfolder, kind):
        path = (output / subfolder / filename).resolve()
        return path if kind == 'output' and path.is_relative_to(output.resolve()) else None
    def sidecar_path(path): return Path(str(path) + '.castle.json')
    def load_sidecar(path):
        source = sidecar_path(path)
        return json.loads(source.read_text()) if source.exists() else None
    def atomic(path, payload, mode):
        path.write_text(json.dumps(payload)); path.chmod(mode)
    # Execute the real existing history persistence function; display helpers
    # and application configuration are synthetic, no server import occurs.
    namespace = {'Path': Path, '_GALLERY_MUTATION_LOCK': threading.RLock(),
        'normalize_email': lambda value: value.lower(), 'prompt_id_hint_valid': lambda value: value == TASK,
        'FEATURE_BY_ID': {'text2img': {'title': 'Synthetic'}}, '_primitive_prompt_value': lambda value: value,
        'direct_sidecar_path': sidecar_path, 'load_direct_sidecar': load_sidecar,
        'submitted_by_email': lambda data: data.get('submittedBy', {}).get('email', ''),
        'verify_output_provenance_receipt': lambda *args: None,
        'gallery_media_kind': lambda path: 'image', 'atomic_write_json': atomic}
    tree = ast.parse((ROOT / 'webui/server.py').read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'persist_output_history_detail')
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), str(ROOT / 'webui/server.py'), 'exec'), namespace)
    persisted = []
    def output_images(history, *, job_index):
        persisted.extend(history)
        for task_id, record in history.items():
            for node in record['outputs'].values():
                for outputs in node.values():
                    for file in outputs:
                        path = resolve(file['filename'], file.get('subfolder', ''), 'output')
                        assert namespace['persist_output_history_detail'](path, prompt_id=task_id,
                            indexed=job_index[task_id], workflow='castle-text2img-web', feature='Synthetic',
                            feature_id='text2img', parameters=[], filename=file['filename'],
                            subfolder=file.get('subfolder', ''), view_type='output')
    def access(user, task, metadata=None, unused=None):
        return index.get(task, {}).get('email') == user['email']
    def access_file(user, filename, subfolder, kind, task):
        path = resolve(filename, subfolder, kind)
        if path is None: return False
        data = load_sidecar(path)
        return (data.get('promptId') == task and data.get('submittedBy', {}).get('email') == user['email']) if data else access(user, task)
    def request(path):
        calls.append(path)
        if path == '/history': return histories
        if path == '/queue': return queue
        if path.startswith('/history/'): return {TASK: histories[TASK]} if TASK in histories else {}
        raise AssertionError(path)
    web = object.__new__(ComputeWeb)
    web.user = {'email': OWNER}; web.owner = OWNER
    web.colab = SimpleNamespace(root=tmp_path / 'runtime' / 'colab')
    web.s = SimpleNamespace(load_job_index=lambda: index, local_job_snapshot=lambda: [], request_json=request,
        user_can_access_job=access, user_can_access_view_item=access_file, resolve_view_path=resolve,
        output_images=output_images, load_direct_sidecar=load_sidecar, direct_sidecar_path=sidecar_path,
        direct_sidecar_owner_email=lambda path, data, task: data.get('submittedBy', {}).get('email', '') if data.get('promptId') == task else '',
        direct_sidecar_for_prompt=lambda task: None)
    return SimpleNamespace(web=web, output=output, index=index, histories=histories, queue=queue,
                           item=item, files=files, persisted=persisted, calls=calls)


def test_completed_poll_persists_all_outputs_before_return_and_recovers_offline(local):
    f = local
    first = f.web.status_local(OWNER, TASK)
    assert first['state'] == 'completed' and len(first['artifacts']) == 2
    assert f.persisted == [TASK]
    path = f.web._local_receipt_path(TASK)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    receipt = json.loads(path.read_text())
    assert receipt['owner'] == OWNER and receipt['task_id'] == TASK
    assert len(receipt['files']) == 2
    assert all(row['sha256'] == hashlib.sha256((f.output / row['filename']).read_bytes()).hexdigest() for row in receipt['files'])
    f.index.clear(); f.histories.clear()
    f.web.s.request_json = lambda *args: pytest.fail('durable completion must work with Comfy offline')
    assert f.web.status_local(OWNER, TASK) == first


@pytest.mark.parametrize('mutation', ['changed', 'removed', 'foreign_sidecar', 'malformed_receipt'])
def test_durable_receipt_never_blesses_changed_or_foreign_output(local, mutation):
    f = local; f.web.status_local(OWNER, TASK)
    receipt_path = f.web._local_receipt_path(TASK); original = receipt_path.read_bytes()
    path = f.output / f.files[0]['filename']
    if mutation == 'changed': Image.new('RGB', (8, 8), 'red').save(path)
    elif mutation == 'removed': path.unlink()
    elif mutation == 'foreign_sidecar':
        sidecar = f.web.s.direct_sidecar_path(path); data = json.loads(sidecar.read_text())
        data['submittedBy']['email'] = 'foreign@example.invalid'; sidecar.write_text(json.dumps(data))
    else: receipt_path.write_text('{broken')
    f.web.s.request_json = lambda *args: pytest.fail('invalid durable proof cannot fall back and re-bless bytes')
    assert f.web.status_local(OWNER, TASK)['state'] == 'submission_uncertain'
    if mutation != 'malformed_receipt': assert receipt_path.read_bytes() == original


@pytest.mark.parametrize(('running', 'pending', 'expected'), [
    ([], [], 'submission_uncertain'), ([[0, TASK]], [], 'running'), ([], [[0, TASK]], 'queued'),
    ([[], [0, 'other']], [[1, 'other']], 'submission_uncertain')])
def test_absent_history_checks_both_actual_queues(local, running, pending, expected):
    local.histories.clear()
    local.queue.update(queue_running=running, queue_pending=pending)
    assert local.web.status_local(OWNER, TASK)['state'] == expected


def test_zero_output_completion_and_unpersisted_sidecars_stay_collecting(local):
    local.item['outputs'] = {}
    assert local.web.status_local(OWNER, TASK)['state'] == 'collecting'
    local.item['outputs'] = {'save': {'images': local.files}}
    local.web.s.output_images = lambda *args, **kwargs: None
    assert local.web.status_local(OWNER, TASK)['state'] == 'collecting'
    assert not local.web._local_receipt_path(TASK).exists()


def test_checkpoint_is_owner_scoped_and_refuses_unverifiable_outputs(local):
    local.histories['foreign'] = local.item
    local.index['foreign'] = {'email': 'other@example.invalid'}
    assert local.web.checkpoint_local_history() == {'persisted': [TASK], 'blocked': []}
    assert local.persisted == [TASK]
    (local.output / local.files[0]['filename']).unlink()
    assert local.web.checkpoint_local_history() == {'persisted': [], 'blocked': [TASK]}


def test_foreign_owner_cannot_recover_known_task(local):
    local.web.status_local(OWNER, TASK)
    local.web.user = {'email': 'other@example.invalid'}
    with pytest.raises(PermissionError): local.web.status_local('other@example.invalid', TASK)


def test_router_running_transitions_to_uncertain_without_duplicate_dispatch(local, tmp_path):
    local.histories.clear(); local.queue['queue_running'] = [[0, TASK]]
    submitted = []
    router = ComputeRouter(tmp_path / 'routing.sqlite3', {'local': ComputeAdapter(
        lambda *args: {'eligible': True},
        lambda *args: (submitted.append(args) or {'state': 'queued', 'downstream_id': TASK}),
        local.web.status_local)})
    rid = '11111111-1111-4111-8111-111111111111'
    router.submit(OWNER, rid, {'featureId': 'text2img'}, 'local')
    assert router.status(OWNER, rid, refresh=True)['state'] == 'running'
    local.queue['queue_running'] = []
    assert router.status(OWNER, rid, refresh=True)['state'] == 'submission_uncertain'
    assert router.submit(OWNER, rid, {'featureId': 'text2img'}, 'local')['state'] == 'submission_uncertain'
    assert len(submitted) == 1


def test_trusted_checkpoint_uses_explicit_root_and_skips_unindexed_external_history(local, tmp_path):
    from compute_web import checkpoint_owned_history
    local.web.s.normalize_email = lambda value: value.lower()
    local.web.s.get_colab_service = lambda: pytest.fail('explicit trusted context must not initialize another runtime')
    local.histories['external-unindexed'] = local.item
    target = tmp_path / 'shared' / 'compute-local-results'
    assert checkpoint_owned_history(local.web.s, local.histories, receipt_root=target) == {'persisted': 1, 'skipped': 1}
    assert len(list(target.glob('*.json'))) == 1
    local.web.local_receipt_root = target
    local.histories.clear()
    local.web.s.request_json = lambda path: pytest.fail('checkpoint is durable before reset')
    assert local.web.status_local(OWNER, TASK)['state'] == 'completed'


def test_trusted_checkpoint_blocks_foreign_existing_sidecar_and_incomplete_known_history(local, tmp_path):
    from compute_web import checkpoint_owned_history
    local.web.s.normalize_email = lambda value: value.lower()
    root = tmp_path / 'checkpoint'
    local.item['status'] = {'completed': False, 'status_str': 'running'}
    with pytest.raises(RuntimeError, match='local_checkpoint_unfinished_task'):
        checkpoint_owned_history(local.web.s, local.histories, receipt_root=root)
    local.item['status'] = {'completed': True, 'status_str': 'success'}
    path = local.web.s.direct_sidecar_path(local.output / local.files[0]['filename'])
    path.write_text(json.dumps({'promptId': TASK, 'submittedBy': {'email': 'foreign@example.invalid'}}))
    with pytest.raises(RuntimeError, match='local_checkpoint_output_unverified'):
        checkpoint_owned_history(local.web.s, local.histories, receipt_root=root)
    assert not root.exists()


def test_trusted_checkpoint_preserves_confirmed_failure_without_publishing_partial_outputs(local, tmp_path):
    from compute_web import checkpoint_owned_history
    local.web.s.normalize_email = lambda value: value.lower()
    local.item['status'] = {'completed': False, 'status_str': 'error'}
    root = tmp_path / 'checkpoint'
    assert checkpoint_owned_history(local.web.s, local.histories, receipt_root=root) == {'persisted': 1, 'skipped': 0}
    local.web.local_receipt_root = root
    local.web.s.request_json = lambda *args: pytest.fail('known failure must survive history reset')
    assert local.web.status_local(OWNER, TASK) == {'state': 'failed', 'downstream_id': TASK}
    assert local.persisted == []


def test_duplicate_output_references_are_frozen_once_and_temp_output_is_not_adopted(local):
    local.item['outputs']['other'] = {'images': [local.files[0], {'filename': 'temp.png', 'type': 'temp'}]}
    receipt = local.web.status_local(OWNER, TASK)
    assert receipt['state'] == 'completed'
    assert len(json.loads(local.web._local_receipt_path(TASK).read_text())['files']) == 2


def test_path_escape_and_invalid_media_never_produce_completion_receipt(local):
    outside = local.output.parent / 'outside.png'; Image.new('RGB', (8, 8), 'blue').save(outside)
    local.item['outputs'] = {'save': {'images': [{'filename': '../outside.png', 'type': 'output'}]}}
    assert local.web.status_local(OWNER, TASK)['state'] == 'collecting'
    assert not local.web._local_receipt_path(TASK).exists()
    local.item['outputs'] = {'save': {'images': [local.files[0]]}}
    (local.output / local.files[0]['filename']).write_bytes(b'not-an-image')
    assert local.web.status_local(OWNER, TASK)['state'] == 'collecting'
    assert not local.web._local_receipt_path(TASK).exists()


def test_admin_view_permission_cannot_override_changed_durable_owner(local):
    local.web.status_local(OWNER, TASK)
    local.web.user['role'] = 'admin'
    local.web.s.user_can_access_view_item = lambda *args: True
    sidecar = local.web.s.direct_sidecar_path(local.output / local.files[0]['filename'])
    record = json.loads(sidecar.read_text()); record['submittedBy']['email'] = 'other@example.invalid'
    sidecar.write_text(json.dumps(record))
    assert local.web.status_local(OWNER, TASK)['state'] == 'submission_uncertain'


@pytest.mark.parametrize('failed_endpoint', ['history', 'queue'])
def test_temporary_comfy_unavailability_is_uncertain_not_requeued(local, failed_endpoint):
    local.histories.clear()
    original = local.web.s.request_json
    def unavailable(path):
        if path.startswith('/' + failed_endpoint): raise ConnectionError('synthetic unavailable service')
        return original(path)
    local.web.s.request_json = unavailable
    assert local.web.status_local(OWNER, TASK) == {'state': 'submission_uncertain', 'downstream_id': TASK}


def test_missing_persistent_runtime_cannot_claim_durable_completion(local):
    del local.web.colab
    assert local.web.status_local(OWNER, TASK)['state'] == 'collecting'
    assert local.persisted == []


@pytest.mark.parametrize('job_kind', ['minimax_h3_web', 'clipmode_character_photoshoot_web'])
def test_wrapper_checkpoint_before_final_sidecar_does_not_publish_or_complete_wrapper(local, tmp_path, job_kind):
    from compute_web import checkpoint_owned_history
    local.index[TASK]['jobKind'] = job_kind
    local.web.s.normalize_email = lambda value: value.lower()
    local.web.s.output_images = lambda *args, **kwargs: pytest.fail('raw wrapper checkpoint must not issue public sidecars')
    local.web.s.direct_sidecar_for_prompt = lambda *args: pytest.fail('checkpoint must not scan unrelated output sidecars')
    root = tmp_path / 'checkpoint'
    assert checkpoint_owned_history(local.web.s, local.histories, receipt_root=root) == {'persisted': 1, 'skipped': 0}
    local.web.local_receipt_root = root
    receipt_path = local.web._wrapper_checkpoint_path(TASK)
    receipt = json.loads(receipt_path.read_text())
    assert receipt['wrapper_verified'] is False and receipt['native_state'] == 'completed'
    assert receipt['owner'] == OWNER and receipt['job_kind'] == job_kind and len(receipt['files']) == 2
    assert stat.S_IMODE(receipt_path.stat().st_mode) == 0o600
    assert not local.web._local_receipt_path(TASK).exists()
    assert not list(local.output.glob('*.castle.json'))
    local.web.s.direct_sidecar_for_prompt = lambda task: None
    # Raw Comfy completion and the private checkpoint never override wrapper
    # lifecycle or turn an expired wrapper into a verified completed product.
    assert local.web.status_local(OWNER, TASK)['state'] == 'submission_uncertain'
    local.web.s.local_job_snapshot = lambda: [{'promptId': TASK, 'state': 'running', 'jobKind': job_kind}]
    assert local.web.status_local(OWNER, TASK)['state'] == 'running'


def test_changed_raw_wrapper_proof_blocks_recycle_instead_of_reblessing(local, tmp_path):
    from compute_web import checkpoint_owned_history
    local.index[TASK]['jobKind'] = 'minimax_h3_web'
    local.web.s.normalize_email = lambda value: value.lower()
    root = tmp_path / 'checkpoint'
    assert checkpoint_owned_history(local.web.s, local.histories, receipt_root=root)['persisted'] == 1
    local.web.local_receipt_root = root
    original = local.web._wrapper_checkpoint_path(TASK).read_bytes()
    Image.new('RGB', (8, 8), 'yellow').save(local.output / local.files[0]['filename'])
    with pytest.raises(RuntimeError, match='local_checkpoint_output_unverified'):
        checkpoint_owned_history(local.web.s, local.histories, receipt_root=root)
    assert local.web._wrapper_checkpoint_path(TASK).read_bytes() == original


def test_failed_raw_wrapper_is_checkpointed_without_claiming_wrapper_failure(local, tmp_path):
    from compute_web import checkpoint_owned_history
    local.index[TASK]['jobKind'] = 'minimax_h3_web'
    local.item['status'] = {'completed': False, 'status_str': 'error'}
    local.web.s.normalize_email = lambda value: value.lower()
    root = tmp_path / 'checkpoint'
    assert checkpoint_owned_history(local.web.s, local.histories, receipt_root=root)['persisted'] == 1
    local.web.local_receipt_root = root
    receipt = json.loads(local.web._wrapper_checkpoint_path(TASK).read_text())
    assert receipt['native_state'] == 'failed' and receipt['files'] == [] and not receipt['wrapper_verified']
    assert local.web.status_local(OWNER, TASK)['state'] == 'submission_uncertain'


def test_lost_history_marks_existing_pipeline_uncertain_and_never_dispatches_dependency(local, tmp_path):
    from compute_pipeline import ComputePipeline, PipelineError
    local.histories.clear(); local.queue['queue_running'] = [[0, TASK]]
    submitted = []
    router = ComputeRouter(tmp_path / 'pipeline-routing.sqlite3', {'local': ComputeAdapter(
        lambda *args: {'eligible': True},
        lambda *args: (submitted.append(args) or {'state': 'queued', 'downstream_id': TASK}),
        local.web.status_local)})
    pipeline = ComputePipeline(tmp_path / 'pipelines.sqlite3', router_factory=lambda owner: router,
        artifact_resolver=lambda *args: pytest.fail('uncertain upstream cannot hand off a guessed artifact'))
    pid = 'cd8a76c6-1111-4111-8111-111111111111'
    plan = {'version': 1, 'steps': [
        {'id': 'image', 'request': {'featureId': 'text2img'}, 'placement': 'local'},
        {'id': 'video', 'request': {'featureId': 'img2video'}, 'placement': 'local',
         'bindings': {'image': {'step_id': 'image', 'artifact_index': 0, 'expected_kind': 'image'}}}]}
    pipeline.create(OWNER, pid, plan)
    pipeline.tick(OWNER, pid)
    assert pipeline.tick(OWNER, pid)['steps'][0]['state'] == 'running'
    local.queue['queue_running'] = []
    result = pipeline.tick(OWNER, pid)
    assert result['state'] == 'submission_uncertain'
    assert result['steps'][0]['state'] == 'submission_uncertain' and not result['steps'][0]['retry_allowed']
    assert result['steps'][1]['state'] == 'pending' and not result['artifacts']
    assert pipeline.resume(OWNER, pid)['state'] == 'submission_uncertain'
    with pytest.raises(PipelineError) as error:
        pipeline.retry(OWNER, pid, 'image', placement='colab')
    assert error.value.code == 'step_retry_unsafe' and len(submitted) == 1
