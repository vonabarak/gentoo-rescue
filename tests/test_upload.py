"""Exercise preparation and publishing without a live server."""

import hashlib
import shlex
import subprocess
from contextlib import nullcontext
from io import BytesIO
from unittest.mock import Mock

import pytest

import upload


def test_prepare_pins_image_and_hashes_staged_bytes(tmp_path, monkeypatch):
    source = b"raw disk"
    client, node, disk, placement = object(), object(), object(), object()
    lookup = Mock(side_effect=[(node, disk, placement), AssertionError("latest moved")])
    open_image = Mock(return_value=nullcontext((tmp_path / "corvus.raw", BytesIO(source))))
    export = Mock()
    monkeypatch.setattr(upload, "connect", lambda: nullcontext(client))
    monkeypatch.setattr(upload, "resolve_image", lookup)
    monkeypatch.setattr(upload, "open_raw_image", open_image)
    monkeypatch.setattr(upload, "download_payloads", export)

    path = upload.prepare(tmp_path)

    lookup.assert_called_once_with(client)
    open_image.assert_called_once_with(node, disk, placement)
    export.assert_called_once_with(client, tmp_path, node, disk, progress=upload.report)
    assert path == tmp_path / "corvus.raw"
    assert (tmp_path / upload.RAW_MANIFEST).read_text() == (
        f"{hashlib.sha256(source).hexdigest()}  {upload.RAW}\n"
    )


@pytest.mark.parametrize("fail", [False, True])
def test_transfer(tmp_path, monkeypatch, capsys, fail):
    path = tmp_path / "corvus.raw"
    process = Mock()
    process.stdout.read1.side_effect = [b"\r 50%\r 100%\r\n", b""] * len(upload.RELEASE)
    process.wait.return_value = int(fail)
    scp = Mock(return_value=nullcontext(process))
    monkeypatch.setattr(upload.subprocess, "Popen", scp)

    if fail:
        with pytest.raises(subprocess.CalledProcessError):
            upload.transfer(tmp_path, path)
        assert scp.call_count == 1
    else:
        upload.transfer(tmp_path, path)
        assert [shlex.split(call.args[0][3])[-1] for call in scp.call_args_list] == [
            f"{upload.HOST}:{upload.PUBLIC}/{name}"
            for name in (upload.RAW, *upload.PAYLOADS, *upload.MANIFESTS)
        ]
    assert "50%\n 100%\n" in capsys.readouterr().out
    assert shlex.split(scp.call_args_list[0].args[0][3])[:2] == ["scp", str(path)]


@pytest.mark.parametrize("status,body", [("200", b"fresh"), ("200", b"stale"), ("404", b"")])
def test_verify_public(tmp_path, monkeypatch, status, body):
    for name in upload.MANIFESTS:
        (tmp_path / name).write_bytes(b"fresh")

    def curl(args, **kwargs):
        return body + status.encode()

    monkeypatch.setattr(upload.subprocess, "check_output", curl)
    if status == "200" and body == b"fresh":
        upload.verify_public(tmp_path)
    else:
        with pytest.raises(ValueError, match="https://"):
            upload.verify_public(tmp_path)



def test_transfer_relays_terminal_meter_to_captured_output(tmp_path, monkeypatch, capsys):
    fake_scp = tmp_path / "scp"
    fake_scp.write_text(
        "#!/bin/sh\n"
        "test -t 1 || exit 1\n"
        "printf '\\r 25%%\\r 100%%\\n'\n"
    )
    fake_scp.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    monkeypatch.setattr(upload, "RELEASE", (upload.RAW,))

    upload.transfer(tmp_path, tmp_path / "corvus.raw")

    assert "25%\n 100%\n" in capsys.readouterr().out



def test_transfer_preserves_native_terminal_output(tmp_path, monkeypatch):
    monkeypatch.setattr(upload.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(upload, "RELEASE", (upload.RAW,))
    run = Mock()
    monkeypatch.setattr(upload.subprocess, "run", run)
    path = tmp_path / "corvus.raw"

    upload.transfer(tmp_path, path)

    run.assert_called_once_with(
        ["scp", str(path), f"{upload.HOST}:{upload.PUBLIC}/{upload.RAW}"],
        check=True,
    )


def test_sets_public_permissions(monkeypatch):
    run = Mock()
    monkeypatch.setattr(upload.subprocess, "run", run)

    upload.set_public_permissions()

    command = run.call_args.args[0]
    assert command[:2] == ["ssh", upload.HOST]
    assert shlex.split(command[2]) == [
        "chmod", "0644", "--", *(f"{upload.PUBLIC}/{name}" for name in upload.RELEASE)
    ]
    assert run.call_args.kwargs == {"check": True}
