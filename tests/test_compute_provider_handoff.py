"""Actual source-route and pipeline contracts over synthetic owned PNG bytes."""
import ast
import copy
import hashlib
from pathlib import Path
import re
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'webui'))
from compute_execution import PipelineExecution
from provider_fabric.media_source import resolve_owned_media_source
from provider_fabric.store import ProviderFabricStoreError


@pytest.fixture
def owned_provider_image(tmp_path):
    from PIL import Image

    owner = 'synthetic@example.invalid'
    foreign = 'foreign@example.invalid'
    asset_id, version_id = 'autoip-output:fixture', 'assetver:fixture'
    output = tmp_path / 'comfy-output'
    database = tmp_path / 'provider' / 'provider.sqlite3'
    roots = (output, database.parent / 'autoip-outputs', database.parent / 'production-edits')
    for root in roots:
        root.mkdir(parents=True)
    source = roots[1] / 'adopted.png'
    Image.new('RGB', (32, 24), 'blue').save(source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    evidence = {'projection_id': asset_id, 'version_id': version_id, 'media_kind': 'image',
                'visual_class': 'anime', 'mime_type': 'image/png', 'byte_size': source.stat().st_size,
                'sha256': digest, 'source_uri': source.as_uri()}
    asset = {'visual_class': 'anime', 'character_ids': [], 'versions': [
        {'version_id': version_id, 'version_number': 1, 'sha256': digest,
         'byte_size': evidence['byte_size']}]}
    calls, registrations = [], []

    class Store:
        def get_owned_remote_asset(self, actor, ident):
            calls.append(('asset', actor, ident))
            if actor != 'owner:fixture' or ident != asset_id:
                raise KeyError(ident)
            return copy.deepcopy(asset)

        def get_owned_remote_asset_version_source(self, actor, ident, version):
            calls.append(('version', actor, ident, version))
            if (actor, ident, version) != ('owner:fixture', asset_id, version_id):
                raise KeyError(version)
            return copy.deepcopy(evidence)

    runtime = SimpleNamespace(store=Store(), provider_media_store=object())
    root_calls = []

    def resolve(*args, **kwargs):
        root_calls.append(kwargs['allowed_file_roots'])
        return resolve_owned_media_source(*args, **kwargs)

    def actor_for(user):
        return SimpleNamespace(user_id='owner:fixture' if user == owner else 'owner:foreign')

    server = SimpleNamespace(COMFY_INPUT=tmp_path / 'input', COMFY_OUTPUT=output,
        user_for_email=lambda email: {'email': email, 'role': 'member'} if email in {owner, foreign} else None,
        get_colab_service=lambda: SimpleNamespace(root=tmp_path / 'colab'),
        provider_database_path=lambda: database, resolve_owned_media_source=resolve,
        record_input_upload_owner=lambda name, user: registrations.append((name, user['email'])),
        Handler=SimpleNamespace(_provider_runtime_actor=lambda context:
            (runtime, actor_for(context.current_user()['email']))))
    service = PipelineExecution(server)
    receipt = {'state': 'completed', 'downstream_id': 'intent:fixture', 'provider_id': 'autoip',
               'artifacts': [{'id': asset_id, 'url': '/api/assets/' + asset_id + '/source'}]}
    return SimpleNamespace(owner=owner, foreign=foreign, source=source, evidence=evidence,
        asset=asset, calls=calls, roots=roots, root_calls=root_calls, runtime=runtime,
        registrations=registrations, server=server, service=service, receipt=receipt,
        actor_for=actor_for)


def source_handler(fixture, owner=None):
    """Execute the real route body without importing production configuration."""
    path = Path(__file__).resolve().parents[1] / 'webui/server.py'
    tree = ast.parse(path.read_text())
    handler = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Handler')
    method = next(n for n in handler.body if isinstance(n, ast.FunctionDef) and n.name == 'api_provider_asset_source')
    namespace = {'re': re, 'COMFY_OUTPUT': fixture.server.COMFY_OUTPUT,
                 'provider_database_path': fixture.server.provider_database_path,
                 'resolve_owned_media_source': fixture.server.resolve_owned_media_source}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec'), namespace)
    responses, failures = [], []
    instance = SimpleNamespace(
        _provider_runtime_actor=lambda: (fixture.runtime, fixture.actor_for(owner or fixture.owner)),
        send_local_file=lambda *args, **kwargs: responses.append((args, kwargs)),
        _send_provider_failure=lambda *args: failures.append(args))
    namespace['api_provider_asset_source'](instance, fixture.receipt['artifacts'][0]['url'])
    return responses, failures


@pytest.mark.parametrize('root_index', [0, 1, 2])
def test_real_source_route_and_pipeline_share_existing_roots_and_owned_bytes(owned_provider_image, root_index):
    fixture = owned_provider_image
    source = fixture.source
    if root_index != 1:
        destination = fixture.roots[root_index] / source.name
        source.rename(destination)
        source = destination
        fixture.evidence['source_uri'] = source.as_uri()
    original = source.read_bytes()
    responses, failures = source_handler(fixture)
    assert not failures and responses[0][0] == (source,)
    assert responses[0][1]['content_type'] == 'image/png'
    assert responses[0][1]['cache_control'] == 'private, no-store'
    proof = fixture.service.resolve_artifact(fixture.owner, fixture.receipt, 0)
    frozen = fixture.server.COMFY_INPUT / proof['file']['serverFile']
    assert frozen.read_bytes() == original == source.read_bytes()
    assert proof['sha256'] == fixture.evidence['sha256']
    assert proof['media'] == {'kind': 'image', 'width': 32, 'height': 24}
    assert fixture.root_calls == [fixture.roots, fixture.roots]
    assert fixture.registrations == [(frozen.name, fixture.owner)]
    assert all(call[1] == 'owner:fixture' for call in fixture.calls)
    # Resuming preparation freezes the same bytes and never generates an image.
    assert fixture.service.resolve_artifact(fixture.owner, fixture.receipt, 0) == proof


def test_provider_handoff_rechecks_asset_owner(owned_provider_image):
    fixture = owned_provider_image
    responses, failures = source_handler(fixture, fixture.foreign)
    assert not responses and failures[0][:2] == (404, 'not_found')
    with pytest.raises(KeyError):
        fixture.service.resolve_artifact(fixture.foreign, fixture.receipt, 0)
    assert not fixture.registrations
    assert not fixture.server.COMFY_INPUT.exists()


@pytest.mark.parametrize('change', ['bytes', 'outside_roots'])
def test_provider_handoff_keeps_hash_and_storage_boundary(owned_provider_image, tmp_path, change):
    fixture = owned_provider_image
    if change == 'bytes':
        original = fixture.source.read_bytes()
        fixture.source.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    else:
        destination = tmp_path / 'outside.png'
        fixture.source.rename(destination)
        fixture.evidence['source_uri'] = destination.as_uri()
    responses, failures = source_handler(fixture)
    assert not responses and failures[0][:2] == (409, 'asset_source_changed')
    with pytest.raises(ProviderFabricStoreError):
        fixture.service.resolve_artifact(fixture.owner, fixture.receipt, 0)
    assert not fixture.registrations
    assert not fixture.server.COMFY_INPUT.exists()
