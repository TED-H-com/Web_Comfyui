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
def test_api_status(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(base + '/api/status', headers={'Cookie': cookie_header})
    with opener.open(req) as response:
        assert response.status == 200

def test_api_queue(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(base + '/api/queue', headers={'Cookie': cookie_header})
    with opener.open(req) as response:
        assert response.status == 200

def test_api_google_status(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(base + '/api/google/status', headers={'Cookie': cookie_header})
    with opener.open(req) as response:
        assert response.status == 200

def test_api_orchestrator_status(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(base + '/api/orchestrator/status', headers={'Cookie': cookie_header})
    with opener.open(req) as response:
        assert response.status == 200

def test_api_history(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(base + '/api/history', headers={'Cookie': cookie_header})
    with opener.open(req) as response:
        assert response.status == 200

def test_api_characters(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(base + '/api/characters', headers={'Cookie': cookie_header})
    with opener.open(req) as response:
        assert response.status == 200

def test_api_voices_list(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(base + '/api/voices', headers={'Cookie': cookie_header})
    with opener.open(req) as response:
        assert response.status == 200

def test_api_workflows(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(base + '/api/workflow-library', headers={'Cookie': cookie_header})
    with opener.open(req) as response:
        assert response.status == 200

def test_api_google_drive_files(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(base + "/api/google/drive/files", headers={"Cookie": cookie_header})
    with opener.open(req) as response:
        assert response.status == 200

def test_api_workflows_file(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(base + '/api/workflow-library/file?id=unknown', headers={'Cookie': cookie_header})
    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 400

def test_api_workflows_file_bad_auth(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(base + '/api/workflow-library/file?id=unknown')
    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 401

def test_api_features(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(base + '/api/features', headers={'Cookie': cookie_header})
    with opener.open(req) as response:
        assert response.status == 200

def test_api_posts(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(base + '/api/posts', headers={'Cookie': cookie_header})
    with opener.open(req) as response:
        assert response.status == 200

def test_api_events(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(base + '/api/events', headers={'Cookie': cookie_header})
    with opener.open(req) as response:
        assert response.status == 200

def test_api_personas(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(base + '/api/personas', headers={'Cookie': cookie_header})
    with opener.open(req) as response:
        assert response.status == 200

def test_api_dance_templates(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(base + '/api/dance-templates', headers={'Cookie': cookie_header})
    with opener.open(req) as response:
        assert response.status == 200


@pytest.mark.parametrize("endpoint, expected_code", [
    ('/api/workflow-library/file?id=unknown', 401),
    ('/api/google/drive/files', 401),
    ('/api/providers', 401),
    ('/api/voiceover/batches', 401),
    ('/api/history/detail?id=123', 401),
    ('/api/admin/users', 401),
    ('/api/voices', 401),
    ('/api/view', 401),
    ('/api/audio-export', 401),
    ('/api/productions', 401),
    ('/api/assets', 401),
    ('/api/orchestrator/resources', 401),
    ('/api/orchestrator/runs', 401),
    ('/api/orchestrator/reel-reviews', 401),
    ('/api/orchestrator/events', 401),
    ('/api/orchestrator/tasks', 401),
    ('/api/orchestrator/artifacts', 401),
    ('/api/orchestrator/tags', 401),
    ('/api/voices/file', 401),
    ('/api/crawl-videos', 401),
    ('/api/crawl-videos/preview', 401),
    ('/api/dance-library', 401),
    ('/api/dance-library/manage', 401),
    ('/api/provider-characters', 401),
])
def test_e2e_integration_bad_auth(test_server, endpoint, expected_code):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(
        base + endpoint,
        headers={'Origin': base}
    )
    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        if expected_code == 401 and endpoint == '/api/history/detail?id=123':
            assert e.code in (401, 404)
        else:
            assert e.code == expected_code
