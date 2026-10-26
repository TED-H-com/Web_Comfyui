"""Lightweight launcher tests: no model downloads, production state, or GPU calls."""
from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("public_launcher", ROOT / "run.py")
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def setUp(self):
        root = Path.home() / "agent-work/tmp/public-launcher-tests"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def environment(self):
        return patch.dict(os.environ, {
            "COMFY_HOME": str(self.root / "ComfyUI"),
            "WEB_COMFYUI_DATA_DIR": str(self.root / "state"),
            "WEB_COMFYUI_CACHE_DIR": str(self.root / "cache"),
        }, clear=True)

    def test_env_is_literal_and_process_environment_wins(self):
        marker = self.root / "must-not-exist"
        literal = f"$(touch {marker})"
        (self.root / ".env").write_text(f"OPENAI_API_KEY='{literal}'\nGEMINI_API_KEY=from-file\n")
        with patch.dict(os.environ, {"GEMINI_API_KEY": "from-process"}, clear=True):
            launcher.load_repository_env(self.root)
            self.assertEqual(os.environ["OPENAI_API_KEY"], literal)
            self.assertEqual(os.environ["GEMINI_API_KEY"], "from-process")
        self.assertFalse(marker.exists())

    def test_no_parent_env_search(self):
        (self.root / ".env").write_text("GEMINI_API_KEY=parent-file\n")
        checkout = self.root / "checkout"
        checkout.mkdir()
        with patch.dict(os.environ, {}, clear=True):
            launcher.load_repository_env(checkout)
            self.assertNotIn("GEMINI_API_KEY", os.environ)

    def test_alias_precedence_across_process_and_env_file(self):
        (self.root / '.env').write_text('WEB_COMFYUI_PORT=8195\n')
        with self.environment(), patch.dict(os.environ, {'CASTLE_COMFY_WEB_PORT': '8196'}):
            launcher.load_repository_env(self.root)
            self.assertEqual(launcher.prepare_environment(create_dirs=False)['port'], 8196)
        (self.root / '.env').write_text('CASTLE_COMFY_WEB_PORT=8195\n')
        with self.environment(), patch.dict(os.environ, {'WEB_COMFYUI_PORT': '8197'}):
            launcher.load_repository_env(self.root)
            self.assertEqual(launcher.prepare_environment(create_dirs=False)['port'], 8197)
        for lines in [('CASTLE_COMFY_WEB_PORT=8195', 'WEB_COMFYUI_PORT=8198'),
                      ('WEB_COMFYUI_PORT=8198', 'CASTLE_COMFY_WEB_PORT=8195')]:
            (self.root / '.env').write_text('\n'.join(lines) + '\n')
            with self.environment():
                launcher.load_repository_env(self.root)
                self.assertEqual(launcher.prepare_environment(create_dirs=False)['port'], 8198)
                self.assertEqual(launcher.prepare_environment(port=8199, create_dirs=False)['port'], 8199)

    def test_env_error_does_not_echo_value(self):
        value = "synthetic-sensitive-value"
        (self.root / ".env").write_text(f"export OPENAI_API_KEY={value}\n")
        with self.assertRaises(launcher.ConfigurationError) as caught:
            launcher.load_repository_env(self.root)
        self.assertNotIn(value, str(caught.exception))

    def test_readonly_configuration_preserves_backend_and_custom_paths(self):
        with self.environment(), patch.dict(os.environ, {"COMFYUI_URL": "http://127.0.0.1:8189", "COMFYUI_OUTPUT_DIR": str(self.root / "custom-outputs")}):
            config = launcher.prepare_environment(port=8192, create_dirs=False)
            self.assertEqual(config["url"], "http://127.0.0.1:8189")
            self.assertEqual(os.environ["COMFYUI_OUTPUT_DIR"], str(self.root / "custom-outputs"))
            self.assertEqual(os.environ["COMFYUI_MODELS_DIR"], str(self.root / "ComfyUI/models"))
            self.assertEqual(os.environ["CASTLE_PROVIDER_DATABASE_PATH"], str(self.root / "state/provider-fabric/provider-fabric.sqlite3"))
            self.assertIn("127.0.0.1:8192", os.environ["CASTLE_ALLOWED_ORIGIN_HOSTS"])
            self.assertFalse((self.root / "state").exists())
            self.assertFalse((self.root / "cache").exists())

    def test_loopback_and_ram_temp_enforced(self):
        for url in ("http://example.com:8188", "http://127.0.0.1:8188/?token=secret", "http://user:password@127.0.0.1:8188"):
            with self.assertRaises(launcher.ConfigurationError):
                launcher.local_comfy_url(url)
        with self.environment(), patch.dict(os.environ, {"CASTLE_COMFY_WEB_HOST": "0.0.0.0"}):
            with self.assertRaises(launcher.ConfigurationError):
                launcher.prepare_environment(create_dirs=False)
        with self.environment(), patch.object(launcher, "_filesystem_type", return_value="tmpfs"):
            with self.assertRaises(launcher.ConfigurationError):
                launcher.prepare_environment(create_dirs=False)

    def test_check_redacts_credentials_and_does_not_import_server(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, size): return b'{"system": {}, "devices": [{"type": "cpu"}]}'
        class Opener:
            def open(self, url, timeout): return Response()
        with self.environment(), patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-secret-do-not-print"}), patch.object(launcher.urllib.request, "build_opener", return_value=Opener()):
            config = launcher.prepare_environment(create_dirs=False)
            report = launcher.readiness_report(config)
            self.assertTrue(report["comfy_api_reachable"])
            self.assertFalse(report["comfy_gpu_reported"])
            self.assertNotIn("synthetic-secret-do-not-print", json.dumps(report))
            self.assertFalse((self.root / "state").exists())


if __name__ == "__main__":
    unittest.main()
