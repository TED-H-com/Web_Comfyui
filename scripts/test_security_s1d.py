"""Offline checks for the S1d download, H3 name, and rename guards."""
import hashlib
import io
import json
import os
import socket
import stat
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webui"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "minimax_h3"))

import server  # noqa: E402
from provider_fabric.h3_worker import (  # noqa: E402
    H3SubmissionProcessor,
    h3_upload_basename,
    load_or_create_upload_name_key,
)
from provider_fabric.provider_worker import DefiniteLocalFailure  # noqa: E402
from run_provider_graph import SAFE_UPLOAD  # noqa: E402
from test_h3_provider_worker import H3ProviderWorkerTests  # noqa: E402

ADMIN = {"email": "administrator" + "@" + "example.invalid", "role": "admin", "displayName": "administrator"}
RESERVED_RENAME_ERROR = "輸入素材的新檔名不可以用 castle_、library_ 或 reference_opc_ 開頭（系統保留名稱）"
UNSAFE_FORMAT_ERROR = "這個網址的影片只提供無法安全下載的串流格式，請改為上傳影片檔"
KEY_ERROR = "H3 upload-name key is not a private 32-byte file"


@pytest.fixture
def state(tmp_path, monkeypatch):
    input_dir, output_dir, temp_dir = (tmp_path / name for name in ("input", "output", "temp"))
    for directory in (input_dir, output_dir, temp_dir):
        directory.mkdir()
    monkeypatch.setattr(server, "COMFY_INPUT", input_dir)
    monkeypatch.setattr(server, "COMFY_OUTPUT", output_dir)
    monkeypatch.setattr(server, "COMFY_TEMP", temp_dir)
    monkeypatch.setattr(server, "VIEW_DIRS", {"input": input_dir, "output": output_dir, "temp": temp_dir})
    monkeypatch.setattr(server, "HIDDEN_PATH", tmp_path / ".hidden.json")
    monkeypatch.setattr(server, "JOB_INDEX_PATH", tmp_path / "jobs.json")
    monkeypatch.setattr(server, "history_gallery_item_for_view", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "load_job_index", lambda: {})
    monkeypatch.setattr(server.library_service, "catalog_path", lambda: tmp_path / "no-catalog.json")
    return SimpleNamespace(root=tmp_path, input=input_dir, output=output_dir, temp=temp_dir)


def handler(body):
    target = object.__new__(server.Handler)
    target.headers = {}
    target.current_user = lambda: ADMIN
    target.read_json = lambda *args: body
    target.status = None
    target.response_headers = {}
    target.wfile = io.BytesIO()
    target.send_response = lambda status: setattr(target, "status", status)
    target.send_header = lambda name, value: target.response_headers.__setitem__(name, str(value))
    target.end_headers = lambda: None
    return target


def response_json(target):
    return json.loads(target.wfile.getvalue())


def rename_input(state, old_name, new_name, view_type="input", payload=b"synthetic-fixture"):
    directory = {"input": state.input, "output": state.output, "temp": state.temp}[view_type]
    source = directory / old_name
    source.write_bytes(payload)
    target = handler({"filename": old_name, "subfolder": "", "type": view_type, "name": new_name})
    target.api_history_rename()
    return target.status, response_json(target), source, directory / new_name


class FakeProxy:
    def __init__(self):
        self.url = "http://127.0.0.1:9"
        self.refusals = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def install_download_fakes(monkeypatch, input_dir, calls):
    monkeypatch.setattr(server, "COMFY_INPUT", input_dir)
    monkeypatch.setattr(server, "CRAWL_DIR", input_dir)
    monkeypatch.setattr(server.time, "time", lambda: 1700000000)
    monkeypatch.setattr(server.secrets, "token_hex", lambda n=16: "ab" * n)
    monkeypatch.setattr(server, "_resolve_yt_dlp", lambda: "/fixture/yt-dlp")
    monkeypatch.setattr(server, "_crawl_cookie_file", lambda url: None)
    monkeypatch.setattr(server, "_video_codec", lambda path: "h264")
    monkeypatch.setattr(server.socket, "getaddrinfo", lambda host, port, *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port or 0)),
    ])
    monkeypatch.setattr(server.egress_guard, "GlobalOnlyProxy", lambda: FakeProxy())

    def run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        filename = argv[argv.index("-o") + 1].replace("%(title).40s", "clip").replace("%(ext).16s", "mp4")
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        Path(filename).write_bytes(b"video")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(server, "subprocess", SimpleNamespace(
        run=run,
        TimeoutExpired=subprocess.TimeoutExpired,
        CompletedProcess=subprocess.CompletedProcess,
    ))


def test_d2b_guest_format_lock_and_operator_argv_matches_s1c(tmp_path, monkeypatch):
    input_dir = tmp_path / "input"
    calls = []
    install_download_fakes(monkeypatch, input_dir, calls)
    url = "https://example.invalid/clip"
    server.fetch_video_url(url, "slot", shared_cookies=False)
    server.fetch_video_url(url, "slot", shared_cookies=True)
    guest, operator = calls[0][0], calls[1][0]
    # Literal operator argv captured once from the frozen S1c tree for this synthetic input.
    output = str(
        input_dir / "castle_url_1700000000_abababababababababababababababab_slot_%(title).40s.%(ext).16s"
    )
    expected_operator = [
        "/fixture/yt-dlp",
        "--no-playlist",
        "--restrict-filenames",
        "--no-progress",
        "--max-filesize", "268435456",
        "-f", "mp4/best[ext=mp4]/best",
        "-o", output,
        "--",
        url,
    ]
    assert guest[0] == "/fixture/yt-dlp"
    assert guest[1] == "--ignore-config"
    assert guest[guest.index("-f") + 1] == server.GUEST_VIDEO_FORMAT
    assert operator == expected_operator
    assert "env" not in calls[1][1]
    expected_guest = list(expected_operator)
    expected_guest.insert(1, "--ignore-config")
    expected_guest[expected_guest.index("-f") + 1] = server.GUEST_VIDEO_FORMAT
    dash = expected_guest.index("--")
    expected_guest[dash:dash] = ["--proxy", "http://127.0.0.1:9"]
    assert guest == expected_guest
    assert calls[0][1]["env"]["HTTPS_PROXY"] == "http://127.0.0.1:9"
    assert "NO_PROXY" not in calls[0][1]["env"] and "no_proxy" not in calls[0][1]["env"]
    assert "--ignore-config" not in operator
    assert operator[operator.index("-f") + 1] == "mp4/best[ext=mp4]/best"


FORMAT_CASES = r"""
import json, os, sys
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import yt_dlp
from yt_dlp.utils import DownloadError, ExtractorError
fmt = sys.argv[2]
def one(protocol, height, fid, url=None):
    item = {
        "format_id": fid,
        "url": url or ("https://cdn.example.invalid/" + fid + ".mp4"),
        "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "height": height,
    }
    if protocol is not None:
        item["protocol"] = protocol
    return item
def info(formats):
    return {
        "id": "vid", "title": "clip", "extractor": "generic", "extractor_key": "Generic",
        "webpage_url": "https://example.invalid/", "formats": formats,
    }
def select(formats):
    ydl = yt_dlp.YoutubeDL({"format": fmt, "quiet": True, "simulate": True})
    try:
        result = ydl.process_ie_result(info(formats), download=False)
    except (ExtractorError, DownloadError) as exc:
        return {"status": "unavailable", "exc": type(exc).__name__, "phrase": "Requested format is not available" in str(exc)}
    return {"status": "selected", "format_id": result.get("format_id"), "protocol": result.get("protocol"), "height": result.get("height")}
cases = [
    ("rtmp-only", [one("rtmp_ffmpeg", 720, "rtmp-only")], "unavailable", None),
    ("prefer-https", [one("rtmp_ffmpeg", 1080, "tall"), one("https", 360, "short")], "selected", "short"),
    ("m3u8_native", [one("m3u8_native", 360, "m3u8-native")], "selected", "m3u8-native"),
    ("m3u8", [one("m3u8", 360, "m3u8")], "selected", "m3u8"),
    ("http_dash_segments", [one("http_dash_segments", 360, "dash")], "selected", "dash"),
    ("http", [one("http", 360, "http")], "selected", "http"),
]
for protocol in ("rtmp", "rtmpe", "rtsp", "mms", "websocket_frag", "f4m", "ism", "mhtml", "fc2_live", "niconico_live"):
    cases.append((protocol, [one(protocol, 360, protocol)], "unavailable", None))
cases.append(("missing-protocol", [one(None, 360, "missing", "rtmp://cdn.example.invalid/live")], "unavailable", None))
rows = []
for name, formats, _expected, _fid in cases:
    row = select(formats)
    row["case"] = name
    rows.append(row)
json.dump({"version": yt_dlp.version.__version__, "rows": rows}, sys.stdout)
"""


def installed_ytdlp():
    """Interpreter and package directory of the yt-dlp shim the server would run."""
    try:
        shim = server._resolve_yt_dlp()
    except RuntimeError:
        pytest.skip("installed yt-dlp not visible")
    shim_path = Path(shim)
    try:
        first = shim_path.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except OSError:
        pytest.skip("installed yt-dlp not visible")
    if not first.startswith("#!"):
        pytest.skip("installed yt-dlp not visible")
    interpreter = Path(first[2:].strip().split()[0])
    if not interpreter.is_file() or not os.access(interpreter, os.X_OK):
        pytest.skip("installed yt-dlp not visible")
    package = None
    for candidate in sorted((interpreter.parent.parent / "lib").glob("python*/site-packages")):
        if (candidate / "yt_dlp" / "__init__.py").is_file():
            package = candidate
            break
    if package is None:
        pytest.skip("installed yt-dlp not visible")
    return interpreter, package


def test_d2b_installed_ytdlp_format_selector(tmp_path, capsys):
    interpreter, package = installed_ytdlp()
    cache_dir = tmp_path / "cache"
    config_dir = tmp_path / "config"
    cache_dir.mkdir()
    config_dir.mkdir()
    script = tmp_path / "format_select.py"
    script.write_text(FORMAT_CASES)
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["XDG_CACHE_HOME"] = str(cache_dir)
    env["XDG_CONFIG_HOME"] = str(config_dir)
    completed = subprocess.run(
        [str(interpreter), "-I", "-B", str(script), str(package), server.GUEST_VIDEO_FORMAT],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-500:]
    payload = json.loads(completed.stdout)
    print("yt-dlp version:", payload["version"])
    expected = {
        "rtmp-only": ("unavailable", None),
        "prefer-https": ("selected", "short"),
        "m3u8_native": ("selected", "m3u8-native"),
        "m3u8": ("selected", "m3u8"),
        "http_dash_segments": ("selected", "dash"),
        "http": ("selected", "http"),
        "missing-protocol": ("unavailable", None),
    }
    for protocol in ("rtmp", "rtmpe", "rtsp", "mms", "websocket_frag", "f4m", "ism", "mhtml", "fc2_live", "niconico_live"):
        expected[protocol] = ("unavailable", None)
    assert {row["case"] for row in payload["rows"]} == set(expected)
    for row in payload["rows"]:
        status, format_id = expected[row["case"]]
        assert row["status"] == status
        if status == "unavailable":
            assert row["phrase"] is True
            assert row["exc"] in {"ExtractorError", "DownloadError"}
        else:
            assert row["format_id"] == format_id
            if row["case"] == "prefer-https":
                assert row["protocol"] == "https"
                assert row["height"] == 360


def test_d2b_unavailable_format_error_is_mapped_for_guests_only(tmp_path, monkeypatch):
    calls = []
    install_download_fakes(monkeypatch, tmp_path / "input", calls)

    def fail(argv, **kwargs):
        calls.append((list(argv), kwargs))
        return subprocess.CompletedProcess(
            argv, 1, stdout="prefix ", stderr="Requested format is not available",
        )

    monkeypatch.setattr(server.subprocess, "run", fail)
    with pytest.raises(ValueError, match=UNSAFE_FORMAT_ERROR):
        server.fetch_video_url("https://example.invalid/clip", "slot", shared_cookies=False)
    with pytest.raises(ValueError, match="下載失敗") as caught:
        server.fetch_video_url("https://example.invalid/clip", "slot", shared_cookies=True)
    assert UNSAFE_FORMAT_ERROR not in str(caught.value)


def test_h3_keyed_name_is_stable_and_not_content_derived(tmp_path):
    helper = H3ProviderWorkerTests()
    helper.path = tmp_path / "avatar.png"
    helper.path.write_bytes(b"h3-avatar")
    helper.observed = []
    key = bytes(range(32))
    other_key = bytes(range(32, 64))

    def make(upload_key):
        return H3SubmissionProcessor(
            lambda owner, asset: helper.path,
            helper.executor,
            profile_resolver=helper.profile,
            upload_name_key=upload_key,
        )

    bundle = helper.bundle()
    make(key)(bundle)
    make(key)(bundle)
    first, second = helper.observed
    digest = hashlib.sha256(b"h3-avatar").hexdigest()
    assert first.upload_basename == second.upload_basename
    assert first.graph_sha256 == second.graph_sha256
    assert first.upload_basename == h3_upload_basename(key, digest, ".png")
    assert SAFE_UPLOAD.fullmatch(first.upload_basename)
    assert first.upload_basename != "castle_h3_" + digest[:32] + ".png"
    make(other_key)(bundle)
    assert helper.observed[-1].upload_basename != first.upload_basename
    assert h3_upload_basename(other_key, digest, ".png") != first.upload_basename
    plain = H3SubmissionProcessor(
        lambda owner, asset: helper.path,
        helper.executor,
        profile_resolver=helper.profile,
    )
    assert isinstance(plain._upload_name_key, bytes) and len(plain._upload_name_key) == 32
    with pytest.raises(ValueError):
        H3SubmissionProcessor(
            lambda owner, asset: helper.path,
            helper.executor,
            profile_resolver=helper.profile,
            upload_name_key=b"\x00" * 31,
        )
    with pytest.raises(DefiniteLocalFailure, match="H3 asset digest is invalid"):
        h3_upload_basename(key, "A" * 64, ".png")
    with pytest.raises(ValueError):
        h3_upload_basename(b"\x00" * 31, digest, ".png")


def write_key(path, payload, mode):
    path.write_bytes(payload)
    os.chmod(path, mode)


def test_h3_upload_name_key_file(tmp_path):
    created = tmp_path / "created.key"
    first = load_or_create_upload_name_key(created)
    info = created.lstat()
    assert stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode)
    assert info.st_size == 32 and (info.st_mode & 0o777) == 0o600
    assert len(first) == 32
    assert load_or_create_upload_name_key(created) == first

    reused = tmp_path / "reused.key"
    payload = bytes(range(32))
    write_key(reused, payload, 0o600)
    inode = reused.lstat().st_ino
    assert load_or_create_upload_name_key(reused) == payload
    assert reused.read_bytes() == payload and reused.lstat().st_ino == inode

    target = tmp_path / "target.key"
    write_key(target, payload, 0o600)
    linked = tmp_path / "linked.key"
    linked.symlink_to(target)
    short = tmp_path / "short.key"
    write_key(short, b"x" * 31, 0o600)
    grouped = tmp_path / "grouped.key"
    write_key(grouped, payload, 0o640)
    for path in (linked, short, grouped):
        with pytest.raises(RuntimeError, match=KEY_ERROR):
            load_or_create_upload_name_key(path)

    raced = tmp_path / "race"
    raced.mkdir()
    key_path = raced / "h3-upload-name.key"
    barrier = threading.Barrier(2)
    found, errors = [], []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        try:
            value = load_or_create_upload_name_key(key_path)
        except Exception as exc:
            with lock:
                errors.append(type(exc).__name__)
            return
        with lock:
            found.append(value)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(found) == 2 and found[0] == found[1] and len(found[0]) == 32
    assert [item.name for item in raced.iterdir()] == ["h3-upload-name.key"]




@pytest.mark.parametrize("new_name", ["Castle_x.png", "LIBRARY_x.png"])
def test_kr_reserved_input_rename_is_refused(state, new_name):
    status, body, source, destination = rename_input(state, "clip.png", new_name)
    assert status == 400
    assert body == {"ok": False, "error": RESERVED_RENAME_ERROR}
    assert source.read_bytes() == b"synthetic-fixture"
    assert not destination.exists()


def test_kr_ordinary_input_rename_stays_allowed(state):
    status, body, source, destination = rename_input(state, "clip.mp4", "my_clip.mp4", payload=b"clip-bytes")
    assert status == 200 and body["ok"] is True
    assert not source.exists()
    assert destination.read_bytes() == b"clip-bytes"


def test_kr_output_may_still_use_staged_prefix(state):
    status, body, source, destination = rename_input(state, "clip.png", "castle_x.png", view_type="output")
    assert status == 200 and body["ok"] is True
    assert not source.exists()
    assert destination.read_bytes() == b"synthetic-fixture"


def test_kr_staged_input_may_be_renamed_to_an_ordinary_name(state):
    old = "castle_colab_" + "ab" * 16 + ".png"
    status, body, source, destination = rename_input(state, old, "clip.png")
    assert status == 200 and body["ok"] is True
    assert not source.exists()
    assert destination.name == "clip.png"
    assert destination.read_bytes() == b"synthetic-fixture"
