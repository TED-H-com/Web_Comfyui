"""Disk-backed durable pipeline tests; synthetic adapters never run providers."""
import concurrent.futures
import json
from pathlib import Path
import sqlite3
import sys
import threading
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'webui'))
from compute_pipeline import ComputePipeline, PipelineError, step_request_id
from compute_routing import ComputeAdapter, ComputeRejected, ComputeRouter


def request(feature='text2img'):
    return {'featureId': feature, 'settings': {'prompt': 'synthetic-private-prompt'}, 'files': {}}


def plan(*steps):
    return {'version': 1, 'steps': list(steps) or [{'id': 'image', 'request': request(), 'placement': 'local'}]}


def chain():
    return plan({'id': 'image', 'request': request(), 'placement': 'local'},
                {'id': 'video', 'request': request('img2video'), 'placement': 'autoip',
                 'bindings': {'image': {'step_id': 'image', 'artifact_index': 0}}})


class Driver:
    def __init__(self, tmp_path):
        self.root, self.calls, self.bindings, self.jobs, self.failures = tmp_path, [], [], {}, {}
        self.clock_value = 1000
        self.on_submit = None
        self.resolver = self.resolve
        self.factory_owners = []

    def clock(self):
        self.clock_value += .01
        return self.clock_value

    def router(self, owner):
        self.factory_owners.append(owner)
        def submit(lane, who, rid, body, limits):
            assert who == owner
            self.calls.append((lane, who, rid, body, limits))
            if self.on_submit:
                self.on_submit(who, rid, body)
            failure = self.failures.get(body['featureId'])
            if failure:
                raise failure
            self.jobs[(who, rid)] = {'state': 'running', 'downstream_id': rid}
            return self.jobs[(who, rid)]
        def status(who, rid):
            return self.jobs[(who, rid)]
        def cancel(who, rid):
            self.jobs[(who, rid)]['state'] = 'cancelling'
            return self.jobs[(who, rid)]
        return ComputeRouter(self.root / 'routing.sqlite3', {
            lane: ComputeAdapter(lambda *args, lane=lane: {'eligible': True, 'queue_depth': 0,
                'estimated_cost': 0, 'currency': {'local': 'local', 'autoip': 'credits'}[lane]},
                lambda *args, lane=lane: submit(lane, *args), status, cancel)
            for lane in ('local', 'autoip')})

    def resolve(self, owner, upstream, index):
        self.bindings.append((owner, upstream, index))
        return {'file': {'serverFile': 'owned-synthetic.png', 'promptId': upstream['downstream_id']},
                'sha256': 'a' * 64, 'artifact_id': 'owned:synthetic'}

    def engine(self):
        return ComputePipeline(self.root / 'pipelines.sqlite3', router_factory=self.router,
            artifact_resolver=lambda *args: self.resolver(*args), clock=self.clock, lease_seconds=5)

    def finish(self, owner, rid, state='completed', artifacts=True):
        self.jobs[(owner, rid)] = {'state': state, 'downstream_id': rid}
        if artifacts:
            self.jobs[(owner, rid)]['artifacts'] = [{'name': 'synthetic.png', 'url': '/api/owned/synthetic'}]


@pytest.fixture
def driver(tmp_path):
    return Driver(tmp_path)


def test_dependency_chain_persists_hashes_and_completes_only_after_every_verified_result(driver):
    engine, ident = driver.engine(), str(uuid.uuid4())
    created = engine.create('owner', ident, chain())
    assert created['state'] == 'queued' and not driver.calls
    first = engine.tick('owner', ident)
    assert len(driver.calls) == 1 and first['steps'][1]['state'] == 'pending'
    rid = first['steps'][0]['request_id']
    driver.finish('owner', rid)
    second = engine.tick('owner', ident)
    assert len(driver.calls) == 2 and len(driver.bindings) == 1
    assert driver.calls[1][0] == 'autoip'
    assert driver.calls[1][3]['files']['image'] == {'serverFile': 'owned-synthetic.png', 'promptId': rid}
    assert second['steps'][1]['dependency_hashes'] == {'image': 'a' * 64}
    assert second['state'] == 'running'
    driver.finish('owner', second['steps'][1]['request_id'])
    completed = driver.engine().resume('owner', ident)
    assert completed['state'] == 'completed' and len(completed['artifacts']) == 2
    driver.engine().resume('owner', ident)
    assert len(driver.calls) == 2
    assert 'synthetic-private-prompt' not in json.dumps(engine.list_jobs('owner'))


def test_parent_plan_identity_is_immutable_and_owner_scoped(driver):
    engine, ident = driver.engine(), str(uuid.uuid4())
    original = engine.create('owner', ident, plan())
    assert engine.create('owner', ident, plan()) == original
    with pytest.raises(PipelineError) as error:
        engine.create('owner', ident, plan({'id': 'different', 'request': request()}))
    assert error.value.status == 409
    for operation in (engine.status, engine.cancel, engine.resume):
        with pytest.raises(PipelineError) as error:
            operation('other', ident)
        assert error.value.status == 404
    assert engine.list_jobs('other') == []
    assert step_request_id(ident, 'image', 1) != step_request_id(ident, 'image', 2)


@pytest.mark.parametrize('bad', [
    plan({'id': 'a', 'request': request(), 'depends_on': ['b']}, {'id': 'b', 'request': request(), 'depends_on': ['a']}),
    plan({'id': 'a', 'request': request(), 'depends_on': ['missing']}),
    plan({'id': 'a', 'request': request()}, {'id': 'a', 'request': request()}),
    plan({'id': 'a', 'request': request(), 'bindings': {'image': {'url': 'https://invalid.example/image.png'}}}),
    plan({'id': 'a', 'request': request(), 'placement': 'unknown'}),
])
def test_invalid_graph_and_client_dependency_urls_rejected_before_persistence(driver, bad):
    engine = driver.engine()
    with pytest.raises(PipelineError):
        engine.create('owner', str(uuid.uuid4()), bad)
    assert engine.list_jobs('owner') == [] and not driver.calls


def test_ambiguous_submit_never_retries_or_switches_provider_after_restart(driver):
    driver.failures['text2img'] = TimeoutError('private upstream detail')
    engine, ident = driver.engine(), str(uuid.uuid4())
    engine.create('owner', ident, chain())
    first = engine.tick('owner', ident)
    assert first['state'] == 'submission_uncertain' and len(driver.calls) == 1
    for _ in range(3):
        driver.engine().resume('owner', ident)
    with pytest.raises(PipelineError):
        engine.retry('owner', ident, 'image')
    assert len(driver.calls) == 1 and driver.calls[0][0] == 'local'
    assert 'private upstream' not in json.dumps(engine.status('owner', ident))


def test_confirmed_failure_retry_has_new_identity_and_keeps_completed_predecessor(driver):
    engine, ident = driver.engine(), str(uuid.uuid4())
    engine.create('owner', ident, chain())
    first = engine.tick('owner', ident)
    driver.finish('owner', first['steps'][0]['request_id'])
    second = engine.tick('owner', ident)
    old_rid = second['steps'][1]['request_id']
    driver.finish('owner', old_rid, 'failed', artifacts=False)
    failed = engine.tick('owner', ident)
    assert failed['state'] == 'blocked'
    retried = engine.retry('owner', ident, 'video')
    assert retried['steps'][1]['attempt'] == 2 and retried['steps'][1]['request_id'] != old_rid
    engine.resume('owner', ident)
    assert len(driver.calls) == 3 and sum(call[3]['featureId'] == 'text2img' for call in driver.calls) == 1
    assert len(engine.status('owner', ident)['steps'][1]['attempts']) == 2


@pytest.mark.parametrize('change', [{'sha256': 'b' * 64}, {'artifact_id': 'different:source'}])
def test_retry_revalidates_original_dependency_hash_and_identity_before_dispatch(driver, change):
    engine, ident = driver.engine(), str(uuid.uuid4())
    engine.create('owner', ident, chain())
    first = engine.tick('owner', ident)
    driver.finish('owner', first['steps'][0]['request_id'])
    second = engine.tick('owner', ident)
    driver.finish('owner', second['steps'][1]['request_id'], 'failed', artifacts=False)
    engine.tick('owner', ident)
    retried = engine.retry('owner', ident, 'video')
    assert retried['steps'][1]['dependency_hashes'] == {'image': 'a' * 64}
    driver.resolver = lambda *args: {**driver.resolve(*args), **change}
    result = engine.tick('owner', ident)
    assert result['steps'][1]['state'] == 'rejected' and result['steps'][1]['error_code'] == 'dependency_changed'
    assert len(driver.calls) == 2


def test_concurrent_workers_only_dispatch_one_step(driver):
    engine, ident = driver.engine(), str(uuid.uuid4())
    engine.create('owner', ident, plan())
    entered, release = threading.Event(), threading.Event()
    def slow(*args):
        entered.set()
        assert release.wait(3)
    driver.on_submit = slow
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(engine.tick, 'owner', ident)
        assert entered.wait(3)
        second = driver.engine().tick('owner', ident)
        assert second['state'] == 'submission_uncertain'
        release.set()
        first.result(timeout=3)
    assert len(driver.calls) == 1


def test_crash_after_pipeline_dispatch_claim_does_not_start_missing_router_request(driver):
    engine, ident = driver.engine(), str(uuid.uuid4())
    engine.create('owner', ident, plan())
    with sqlite3.connect(engine.path) as db:
        db.execute("UPDATE compute_pipeline_attempts SET state='dispatching'")
    result = driver.engine().resume('owner', ident)
    assert result['state'] == 'submission_uncertain' and not driver.calls


def test_lost_pipeline_receipt_recovers_existing_router_identity_without_resubmit(driver):
    engine, ident = driver.engine(), str(uuid.uuid4())
    engine.create('owner', ident, plan())
    original_write = engine._write
    def lose_receipt(*args, **kwargs):
        if kwargs.get('receipt'):
            return False
        return original_write(*args, **kwargs)
    engine._write = lose_receipt
    first = engine.tick('owner', ident)
    assert first['state'] == 'submission_uncertain' and len(driver.calls) == 1
    recovered = driver.engine().resume('owner', ident)
    assert recovered['steps'][0]['state'] == 'running' and len(driver.calls) == 1


def test_stale_preparation_can_resume_but_completed_work_cannot_repeat(driver):
    engine, ident = driver.engine(), str(uuid.uuid4())
    engine.create('owner', ident, plan())
    with sqlite3.connect(engine.path) as db:
        db.execute("UPDATE compute_pipeline_attempts SET state='preparing'")
        db.execute("UPDATE compute_pipelines SET lease_token='stale',lease_until=1")
    result = driver.engine().resume('owner', ident)
    assert len(driver.calls) == 1 and result['steps'][0]['state'] == 'running'


@pytest.mark.parametrize('change', [
    {'sha256': ''}, {'file': {'serverFile': '../unowned.png'}},
    {'file': {'serverFile': 'https://invalid.example/image.png'}},
    {'file': {'_characterAssetPath': '/unowned/file'}},
])
def test_invalid_resolver_receipt_blocks_dependent_before_dispatch(driver, change):
    engine, ident = driver.engine(), str(uuid.uuid4())
    engine.create('owner', ident, chain())
    first = engine.tick('owner', ident)
    driver.finish('owner', first['steps'][0]['request_id'])
    driver.resolver = lambda *args: {**driver.resolve(*args), **change}
    result = engine.tick('owner', ident)
    assert result['steps'][1]['state'] == 'rejected' and len(driver.calls) == 1


def test_cancel_blocks_dependents_and_waits_for_active_confirmation(driver):
    engine, ident = driver.engine(), str(uuid.uuid4())
    engine.create('owner', ident, chain())
    first = engine.tick('owner', ident)
    result = engine.cancel('owner', ident)
    assert result['state'] == 'cancelling'
    assert [row['state'] for row in result['steps']] == ['cancelling', 'cancelled']
    driver.finish('owner', first['steps'][0]['request_id'], 'cancelled', artifacts=False)
    assert engine.tick('owner', ident)['state'] == 'cancelled'
    with pytest.raises(PipelineError):
        engine.resume('owner', ident)
    assert len(driver.calls) == 1


def test_cancel_during_dependency_preparation_prevents_dispatch(driver):
    engine, ident = driver.engine(), str(uuid.uuid4())
    engine.create('owner', ident, chain())
    first = engine.tick('owner', ident)
    driver.finish('owner', first['steps'][0]['request_id'])
    entered, release = threading.Event(), threading.Event()
    def slow(*args):
        entered.set()
        assert release.wait(3)
        return driver.resolve(*args)
    driver.resolver = slow
    with concurrent.futures.ThreadPoolExecutor() as pool:
        future = pool.submit(engine.tick, 'owner', ident)
        assert entered.wait(3)
        cancelled = driver.engine().cancel('owner', ident)
        assert cancelled['state'] == 'cancelled'
        release.set()
        future.result(timeout=3)
    assert len(driver.calls) == 1 and engine.status('owner', ident)['steps'][1]['state'] == 'cancelled'


def test_expired_preparation_lease_fences_old_worker_before_dispatch(driver):
    engine, ident = driver.engine(), str(uuid.uuid4())
    engine.create('owner', ident, chain())
    first = engine.tick('owner', ident)
    driver.finish('owner', first['steps'][0]['request_id'])
    entered, release = threading.Event(), threading.Event()
    seen = []
    def slow_once(*args):
        seen.append(True)
        if len(seen) == 1:
            entered.set()
            assert release.wait(3)
        return driver.resolve(*args)
    driver.resolver = slow_once
    with concurrent.futures.ThreadPoolExecutor() as pool:
        future = pool.submit(engine.tick, 'owner', ident)
        assert entered.wait(3)
        driver.clock_value += 10
        driver.engine().tick('owner', ident)
        release.set()
        future.result(timeout=3)
    assert len(seen) == 2 and len(driver.calls) == 2
    assert engine.status('owner', ident)['steps'][1]['state'] == 'running'


def test_completed_transport_without_artifact_cannot_complete_parent(driver):
    engine, ident = driver.engine(), str(uuid.uuid4())
    engine.create('owner', ident, plan())
    first = engine.tick('owner', ident)
    driver.finish('owner', first['steps'][0]['request_id'], artifacts=False)
    result = engine.tick('owner', ident)
    assert result['steps'][0]['state'] == 'collecting' and result['state'] != 'completed'


def test_worker_inventory_has_only_owner_identity_and_resumes_fresh_context(driver):
    engine, ident = driver.engine(), str(uuid.uuid4())
    engine.create('first', ident, plan())
    engine.create('second', str(uuid.uuid4()), plan())
    items = engine.work_items()
    assert all(set(item) == {'owner', 'pipeline_id'} for item in items)
    for item in items:
        engine.tick(item['owner'], item['pipeline_id'])
    assert driver.factory_owners == ['first', 'second']


def test_retry_pins_new_placement_and_merged_budget_without_mutating_original_attempt(driver):
    engine, ident = driver.engine(), str(uuid.uuid4())
    original = plan({'id': 'image', 'request': request(), 'placement': 'local',
                     'limits': {'max_minutes': 5, 'max_credits': 2}})
    accepted = engine.create('owner', ident, original)
    first = engine.tick('owner', ident)
    driver.finish('owner', first['steps'][0]['request_id'], 'failed', artifacts=False)
    engine.tick('owner', ident)
    retry = engine.retry('owner', ident, 'image', placement='autoip', limits={'max_credits': 3})
    assert retry['plan_hash'] == accepted['plan_hash']
    assert retry['steps'][0]['placement'] == 'autoip'
    assert retry['steps'][0]['limits'] == {'max_minutes': 5, 'max_credits': 3}
    assert retry['steps'][0]['attempts'][0]['placement'] == 'local'
    assert retry['steps'][0]['attempts'][0]['limits']['max_credits'] == 2
    with pytest.raises(PipelineError):
        engine.retry('owner', ident, 'image', placement='local')
    driver.engine().resume('owner', ident)
    assert [call[0] for call in driver.calls] == ['local', 'autoip']
    assert driver.calls[0][2] != driver.calls[1][2]
    assert driver.calls[1][4] == {'max_minutes': 5, 'max_credits': 3}


def test_uncertain_attempt_cannot_be_replaced_even_with_new_placement_and_limits(driver):
    driver.failures['text2img'] = TimeoutError('uncertain')
    engine, ident = driver.engine(), str(uuid.uuid4())
    engine.create('owner', ident, plan())
    engine.tick('owner', ident)
    with pytest.raises(PipelineError, match='已確認'):
        engine.retry('owner', ident, 'image', placement='autoip', limits={'max_credits': 1})
    assert len(driver.calls) == 1


@pytest.mark.parametrize('kind', ['audio', 'video'])
def test_typed_dependency_persists_decoder_metadata_and_rejects_kind_mismatch(driver, kind):
    engine, ident = driver.engine(), str(uuid.uuid4())
    spec = chain()
    spec['steps'][1]['bindings']['image']['expected_kind'] = kind
    driver.resolver = lambda *args: {**driver.resolve(*args), 'media': {'kind': kind, 'duration': .25}}
    engine.create('owner', ident, spec)
    first = engine.tick('owner', ident)
    driver.finish('owner', first['steps'][0]['request_id'])
    second = engine.tick('owner', ident)
    assert second['steps'][1]['dependency_media'] == {'image': {'kind': kind, 'duration': .25}}
    driver.finish('owner', second['steps'][1]['request_id'], 'failed', artifacts=False)
    engine.tick('owner', ident)
    engine.retry('owner', ident, 'video', placement='local')
    driver.resolver = lambda *args: {**driver.resolve(*args), 'media': {'kind': 'image', 'width': 8, 'height': 8}}
    rejected = engine.tick('owner', ident)
    assert rejected['steps'][1]['error_code'] == 'artifact_kind_mismatch'
    assert len(driver.calls) == 2


def test_ready_independent_steps_start_without_waiting_for_each_others_outputs(driver):
    engine, ident = driver.engine(), str(uuid.uuid4())
    engine.create('owner', ident, plan(
        {'id': 'a', 'request': request('a'), 'placement': 'local'},
        {'id': 'b', 'request': request('b'), 'placement': 'autoip'}))
    state = engine.tick('owner', ident)
    assert [step['state'] for step in state['steps']] == ['running', 'running']
    assert [call[0] for call in driver.calls] == ['local', 'autoip']
    engine.tick('owner', ident)
    assert len(driver.calls) == 2


def test_reconciliation_failure_does_not_starve_later_owner(driver):
    engine = driver.engine()
    for owner in ['disabled1', 'disabled2', 'disabled3', 'enabled']:
        engine.create(owner, str(uuid.uuid4()), plan())
    def factory(owner):
        if owner.startswith('disabled'):
            raise PermissionError('account disabled')
        return driver.router(owner)
    engine.router_factory = factory
    visited = []
    for _ in range(2):
        for item in engine.work_items(limit=2):
            visited.append(item['owner'])
            try:
                engine.tick(item['owner'], item['pipeline_id'])
            except PermissionError:
                pass
    assert visited == ['disabled1', 'disabled2', 'disabled3', 'enabled']
    assert len(driver.calls) == 1 and driver.calls[0][1] == 'enabled'


def test_old_database_migration_keeps_identity_and_inherits_original_attempt_options(driver):
    engine, ident = driver.engine(), str(uuid.uuid4())
    initial = engine.create('owner', ident, plan())
    # Reproduce the immediately previous schema with an accepted old row.
    with sqlite3.connect(engine.path) as db:
        db.execute('ALTER TABLE compute_pipeline_attempts DROP COLUMN placement')
        db.execute('ALTER TABLE compute_pipeline_attempts DROP COLUMN limits_json')
        db.execute('ALTER TABLE compute_pipelines DROP COLUMN last_reconciled')
    migrated = driver.engine()
    assert migrated.status('owner', ident)['steps'][0]['request_id'] == initial['steps'][0]['request_id']
    assert migrated.status('owner', ident)['steps'][0]['placement'] == 'local'
    migrated.tick('owner', ident)
    assert len(driver.calls) == 1
