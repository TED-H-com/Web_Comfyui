#!/usr/bin/env python3
"""Launch the local Web_Comfyui UI against a separately installed ComfyUI."""
from __future__ import annotations

import argparse
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import sys
import urllib.parse
import urllib.request
from webui.config_env import apply_environment_aliases, legacy_name, public_name

ROOT = Path(__file__).resolve().parent
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class ConfigurationError(ValueError):
    pass


def load_repository_env(root: Path = ROOT) -> None:
    """Read only this checkout's .env; never evaluate shell code or expand values."""
    path = root / ".env"
    if not path.exists():
        return
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65_536:
        raise ConfigurationError("The repository .env must be a regular file smaller than 64 KiB")
    process_names = set(os.environ)
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not _ENV_NAME.fullmatch(key):
            raise ConfigurationError(f"Invalid .env assignment on line {number}")
        if key in {"HOME", "home", "CODEX_HOME", "PATH", "PYTHONPATH", "LD_PRELOAD", "LD_LIBRARY_PATH"}:
            raise ConfigurationError(f"Unsupported process setting on .env line {number}")
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ConfigurationError(f"Unclosed quote on .env line {number}")
            value = value[1:-1]
        if "\x00" in value:
            raise ConfigurationError(f"Invalid .env value on line {number}")
        # A process setting wins over .env even when the two use different aliases.
        legacy = legacy_name(key)
        if not any(name in process_names for name in {key, legacy, public_name(legacy)}):
            os.environ.setdefault(key, value)


def local_comfy_url(value: str) -> str:
    """Require a literal loopback HTTP endpoint; do not resolve arbitrary hosts."""
    try:
        parsed = urllib.parse.urlsplit(value)
        host = parsed.hostname or ""
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
        port = parsed.port
    except ValueError as error:
        raise ConfigurationError("COMFYUI_URL must be a loopback HTTP URL") from error
    if (not loopback or parsed.scheme != "http" or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"} or port == 0):
        raise ConfigurationError("COMFYUI_URL must be a loopback HTTP URL without credentials or a path")
    return value.rstrip("/")


def _absolute_setting(name: str, default: Path) -> Path:
    path = Path(os.environ.get(name) or default).expanduser()
    if not path.is_absolute():
        raise ConfigurationError(f"{name} must be an absolute path")
    return path


def _filesystem_type(path: Path) -> str:
    """Find the longest containing Linux mount without creating the directory."""
    mounts = Path("/proc/self/mountinfo")
    if not mounts.is_file():
        return ""
    resolved = path.resolve()
    best, result = 0, ""
    for line in mounts.read_text(encoding="utf-8").splitlines():
        left, separator, right = line.partition(" - ")
        fields = left.split()
        if not separator or len(fields) < 5:
            continue
        mount = Path(re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), fields[4]))
        if resolved.is_relative_to(mount) and len(mount.parts) >= best:
            best, result = len(mount.parts), right.split()[0]
    return result


def prepare_environment(*, port: int | None = None, create_dirs: bool = True) -> dict[str, Path | str | int]:
    """Set application paths before importing any module with runtime defaults."""
    apply_environment_aliases()
    comfy = _absolute_setting("COMFY_HOME", Path.home() / "comfyui")
    data = _absolute_setting("WEB_COMFYUI_DATA_DIR", Path.home() / ".local/share/web-comfyui")
    cache = _absolute_setting("WEB_COMFYUI_CACHE_DIR", Path.home() / ".cache/web-comfyui")
    temporary = _absolute_setting("WEB_COMFYUI_TMP_DIR", cache / "tmp")
    if temporary.resolve().is_relative_to(Path("/dev/shm")) or _filesystem_type(temporary) in {"tmpfs", "ramfs"}:
        raise ConfigurationError("WEB_COMFYUI_TMP_DIR must be on disk, not a RAM-backed filesystem")
    host = os.environ.get("CASTLE_COMFY_WEB_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "localhost"}:
        raise ConfigurationError("The Web UI must bind to 127.0.0.1")
    try:
        selected_port = port if port is not None else int(os.environ.get("CASTLE_COMFY_WEB_PORT", "8190"))
    except ValueError as error:
        raise ConfigurationError("WEB_COMFYUI_PORT must be an integer") from error
    if not 1 <= selected_port <= 65535:
        raise ConfigurationError("The Web UI port must be between 1 and 65535")
    url = local_comfy_url(os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188"))
    os.environ["CASTLE_COMFY_WEB_HOST"] = "127.0.0.1"
    os.environ["CASTLE_COMFY_WEB_PORT"] = str(selected_port)
    os.environ["COMFYUI_URL"] = url
    os.environ["TMPDIR"] = str(temporary)
    defaults = {
        "COMFYUI_INPUT_DIR": comfy / "input", "COMFYUI_OUTPUT_DIR": comfy / "output",
        "COMFYUI_TEMP_DIR": comfy / "temp", "COMFYUI_MODELS_DIR": comfy / "models",
        "CASTLE_H3_COMFY_DIR": comfy, "CASTLE_H3_MODELS_DIR": comfy / "models",
        "CASTLE_COMFY_AUTH_PATH": data / "auth/auth.json",
        "CASTLE_COMFY_USERS_PATH": data / "auth/users.json",
        "CASTLE_COMFY_SESSION_STORE_PATH": data / "auth/sessions.json",
        "CASTLE_PROVENANCE_KEY_PATH": data / "auth/provenance.key",
        "CASTLE_GOOGLE_TOKEN_PATH": data / "auth/google_drive_token.json",
        "CASTLE_HIDDEN_PATH": data / "gallery/hidden.json",
        "CASTLE_CHARACTER_DIR": data / "characters", "CASTLE_VOICE_DIR": data / "voices",
        "CASTLE_ORCH_DATA_ROOT": data / "orchestrator", "CASTLE_ORCH_CACHE_ROOT": cache / "orchestrator",
        "CASTLE_PROVIDER_DATABASE_PATH": data / "provider-fabric/provider-fabric.sqlite3",
        "CASTLE_PROVIDER_CREDENTIAL_ROOT": data / "provider-secrets",
        "CASTLE_MEDIA_CATALOG": data / "media/library.sqlite3",
        "CASTLE_DANCE_LIBRARY_CATALOG": data / "dance-library/catalog.json",
        "CASTLE_DANCE_LIBRARY_CACHE": cache / "dance-library",
        "CASTLE_AUDIO_EXPORT_CACHE_DIR": cache / "audio-exports",
        "CASTLE_H3_WEB_STATE_ROOT": cache / "minimax-h3-web",
        "CASTLE_CLIPMODE_PHOTOSHOOT_STATE_ROOT": cache / "clipmode-character-photoshoot",
        "CASTLE_GUARDED_JOB_DIR": cache / "guarded-voice",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, str(value))
    allowed = {item.strip() for item in os.environ.get("CASTLE_ALLOWED_ORIGIN_HOSTS", "").split(",") if item.strip()}
    allowed.update({f"127.0.0.1:{selected_port}", f"localhost:{selected_port}"})
    os.environ["CASTLE_ALLOWED_ORIGIN_HOSTS"] = ",".join(sorted(allowed))
    if create_dirs:
        for directory in (data, cache, temporary, data / "auth", data / "gallery", data / "characters", data / "voices"):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return {"comfy": comfy, "data": data, "cache": cache, "temporary": temporary, "url": url, "port": selected_port}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def readiness_report(config: dict) -> dict:
    """Read-only checks; no model execution, installs, secrets, or environment dumps."""
    dependencies = {"pillow": importlib.util.find_spec("PIL") is not None,
                    "ffmpeg": bool(shutil.which("ffmpeg")), "ffprobe": bool(shutil.which("ffprobe")),
                    "yt_dlp": bool(shutil.which("yt-dlp"))}
    report = {"python_supported": sys.version_info >= (3, 11), "platform_supported": sys.platform.startswith("linux"),
              "comfy_directory_exists": config["comfy"].is_dir(), "comfy_api_reachable": False,
              "dependencies": dependencies,
              "integrations_configured": {"openai": bool(os.environ.get("OPENAI_API_KEY")),
                                          "gemini": bool(os.environ.get("GEMINI_API_KEY")),
                                          "explicit_secrets_file": bool(os.environ.get("CASTLE_VOICE_LAB_ENV")),
                                          "breezy_voice": bool(os.environ.get("CASTLE_BREEZY_URL"))}}
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        with opener.open(config["url"] + "/system_stats", timeout=2) as response:
            raw = response.read(1_048_577)
        if len(raw) <= 1_048_576:
            stats = json.loads(raw)
            report["comfy_api_reachable"] = isinstance(stats, dict) and isinstance(stats.get("system"), dict)
            devices = stats.get("devices", []) if isinstance(stats, dict) else []
            report["comfy_gpu_reported"] = any(isinstance(device, dict) and device.get("type") in {"cuda", "mps", "xpu"} for device in devices)
    except Exception:
        # Exception text can contain URL credentials or private server details.
        report["comfy_api_reachable"] = False
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Report read-only readiness as JSON and exit")
    parser.add_argument("--no-env", action="store_true", help="Ignore the checkout's .env file")
    parser.add_argument("--port", type=int, help="Loopback UI port (default 8190)")
    args = parser.parse_args(argv)
    try:
        if not args.no_env:
            load_repository_env()
        config = prepare_environment(port=args.port, create_dirs=not args.check)
    except (ConfigurationError, OSError, UnicodeError) as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    if args.check:
        report = readiness_report(config)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["python_supported"] and report["platform_supported"] and report["dependencies"]["pillow"] and report["comfy_api_reachable"] else 1
    if sys.version_info < (3, 11) or not sys.platform.startswith("linux"):
        print("Use Python 3.11+ on Linux or WSL2.", file=sys.stderr)
        return 2
    if importlib.util.find_spec("PIL") is None:
        print("Install Web UI requirements first: python -m pip install -r requirements-webui.txt", file=sys.stderr)
        return 2
    sys.path.insert(0, str(ROOT / "webui"))
    import server
    sys.argv = [str(ROOT / "webui/server.py"), "--host", "127.0.0.1", "--port", str(config["port"])]
    server.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
