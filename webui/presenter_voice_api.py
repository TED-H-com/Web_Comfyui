#!/usr/bin/env python3
"""Tailnet-only machine-to-machine API for presenter voice segments."""

from __future__ import annotations

import argparse
import urllib.parse
from http.server import ThreadingHTTPServer

import server as castle_web


class PresenterVoiceHandler(castle_web.Handler):
    """Expose only the narrow presenter-voice integration surface."""

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if not self.require_presenter_voice_service():
            return
        if path.startswith("/api/integrations/presenter-voice/jobs/") and path.endswith("/audio"):
            self.api_presenter_voice_audio(path)
            return
        if path.startswith("/api/integrations/presenter-voice/jobs/"):
            self.api_presenter_voice_status(path)
            return
        self.send_json({"ok": False, "error": "Not found"}, 404)

    def do_POST(self) -> None:
        if not self.origin_allowed():
            self.send_json({"ok": False, "error": "Forbidden origin"}, 403)
            return
        path = urllib.parse.urlparse(self.path).path
        if not self.require_presenter_voice_service():
            return
        if path == "/api/integrations/presenter-voice/jobs":
            self.api_presenter_voice_create()
            return
        self.send_json({"ok": False, "error": "Not found"}, 404)

    def do_PUT(self) -> None:
        self.send_json({"ok": False, "error": "Not found"}, 404)

    do_PATCH = do_PUT
    do_DELETE = do_PUT


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8191)
    args = parser.parse_args()
    castle_web.castle_orchestrator.initialize()
    httpd = ThreadingHTTPServer((args.host, args.port), PresenterVoiceHandler)
    print(f"Presenter voice API listening on http://{args.host}:{args.port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
