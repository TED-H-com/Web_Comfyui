def _test():
    import urllib.parse
    import socket
    import ssl
    import threading
    from unittest.mock import patch
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[0] / "webui"))
    import server

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

    # Mock recv for handshake
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

        for msg in server.comfy_ws_messages("test_client"):
            print("got:", msg)

_test()
