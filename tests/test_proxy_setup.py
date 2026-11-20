"""Native proxy prerequisites are provisioned before running the required suites."""

from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
import sys
import zipfile
from typing import TYPE_CHECKING

import pytest

from scripts import setup_proxy
from scripts.verify import GATES

if TYPE_CHECKING:
    from pathlib import Path


def test_proxy_setup_is_a_manifest_prerequisite() -> None:
    gate = next(gate for gate in GATES if gate.name == "native-proxy")
    assert gate.tier == "setup"
    assert gate.command[-1] == "scripts/setup_proxy.py"


def test_proxy_setup_finds_managed_windows_tools_without_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "nginx"
    directory.mkdir()
    (directory / "nginx.exe").write_bytes(b"fixture")
    openssl = tmp_path / "Program Files" / "Git" / "usr" / "bin" / "openssl.exe"
    openssl.parent.mkdir(parents=True)
    openssl.write_bytes(b"fixture")

    def missing(_name: str) -> None:
        return None

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(setup_proxy, "WINDOWS_DIRECTORY", directory)
    monkeypatch.setattr(shutil, "which", missing)
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    monkeypatch.delenv("PYSX_NGINX", raising=False)
    monkeypatch.delenv("PYSX_OPENSSL", raising=False)
    assert setup_proxy.nginx_binary() == str(directory / "nginx.exe")
    assert setup_proxy.openssl_binary() == str(openssl)


@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
def test_proxy_setup_provisions_missing_nginx_in_ci(
    monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    calls: list[list[str]] = []
    installed = False

    def nginx() -> str | None:
        return "/tools/nginx" if installed else None

    def install_windows() -> None:
        nonlocal installed
        installed = True
        calls.append(["windows-archive"])

    def run(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal installed
        calls.append(arguments)

        if "install" in arguments:
            installed = True

        return subprocess.CompletedProcess(arguments, 0, stdout="version", stderr="")

    def which(_name: str) -> str:
        return "/usr/bin/sudo"

    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(setup_proxy, "nginx_binary", nginx)
    monkeypatch.setattr(setup_proxy, "openssl_binary", lambda: "/tools/openssl")
    monkeypatch.setattr(setup_proxy, "install_windows_nginx", install_windows)
    monkeypatch.setattr(shutil, "which", which)
    monkeypatch.setattr(subprocess, "run", run)
    setup_proxy.main()
    assert calls[-2:] == [["/tools/nginx", "-v"], ["/tools/openssl", "version"]]

    if platform == "darwin":
        assert calls[0] == ["brew", "install", "nginx"]
    elif platform == "linux":
        assert calls[0] == ["sudo", "apt-get", "update"]
        assert calls[1] == ["sudo", "apt-get", "install", "-y", "--no-install-recommends", "nginx"]
    else:
        assert calls[0] == ["windows-archive"]


@pytest.mark.parametrize("valid", [False, True])
def test_proxy_setup_checks_windows_archive_before_extracting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, valid: bool
) -> None:
    directory = tmp_path / "nginx" / setup_proxy.WINDOWS_VERSION
    directory.parent.mkdir()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr(f"nginx-{setup_proxy.WINDOWS_VERSION}/nginx.exe", b"fixture-executable")
    data = archive.getvalue()
    (directory.parent / f"nginx-{setup_proxy.WINDOWS_VERSION}.zip").write_bytes(data)
    monkeypatch.setattr(setup_proxy, "WINDOWS_DIRECTORY", directory)
    monkeypatch.setattr(
        setup_proxy, "WINDOWS_SHA256", hashlib.sha256(data).hexdigest() if valid else "invalid"
    )

    if valid:
        setup_proxy.install_windows_nginx()
        assert (directory / "nginx.exe").read_bytes() == b"fixture-executable"
    else:
        with pytest.raises(RuntimeError, match="SHA-256"):
            setup_proxy.install_windows_nginx()
        assert not directory.exists()
