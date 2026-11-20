"""Provision and qualify native reverse-proxy test prerequisites in CI."""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WINDOWS_VERSION = "1.30.7"
WINDOWS_SHA256 = "204bb1685b777d7124e9afba2a1dc51edb0eccdfb366262186eb55803a382e02"
WINDOWS_DIRECTORY = ROOT / ".vscode-test" / "nginx" / WINDOWS_VERSION


def nginx_binary() -> str | None:
    supplied = os.environ.get("PYSX_NGINX") or shutil.which("nginx")

    if supplied:
        return supplied
    managed = WINDOWS_DIRECTORY / "nginx.exe"

    return str(managed) if sys.platform == "win32" and managed.is_file() else None


def openssl_binary() -> str | None:
    supplied = os.environ.get("PYSX_OPENSSL") or shutil.which("openssl")

    if supplied:
        return supplied

    if sys.platform == "win32":
        for variable in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
            base = os.environ.get(variable)

            if not base:
                continue

            for relative in ("Git/usr/bin/openssl.exe", "OpenSSL-Win64/bin/openssl.exe"):
                candidate = Path(base) / relative

                if candidate.is_file():
                    return str(candidate)

    return None


def install_windows_nginx() -> None:
    archive = WINDOWS_DIRECTORY.parent / f"nginx-{WINDOWS_VERSION}.zip"

    if archive.is_file():
        data = archive.read_bytes()
    else:
        with urllib.request.urlopen(
            f"https://nginx.org/download/nginx-{WINDOWS_VERSION}.zip", timeout=60
        ) as response:
            data = response.read()

    if hashlib.sha256(data).hexdigest() != WINDOWS_SHA256:
        raise RuntimeError("Windows nginx archive failed SHA-256 verification")
    WINDOWS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(data)
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        executable = package.read(f"nginx-{WINDOWS_VERSION}/nginx.exe")
    (WINDOWS_DIRECTORY / "nginx.exe").write_bytes(executable)


def main() -> None:
    missing = [
        name
        for name, binary in (("nginx", nginx_binary()), ("openssl", openssl_binary()))
        if binary is None
    ]

    if missing:
        if os.environ.get("CI", "").lower() != "true":
            raise RuntimeError(f"Install reverse-proxy prerequisites: {', '.join(missing)}")

        if sys.platform == "win32":
            if "nginx" in missing:
                install_windows_nginx()
        elif sys.platform == "darwin":
            packages = ["openssl@3" if name == "openssl" else name for name in missing]
            subprocess.run(
                ["brew", "install", *packages],
                check=True,
                env={**os.environ, "HOMEBREW_NO_AUTO_UPDATE": "1"},
            )
        elif sys.platform.startswith("linux"):
            sudo = ["sudo"] if shutil.which("sudo") else []
            subprocess.run([*sudo, "apt-get", "update"], check=True)
            subprocess.run(
                [*sudo, "apt-get", "install", "-y", "--no-install-recommends", *missing], check=True
            )

    for name, binary, flag in (
        ("nginx", nginx_binary(), "-v"),
        ("openssl", openssl_binary(), "version"),
    ):
        if binary is None:
            raise RuntimeError(f"{name} is required for reverse-proxy qualification")
        result = subprocess.run(
            [binary, flag], capture_output=True, text=True, check=True, timeout=30
        )
        sys.stdout.write((result.stdout + result.stderr).strip() + "\n")


if __name__ == "__main__":
    main()
