"""Self-update for the AppImage via GitHub Releases.

Flow: ask the GitHub API for the latest release, compare its tag with
``__version__``, download the AppImage next to the running one, verify it
against the published SHA256SUMS, and atomically replace the running file.
The running program keeps working (Linux keeps the old inode open); the next
start, or an immediate restart, uses the new version.

Only plain HTTPS GET requests, no account, no token. If the repository is
private or there is no network, every call fails quietly with UpdateError.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import __version__

REPO = "conty1337/Video-to-funscript-Generator"
# VTF_UPDATE_API: nur für Tests (lokaler Server statt GitHub)
API_LATEST = os.environ.get("VTF_UPDATE_API") or f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
ASSET_SUFFIX = "-x86_64.AppImage"
CHECKSUM_ASSET = "SHA256SUMS"
USER_AGENT = f"video-to-funscript-updater/{__version__}"
TIMEOUT = 15


class UpdateError(Exception):
    pass


@dataclass
class Release:
    version: str
    tag: str
    notes: str
    asset_name: str
    asset_url: str
    asset_size: int
    checksum_url: str | None
    page_url: str


def parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.3' / '1.2' / '1.2.3-beta' -> (1, 2, 3); unparsable -> ()."""
    m = re.match(r"^\s*v?(\d+(?:\.\d+)*)", text or "")
    return tuple(int(p) for p in m.group(1).split(".")) if m else ()


def is_newer(candidate: str, current: str = __version__) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    if not a or not b:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def running_appimage() -> Path | None:
    """Path of the AppImage this process was started from, if any."""
    p = os.environ.get("APPIMAGE")
    if not p or not getattr(sys, "frozen", False):
        return None
    path = Path(p)
    return path if path.is_file() else None


def can_self_update() -> bool:
    p = running_appimage()
    return p is not None and os.access(p.parent, os.W_OK)


# CA bundles of common distributions. The AppImage ships Ubuntu's OpenSSL,
# which only looks in /usr/lib/ssl, so on Fedora/Nobara we must point it here.
SYSTEM_CA_FILES = (
    "/etc/pki/tls/certs/ca-bundle.crt",                    # Fedora, Nobara, RHEL
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",   # Fedora (extracted)
    "/etc/ssl/certs/ca-certificates.crt",                  # Debian, Ubuntu, Arch
    "/etc/ssl/ca-bundle.pem",                              # openSUSE
    "/etc/ssl/cert.pem",                                   # Alpine, macOS
)
_ssl_ctx: ssl.SSLContext | None = None


def ssl_context() -> ssl.SSLContext:
    """Verifying TLS context: system CA bundle if found, else certifi, else OpenSSL defaults."""
    global _ssl_ctx
    if _ssl_ctx is None:
        ctx = ssl.create_default_context()
        loaded = False
        for path in ([os.environ["SSL_CERT_FILE"]] if os.environ.get("SSL_CERT_FILE") else []) + list(SYSTEM_CA_FILES):
            if os.path.isfile(path):
                try:
                    ctx.load_verify_locations(cafile=path)
                    loaded = True
                    break
                except (ssl.SSLError, OSError):
                    continue
        if not loaded:
            try:
                import certifi
                ctx.load_verify_locations(cafile=certifi.where())
            except (ImportError, ssl.SSLError, OSError):
                pass
        _ssl_ctx = ctx
    return _ssl_ctx


def _get(url: str, accept: str = "application/octet-stream"):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        ctx = ssl_context() if url.startswith("https:") else None
        return urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx)
    except urllib.error.HTTPError as e:
        raise UpdateError(f"HTTP {e.code}") from e
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError(str(getattr(e, "reason", e))) from e


def parse_release(data: dict) -> Release:
    if data.get("draft") or data.get("prerelease"):
        raise UpdateError("kein stabiles Release")
    tag = str(data.get("tag_name") or "")
    assets = data.get("assets") or []
    app = [a for a in assets if str(a.get("name", "")).endswith(ASSET_SUFFIX)]
    if not app:
        raise UpdateError("Release enthält kein AppImage")
    # prefer the version-less name used for stable download links
    app.sort(key=lambda a: len(a["name"]))
    asset = app[0]
    sums = next((a for a in assets if a.get("name") == CHECKSUM_ASSET), None)
    return Release(
        version=".".join(map(str, parse_version(tag))) or tag,
        tag=tag,
        notes=str(data.get("body") or "").strip(),
        asset_name=str(asset["name"]),
        asset_url=str(asset["browser_download_url"]),
        asset_size=int(asset.get("size") or 0),
        checksum_url=str(sums["browser_download_url"]) if sums else None,
        page_url=str(data.get("html_url") or RELEASES_PAGE),
    )


def fetch_latest() -> Release:
    with _get(API_LATEST, "application/vnd.github+json") as r:
        try:
            data = json.loads(r.read().decode("utf-8"))
        except ValueError as e:
            raise UpdateError("ungültige Antwort von GitHub") from e
    return parse_release(data)


def check_for_update() -> Release | None:
    """The latest release if it is newer than this version, else None."""
    rel = fetch_latest()
    return rel if is_newer(rel.version) else None


def expected_sha256(release: Release) -> str:
    if not release.checksum_url:
        raise UpdateError("Release enthält keine Prüfsummen (SHA256SUMS)")
    with _get(release.checksum_url) as r:
        text = r.read(64 * 1024).decode("utf-8", "replace")
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*").endswith(release.asset_name):
            if re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
                return parts[0].lower()
    raise UpdateError(f"keine Prüfsumme für {release.asset_name}")


def download_and_install(release: Release, target: Path | None = None,
                         progress: Callable[[float], None] | None = None,
                         cancel: Callable[[], bool] | None = None) -> Path:
    """Download, verify and atomically replace ``target`` (default: the running AppImage)."""
    target = target or running_appimage()
    if target is None:
        raise UpdateError("läuft nicht als AppImage")
    if not os.access(target.parent, os.W_OK):
        raise UpdateError(f"keine Schreibrechte in {target.parent}")
    want = expected_sha256(release)
    tmp = target.with_name(f".{target.name}.update")
    h = hashlib.sha256()
    done = 0
    try:
        with _get(release.asset_url) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or release.asset_size or 0)
            while True:
                if cancel and cancel():
                    raise UpdateError("abgebrochen")
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress and total:
                    progress(min(1.0, done / total))
            f.flush()
            os.fsync(f.fileno())
        if release.asset_size and done != release.asset_size:
            raise UpdateError("Download unvollständig")
        if h.hexdigest() != want:
            raise UpdateError("Prüfsumme stimmt nicht, Update verworfen")
        os.chmod(tmp, 0o755)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target


def restart(target: Path, args: list[str] | None = None) -> None:
    """Replace this process with the (updated) AppImage."""
    env = dict(os.environ)
    # tell the new PyInstaller bootloader it is a fresh program, not our child
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    for key in ("APPDIR", "APPIMAGE", "ARGV0", "OWD"):
        env.pop(key, None)
    if "LD_LIBRARY_PATH_ORIG" in env:
        env["LD_LIBRARY_PATH"] = env.pop("LD_LIBRARY_PATH_ORIG")
    else:
        env.pop("LD_LIBRARY_PATH", None)
    os.execve(str(target), [str(target), *(args or [])], env)
