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
    req = urllib.request.Request(base + '/api/google/drive/files', headers={'Cookie': cookie_header})
    try:
        with opener.open(req) as response:
            pass # Requires auth
    except urllib.error.HTTPError as e:
        assert e.code == 400


def test_api_workflows_file(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(base + '/api/workflow-library/file?id=unknown', headers={'Cookie': cookie_header})
    try:
        with opener.open(req) as response:
            pass
    except urllib.error.HTTPError as e:
        assert e.code == 400


def test_api_workflows_file_bad_auth(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(base + '/api/workflow-library/file?id=unknown')
    try:
        with opener.open(req) as response:
            pass
    except urllib.error.HTTPError as e:
        assert e.code == 401

def test_api_google_drive_files_bad_auth(test_server):
    base, opener, cookie_header = test_server
    req = urllib.request.Request(base + '/api/google/drive/files')
    try:
        with opener.open(req) as response:
            pass
    except urllib.error.HTTPError as e:
        assert e.code == 401
