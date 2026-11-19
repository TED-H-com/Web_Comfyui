import time
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webui"))
import server

def test_websocket_unmask_performance():
    mask = b'\x12\x34\x56\x78'
    payload = os.urandom(1024 * 1024 * 5)

    start = time.time()
    res = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    old_time = time.time() - start

    start = time.time()
    mask_repeated = (mask * (len(payload) // 4 + 1))[:len(payload)]
    res_new = (int.from_bytes(payload, 'little') ^ int.from_bytes(mask_repeated, 'little')).to_bytes(len(payload), 'little')
    new_time = time.time() - start

    assert res == res_new
    print(f"Unmasking: old={old_time:.3f}s new={new_time:.3f}s")

    # ensure it's actually faster (or close to it)
    assert new_time < old_time

def test_opcode_2_handling():
    from unittest.mock import patch

    # Mock _ws_read_frame to yield an opcode 2 frame
    def _mock_read_frame(sock):
        if not getattr(_mock_read_frame, "called", False):
            _mock_read_frame.called = True
            return 2, b"test_payload"
        return 8, b""

    # Mock socket to avoid real connection
    class MockSocket:
        def __init__(self, *args, **kwargs):
            self.called = False
        def sendall(self, data):
            pass
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass
        def settimeout(self, timeout):
            pass

    # The while loop reads 1 byte at a time until \r\n\r\n
    def mock_recv_exact(sock, size):
        if not getattr(mock_recv_exact, "handshake_sent", False):
            mock_recv_exact.handshake_sent = 0
        handshake = b"HTTP/1.1 101 Switching Protocols\r\n\r\n"
        if mock_recv_exact.handshake_sent < len(handshake):
            res = handshake[mock_recv_exact.handshake_sent:mock_recv_exact.handshake_sent+size]
            mock_recv_exact.handshake_sent += size
            return res
        return b""

    with patch("server._ws_read_frame", side_effect=_mock_read_frame), \
         patch("socket.create_connection", return_value=MockSocket()), \
         patch("ssl.create_default_context", return_value=type("MockContext", (), {"wrap_socket": lambda self, s, **kwargs: s})()), \
         patch("server._recv_exact", side_effect=mock_recv_exact):

        # the generator logic yields payload for opcode 2 and breaks for opcode 8
        messages = list(server.comfy_ws_messages("test_client"))
        assert len(messages) == 1
        assert messages[0] == b"test_payload"
