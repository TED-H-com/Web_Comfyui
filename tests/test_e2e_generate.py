import json
import pytest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import socket
import urllib.request
import os
import subprocess
import sys
from pathlib import Path
def test_e2e_generate_unauthorized_post(test_server):
    base, opener, cookie_header = test_server

    payload = {
        "featureId": "unknown-feature-id",
        "settings": {"topic": "test e2e"}
    }

    req = urllib.request.Request(
        base + '/api/post',
        data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json', 'Origin': base, 'Cookie': cookie_header}
    )

    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError") # We expect it to fail with 400 Unknown feature
    except urllib.error.HTTPError as e:
        assert e.code == 400
        error_msg = json.loads(e.read().decode())['error']
        assert "Unknown post feature" in error_msg

def test_e2e_api_history_import_input_bad_payload(test_server):
    base, opener, cookie_header = test_server

    req = urllib.request.Request(
        base + '/api/history/import-input',
        data=b'not json',
        headers={'Content-Type': 'application/json', 'Origin': base, 'Cookie': cookie_header}
    )

    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 400

def test_e2e_generate_missing_builder(test_server):
    base, opener, cookie_header = test_server

    payload = {
        "featureId": "unknown-feature-id",
        "settings": {"topic": "test e2e"}
    }

    req = urllib.request.Request(
        base + '/api/generate',
        data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json', 'Origin': base, 'Cookie': cookie_header}
    )

    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError") # We expect it to fail with 400 Unknown feature
    except urllib.error.HTTPError as e:
        assert e.code == 400
        error_msg = json.loads(e.read().decode())['error']
        assert "Unknown feature" in error_msg


def test_e2e_orchestrator_runs(test_server):
    base, opener, cookie_header = test_server

    req = urllib.request.Request(
        base + '/api/orchestrator/runs',
        headers={'Origin': base, 'Cookie': cookie_header}
    )

    with opener.open(req) as response:
        assert response.status == 200
        result = json.load(response)
        assert 'runs' in result

def test_e2e_orchestrator_tasks(test_server):
    base, opener, cookie_header = test_server

    req = urllib.request.Request(
        base + '/api/orchestrator/tasks',
        headers={'Origin': base, 'Cookie': cookie_header}
    )

    with opener.open(req) as response:
        assert response.status == 200
        result = json.load(response)
        assert 'tasks' in result

def test_e2e_orchestrator_resources(test_server):
    base, opener, cookie_header = test_server

    req = urllib.request.Request(
        base + '/api/orchestrator/resources',
        headers={'Origin': base, 'Cookie': cookie_header}
    )

    with opener.open(req) as response:
        assert response.status == 200
        result = json.load(response)
        assert "summary" in result


def test_e2e_orchestrator_events(test_server):
    base, opener, cookie_header = test_server

    req = urllib.request.Request(
        base + '/api/orchestrator/events',
        headers={'Origin': base, 'Cookie': cookie_header}
    )

    with opener.open(req) as response:
        assert response.status == 200
        result = json.load(response)
        assert 'events' in result

def test_e2e_orchestrator_artifacts(test_server):
    base, opener, cookie_header = test_server

    req = urllib.request.Request(
        base + '/api/orchestrator/artifacts',
        headers={'Origin': base, 'Cookie': cookie_header}
    )

    with opener.open(req) as response:
        assert response.status == 200
        result = json.load(response)
        assert 'artifacts' in result

def test_e2e_orchestrator_tags(test_server):
    base, opener, cookie_header = test_server

    req = urllib.request.Request(
        base + '/api/orchestrator/tags',
        headers={'Origin': base, 'Cookie': cookie_header}
    )

    with opener.open(req) as response:
        assert response.status == 200
        result = json.load(response)
        assert 'tags' in result

def test_e2e_fetch_url(test_server):
    base, opener, cookie_header = test_server

    req = urllib.request.Request(
        base + '/api/fetch-url',
        data=json.dumps({"url": "http://127.0.0.1:0/fake"}).encode(),
        headers={'Content-Type': 'application/json', 'Origin': base, 'Cookie': cookie_header}
    )

    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError") # Expect error since fetch doesn't allow random urls
    except urllib.error.HTTPError as e:
        assert e.code == 400
        error_msg = json.loads(e.read().decode())['error']
        assert error_msg


def test_e2e_generate_bad_body(test_server):
    base, opener, cookie_header = test_server

    req = urllib.request.Request(
        base + '/api/generate',
        data=b'not json',
        headers={'Content-Type': 'application/json', 'Origin': base, 'Cookie': cookie_header}
    )

    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 400


def test_e2e_post_generate_bad_body(test_server):
    base, opener, cookie_header = test_server

    req = urllib.request.Request(
        base + '/api/post',
        data=b'not json',
        headers={'Content-Type': 'application/json', 'Origin': base, 'Cookie': cookie_header}
    )

    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 400


def test_e2e_orchestrator_runs_bad_auth(test_server):
    base, opener, cookie_header = test_server

    req = urllib.request.Request(
        base + '/api/orchestrator/runs',
        headers={'Origin': base}
    )

    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 401

def test_e2e_orchestrator_tasks_bad_auth(test_server):
    base, opener, cookie_header = test_server

    req = urllib.request.Request(
        base + '/api/orchestrator/tasks',
        headers={'Origin': base}
    )

    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 401

def test_e2e_orchestrator_events_bad_auth(test_server):
    base, opener, cookie_header = test_server

    req = urllib.request.Request(
        base + '/api/orchestrator/events',
        headers={'Origin': base}
    )

    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 401

def test_e2e_orchestrator_artifacts_bad_auth(test_server):
    base, opener, cookie_header = test_server

    req = urllib.request.Request(
        base + '/api/orchestrator/artifacts',
        headers={'Origin': base}
    )

    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 401

def test_e2e_orchestrator_tags_bad_auth(test_server):
    base, opener, cookie_header = test_server

    req = urllib.request.Request(
        base + '/api/orchestrator/tags',
        headers={'Origin': base}
    )

    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 401

def test_e2e_orchestrator_resources_bad_auth(test_server):
    base, opener, cookie_header = test_server

    req = urllib.request.Request(
        base + '/api/orchestrator/resources',
        headers={'Origin': base}
    )

    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 401

def test_e2e_orchestrator_adopt_artifact_bad_auth(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(
        base + '/api/orchestrator/artifacts/adopt',
        data=json.dumps({"id": "unknown"}).encode(),
        headers={'Content-Type': 'application/json', 'Origin': base}
    )
    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 401

def test_e2e_voiceover_extract_pptx_bad_auth(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(
        base + '/api/voiceover/extract-pptx',
        data=b'bad',
        headers={'Content-Type': 'application/json', 'Origin': base}
    )
    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 401

def test_e2e_character_create_bad_auth(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(
        base + '/api/characters',
        data=b'bad',
        headers={'Content-Type': 'application/json', 'Origin': base}
    )
    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 401

def test_e2e_voiceover_batches_bad_auth(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(
        base + '/api/voiceover/batches',
        headers={'Origin': base}
    )
    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 401

def test_e2e_admin_users_bad_auth(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(
        base + '/api/admin/users',
        headers={'Origin': base}
    )
    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 401

def test_e2e_dance_library_bad_auth(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(
        base + '/api/dance-library',
        headers={'Origin': base}
    )
    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 401

def test_e2e_dance_templates_bad_auth(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(
        base + '/api/dance-templates',
        headers={'Origin': base}
    )
    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 401

def test_e2e_orchestrator_adopt_artifacts_bad_auth(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(
        base + '/api/orchestrator/artifacts/adopt-batch',
        data=b'{}',
        headers={'Content-Type': 'application/json', 'Origin': base}
    )
    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 401

def test_e2e_orchestrator_update_artifact_bad_auth(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(
        base + '/api/orchestrator/artifacts/123',
        data=b'{}',
        headers={'Content-Type': 'application/json', 'Origin': base}
    )
    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 401

def test_e2e_fetch_url_bad_auth(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(
        base + '/api/fetch-url',
        data=json.dumps({"url": "http://127.0.0.1:0/fake"}).encode(),
        headers={'Content-Type': 'application/json', 'Origin': base}
    )
    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 401

def test_e2e_generate_bad_auth(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(
        base + '/api/generate',
        data=b'{}',
        headers={'Content-Type': 'application/json', 'Origin': base}
    )
    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 401

def test_e2e_post_generate_bad_auth(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(
        base + '/api/post',
        data=b'{}',
        headers={'Content-Type': 'application/json', 'Origin': base}
    )
    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 401

def test_e2e_orchestrator_create_run_bad_auth(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(
        base + '/api/orchestrator/runs',
        data=b'{}',
        headers={'Content-Type': 'application/json', 'Origin': base}
    )
    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 401

def test_e2e_orchestrator_cancel_run_bad_auth(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(
        base + '/api/orchestrator/runs/123/cancel',
        data=b'{}',
        headers={'Content-Type': 'application/json', 'Origin': base}
    )
    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 401

def test_e2e_orchestrator_pause_scheduler_bad_auth(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(
        base + '/api/orchestrator/scheduler/pause',
        data=b'{}',
        headers={'Content-Type': 'application/json', 'Origin': base}
    )
    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 401

def test_e2e_orchestrator_resume_scheduler_bad_auth(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(
        base + '/api/orchestrator/scheduler/resume',
        data=b'{}',
        headers={'Content-Type': 'application/json', 'Origin': base}
    )
    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 401

def test_e2e_history_rename_bad_auth(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(
        base + '/api/history/rename',
        data=b'{}',
        headers={'Content-Type': 'application/json', 'Origin': base}
    )
    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 401

def test_e2e_history_hide_bad_auth(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(
        base + '/api/history/hide',
        data=b'{}',
        headers={'Content-Type': 'application/json', 'Origin': base}
    )
    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 401
