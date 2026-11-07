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

def test_api_google_drive_files_bad_auth(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(base + '/api/google/drive/files')
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

def test_api_providers_bad_auth(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(
        base + '/api/providers',
        headers={'Origin': base}
    )
    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 401

def test_api_voiceover_batches_get_bad_auth(test_server):
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

def test_api_history_detail_bad_auth(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(
        base + '/api/history/detail?id=123',
        headers={'Origin': base}
    )
    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 401

def test_api_admin_users_get_bad_auth(test_server):
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

def test_api_voices_get_bad_auth(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(
        base + '/api/voices',
        headers={'Origin': base}
    )
    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 401

def test_api_view_bad_auth(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(
        base + '/api/view',
        headers={'Origin': base}
    )
    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 401

def test_api_audio_export_bad_auth(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(
        base + '/api/audio-export',
        headers={'Origin': base}
    )
    try:
        with opener.open(req) as response:
            pytest.fail("Expected HTTPError")
    except urllib.error.HTTPError as e:
        assert e.code == 401
