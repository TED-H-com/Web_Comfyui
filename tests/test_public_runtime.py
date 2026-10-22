"""Public distribution privacy boundaries, using only synthetic data."""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webui"))
import server


class PublicRuntimeTests(unittest.TestCase):
    def test_no_bundled_users_or_characters(self):
        self.assertEqual(server.seed_users(), {})
        self.assertEqual(server._seed_character_items(), [])
        self.assertEqual(server.AI_SISTER_PROFILE_SEEDS, {})
        self.assertEqual(server.BESTIE_PROFILE_SEEDS, {})

    def test_unconfigured_operator_never_authorizes_anonymous_user(self):
        with patch.object(server, "OPERATOR_EMAIL", ""):
            self.assertFalse(server.is_operator_user(None))
            self.assertFalse(server.is_operator_user({}))
            self.assertTrue(server.is_operator_user(server.LOCAL_SESSION_USER))

    def test_credentials_do_not_search_other_projects(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(server, "VOICE_LAB_ENV", None):
            with patch.object(Path, "read_text", side_effect=AssertionError("unexpected file access")):
                self.assertIsNone(server._lab_secret("OPENAI_API_KEY"))
            with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-test-value"}):
                self.assertEqual(server._lab_secret("OPENAI_API_KEY"), "synthetic-test-value")

    def test_explicit_secrets_file_and_environment_override(self):
        root = Path.home() / "agent-work/tmp/public-runtime-tests"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory:
            secret_file = Path(directory) / "synthetic.env"
            secret_file.write_text("OPENAI_API_KEY=synthetic-file-value\n")
            with patch.dict(os.environ, {}, clear=True), patch.object(server, "VOICE_LAB_ENV", secret_file), patch.object(server, "_LAB_SECRETS", None):
                self.assertEqual(server._lab_secret("OPENAI_API_KEY"), "synthetic-file-value")
                with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-env-value"}):
                    self.assertEqual(server._lab_secret("OPENAI_API_KEY"), "synthetic-env-value")

    def test_optional_voice_service_does_not_connect_unconfigured(self):
        with patch.object(server, "BREEZY_URL", ""), patch.object(urllib.request, "urlopen", side_effect=AssertionError("unexpected network")):
            self.assertEqual(server.breezy_persona_runtime()["personaIds"], [])

    def test_custom_voice_settings_are_used(self):
        with patch.object(server, "CUSTOM_ENGLISH_LORA", Path("/configured/voice/lora")), patch.object(server, "CUSTOM_ENGLISH_REFERENCE_AUDIO", Path("/configured/voice/reference.wav")), patch.object(server, "CUSTOM_ENGLISH_REFERENCE_TEXT", "Synthetic transcript"):
            self.assertEqual(server.voxcpm_lora_path("custom_english"), Path("/configured/voice/lora"))
            self.assertEqual(server.voxcpm_profile_reference("custom_english"), (Path("/configured/voice/reference.wav"), "Synthetic transcript"))

    def test_optional_scene_worker_requires_configured_remote(self):
        from novel_scene_art.worker import WorkerSettings
        with patch.dict(os.environ, {}, clear=True):
            settings = WorkerSettings.from_environment()
            self.assertEqual(settings.mode, "off")
            self.assertEqual(settings.oracle_origin, "")
        with patch.dict(os.environ, {"CASTLE_NOVEL_SCENE_ART_MODE": "run"}, clear=True):
            with self.assertRaisesRegex(Exception, "AISISTER_BASE"):
                WorkerSettings.from_environment()

    def test_local_first_run_setup_and_login(self):
        root = Path.home() / "agent-work/tmp/public-runtime-tests"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory:
            state = Path(directory)
            with patch.object(server, "AUTH_PATH", state / "auth.json"), patch.object(server, "USERS_PATH", state / "users.json"), patch.object(server, "SESSION_STORE_PATH", state / "sessions.json"), patch.object(server, "CANONICAL_WEB_ORIGIN", ""), patch.object(server, "LEGACY_WEB_HOSTS", set()):
                http = server.CastleThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
                thread = threading.Thread(target=http.serve_forever, daemon=True)
                thread.start()
                try:
                    base = f"http://127.0.0.1:{http.server_port}"
                    payload = json.dumps({"password": "synthetic-test-password"}).encode()
                    for endpoint in ("setup", "login"):
                        request = urllib.request.Request(base + f"/api/auth/{endpoint}", data=payload, headers={"Content-Type": "application/json"})
                        with urllib.request.urlopen(request, timeout=5) as response:
                            self.assertEqual(response.status, 200)
                            self.assertIn(server.SESSION_COOKIE, response.headers.get("Set-Cookie", ""))
                    self.assertTrue((state / "auth.json").is_file())
                    self.assertFalse((state / "auth.json").stat().st_mode & 0o077)
                finally:
                    http.shutdown()
                    http.server_close()
                    thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
