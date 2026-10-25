"""Se etter og hent nye versjoner fra GitHub.

Sjekken bruker GitHubs offentlige API (ingen innlogging, ingen data om brukeren
sendes). Selve installasjonen gjøres av install.sh fra den nedlastede versjonen,
startet med pkexec slik at brukeren godkjenner med passord.
"""
import json
import os
import re
import tarfile
import tempfile
import time
import urllib.request

from . import __version__

REPO = "Vindihaaret/vifte"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
TARBALL = f"https://github.com/{REPO}/archive/refs/tags/{{tag}}.tar.gz"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
CHECK_INTERVAL = 30 * 24 * 3600     # automatisk sjekk: én gang i måneden
INSTALL_DIR = "/opt/vifte"


def parse_version(v):
    """«v1.2.3» / «1.2» -> (1, 2, 3). Ugyldig -> None."""
    m = re.fullmatch(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", (v or "").strip())
    if not m:
        return None
    return tuple(int(x or 0) for x in m.groups())


def is_newer(tag, current=__version__):
    new, cur = parse_version(tag), parse_version(current)
    return bool(new and cur and new > cur)


def is_installed():
    """Oppdatering fra appen gjelder bare en installert Vifte (ikke en git-mappe)."""
    pkg = os.path.dirname(os.path.realpath(__file__))
    return pkg.startswith(INSTALL_DIR + os.sep)


def due(last_check, now=None, enabled=True):
    if not enabled:
        return False
    now = time.time() if now is None else now
    return not last_check or now - float(last_check) >= CHECK_INTERVAL


def fetch_latest(timeout=10):
    """Returnerer {"tag", "version", "name", "notes", "url"} for siste utgivelse."""
    req = urllib.request.Request(API_LATEST, headers={
        "Accept": "application/vnd.github+json", "User-Agent": f"vifte/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    return release_info(data)


def release_info(data):
    tag = data.get("tag_name") or ""
    if not parse_version(tag) or data.get("draft") or data.get("prerelease"):
        raise ValueError(f"unexpected release: {tag!r}")
    return {"tag": tag, "version": ".".join(map(str, parse_version(tag))),
            "name": data.get("name") or tag, "notes": (data.get("body") or "").strip(),
            "url": data.get("html_url") or RELEASES_PAGE}


def download(tag, dest_dir=None, timeout=60):
    """Laster ned og pakker ut kildekoden for en utgivelse. Returnerer mappen med install.sh."""
    if not parse_version(tag):
        raise ValueError(f"invalid tag: {tag!r}")
    base = tempfile.mkdtemp(prefix="vifte-update-", dir=dest_dir)
    archive = os.path.join(base, "source.tar.gz")
    req = urllib.request.Request(TARBALL.format(tag=tag), headers={"User-Agent": f"vifte/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(archive, "wb") as f:
        while chunk := resp.read(65536):
            f.write(chunk)
    return extract(archive, os.path.join(base, "src"), tag)


def extract(archive, target, tag):
    """Trygg utpakking (ingen stier utenfor målmappen, ingen lenker/enheter), og kontroll
    av at innholdet faktisk er Vifte i riktig versjon."""
    os.makedirs(target, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        for m in members:
            path = os.path.realpath(os.path.join(target, m.name))
            if not path.startswith(os.path.realpath(target) + os.sep):
                raise ValueError(f"unsafe path in archive: {m.name}")
            if not (m.isfile() or m.isdir()):
                raise ValueError(f"unexpected entry in archive: {m.name}")
        try:
            tar.extractall(target, members=members, filter="data")
        except TypeError:  # Python < 3.12 uten filter-støtte; stiene er kontrollert over
            tar.extractall(target, members=members)
    roots = [d for d in os.listdir(target) if os.path.isdir(os.path.join(target, d))]
    if len(roots) != 1:
        raise ValueError("unexpected archive layout")
    src = os.path.join(target, roots[0])
    init = os.path.join(src, "vifte", "__init__.py")
    if not (os.path.isfile(os.path.join(src, "install.sh")) and os.path.isfile(init)):
        raise ValueError("the download does not look like Vifte")
    with open(init, encoding="utf-8") as f:
        m = re.search(r'__version__\s*=\s*"([^"]+)"', f.read())
    if not m or parse_version(m.group(1)) != parse_version(tag):
        raise ValueError(f"version mismatch in download ({m.group(1) if m else '?'} vs {tag})")
    return src
