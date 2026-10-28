import json
import urllib.error
import urllib.request
from unittest.mock import patch, MagicMock

import pytest

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'webui'))

import server

def test_request_json_success():
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"success": true}'
        mock_urlopen.return_value.__enter__.return_value = mock_response

        result = server.request_json("/test-path")

        assert result == {"success": True}
        args, kwargs = mock_urlopen.call_args
        assert args[0].get_full_url() == f"{server.DEFAULT_COMFY}/test-path"


def test_proxy_json_success():
    class DummyHandler:
        def __init__(self):
            self.sent_json = None
            self.error_json = None
        def send_json(self, data, status=200):
            self.sent_json = data
        def send_error_json(self, error, status=500):
            self.error_json = error

    handler = DummyHandler()

    with patch("server.request_json") as mock_request_json:
        mock_request_json.return_value = {"test": "data"}

        server.Handler.proxy_json(handler, "/test-path")

        assert handler.sent_json == {"test": "data"}
        assert handler.error_json is None


def test_proxy_json_error():
    class DummyHandler:
        def __init__(self):
            self.sent_json = None
            self.error_json = None
        def send_json(self, data, status=200):
            self.sent_json = data
        def send_error_json(self, error, status=500):
            self.error_json = error

    handler = DummyHandler()

    with patch("server.request_json") as mock_request_json:
        mock_request_json.side_effect = urllib.error.URLError("test error")

        server.Handler.proxy_json(handler, "/test-path")

        assert handler.sent_json is None
        assert handler.error_json is not None

def test_websocket_progress():
    class DummySocket:
        def __init__(self, data):
            self.data = data
            self.sent = b""
        def recv(self, size):
            if not self.data:
                return b""
            chunk = self.data[:size]
            self.data = self.data[size:]
            return chunk
        def sendall(self, data):
            self.sent += data
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def settimeout(self, timeout):
            pass

    # Example 1: a text message payload containing progress
    # Opcode 1 (text), size 10, unmasked, payload "1234567890"
    payload = b"1234567890"
    frame1 = bytes([0x81, len(payload)]) + payload

    # Example 2: a close frame
    # Opcode 8 (close), size 0, unmasked
    frame2 = bytes([0x88, 0])

    # Send handshake response (simulated HTTP 101) + frame1 + frame2
    sock_data = b"HTTP/1.1 101 Switching Protocols\r\n\r\n" + frame1 + frame2

    with patch("socket.create_connection") as mock_conn:
        mock_conn.return_value = DummySocket(sock_data)

        messages = list(server.comfy_ws_messages("test_client"))

        assert messages == ["1234567890"]

def test_api_generate_submit_workflow():
    class DummyHandler:
        def __init__(self):
            self.sent_json = None
            self.error_json = None
            self._current_user = {"email": "test@example.com"}
        def send_json(self, data, status=200):
            self.sent_json = data
        def send_error_json(self, error, status=500):
            self.error_json = error
        def read_json(self, limit):
            return {
                "featureId": "test-feature",
                "settings": {"prompt": "a test prompt"}
            }
        def current_user(self):
            return self._current_user
        def is_orchestrator_service_authenticated(self):
            return False
        def require_orchestrator_auth(self):
            return False
        def require_auth(self):
            return True

    handler = DummyHandler()

    with patch("server.FEATURE_BY_ID", {"test-feature": {"builder": "test-builder", "apiEnabled": True}}), \
         patch("server.BUILDERS", {"test-builder": lambda s, u: {"3": {"class_type": "KSampler"}}}), \
         patch("server.character_generation_context") as mock_context, \
         patch("server.workflow_extra_data") as mock_extra_data, \
         patch("server.request_json") as mock_request_json, \
         patch("server.record_job_attribution") as mock_attribution:

        mock_context.return_value = {}
        mock_extra_data.return_value = {}
        mock_request_json.return_value = {"prompt_id": "test-prompt-123"}

        server.Handler.api_generate(handler)

        assert handler.sent_json is not None
        assert handler.sent_json.get("prompt_id") == "test-prompt-123"

        args, kwargs = mock_request_json.call_args
        assert args[0] == "/prompt"
        assert "payload" in kwargs or len(args) > 1

        if "payload" in kwargs:
            payload = kwargs["payload"]
        else:
            payload = args[1]

        assert "prompt" in payload
        assert "KSampler" == payload["prompt"]["3"]["class_type"]

def test_api_queue_idle():
    class DummyHandler:
        def __init__(self):
            self.sent_json = None
            self.error_json = None
            self._current_user = {"email": "test@example.com"}
        def send_json(self, data, status=200):
            self.sent_json = data
        def send_error_json(self, error, status=500):
            self.error_json = error
        def current_user(self):
            return self._current_user

    handler = DummyHandler()

    with patch("server.request_json") as mock_request_json, \
         patch("server.load_job_index") as mock_job_index, \
         patch("server.local_job_snapshot") as mock_local_snapshot:

        mock_request_json.return_value = {"queue_running": [], "queue_pending": []}
        mock_job_index.return_value = {}
        mock_local_snapshot.return_value = []

        server.Handler.api_queue(handler)

        assert handler.sent_json is not None
        assert handler.sent_json.get("ok") is True
        assert handler.sent_json.get("scope") == "own"
        assert handler.sent_json.get("jobs") == []
