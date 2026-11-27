"""Discover and stage verified portable Windows updates from GitHub Releases."""

import hashlib
import json
import re
import shutil
import ssl
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4
from zipfile import ZipFile

ROOT = "https://github.com/muratkorayozkan/Pivluma"
LATEST_API = "https://api.github.com/repos/muratkorayozkan/Pivluma/releases/latest"
VERSION = re.compile(r"(?:0|[1-9][0-9]{0,8})(?:\.(?:0|[1-9][0-9]{0,8})){2}")
MAX_DOWNLOAD = 256 * 1024 * 1024
MAX_EXPANDED = 768 * 1024 * 1024


@dataclass(frozen=True)
class Update:
    version: str
    url: str
    size: int
    sha256: str


def latest_update(payload, current_version: str) -> Update | None:
    tag = payload.get("tag_name", "")
    if payload.get("draft") is not False or payload.get("prerelease") is not False:
        return None
    if not isinstance(tag, str) or not tag.startswith("v") or not VERSION.fullmatch(tag[1:]):
        return None
    version = tag[1:]
    if tuple(map(int, version.split("."))) <= tuple(map(int, current_version.split("."))):
        return None
    filename = f"Pivluma-{version}-Windows.zip"
    expected_url = f"{ROOT}/releases/download/{tag}/{filename}"
    for asset in payload.get("assets", []):
        digest = asset.get("digest")
        size = asset.get("size")
        if (
            asset.get("name") == filename and asset.get("state") == "uploaded"
            and asset.get("browser_download_url") == expected_url
            and type(size) is int and 0 < size <= MAX_DOWNLOAD
            and isinstance(digest, str) and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest)
        ):
            return Update(version, expected_url, size, digest[7:].lower())
    return None


def check_update(current_version: str) -> Update | None:
    request = Request(LATEST_API, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "Pivluma",
    })
    for attempt in range(2):
        try:
            with urlopen(request, timeout=15) as response:
                return latest_update(json.loads(response.read(2 * 1024 * 1024)), current_version)
        except HTTPError as error:
            if error.code == 404:
                return None
            if attempt or error.code not in {502, 503, 504}:
                raise
        except (URLError, TimeoutError) as error:
            if attempt or isinstance(getattr(error, "reason", None), ssl.SSLCertVerificationError):
                raise
        sleep(.5)
    return None


def update_error_message(error: Exception) -> str:
    if isinstance(error, HTTPError):
        if error.code == 429 or (error.code == 403 and (
            error.headers.get("X-RateLimit-Remaining") == "0" or error.headers.get("Retry-After")
        )):
            return "GitHub rate limit reached. Try again later."
        return "GitHub rejected the update request. Try again or use Releases."
    if isinstance(error, ssl.SSLCertVerificationError) or isinstance(
        getattr(error, "reason", None), ssl.SSLCertVerificationError
    ):
        return "GitHub certificate could not be verified. Check Windows certificates and time."
    if isinstance(error, (URLError, TimeoutError)):
        return "Could not reach GitHub. Check your network and try again."
    if isinstance(error, OSError):
        return "Could not write the update package. Check disk space and access."
    return "Update package could not be verified. Try again or use Releases."


def _extract(archive_path: Path, output: Path) -> None:
    with ZipFile(archive_path) as archive:
        entries = archive.infolist()
        if len(entries) > 10000 or sum(item.file_size for item in entries) > MAX_EXPANDED:
            raise ValueError("Update archive exceeds the extraction limit.")
        paths = set()
        for item in entries:
            parts = item.filename.rstrip("/").split("/")
            if (
                parts[0] != "Pivluma" or stat.S_ISLNK(item.external_attr >> 16)
                or any(
                    not part or part in {".", ".."} or part.endswith((".", " "))
                    or PureWindowsPath(part).is_reserved()
                    or any(character in '<>:"\\|?*' or ord(character) < 32 for character in part)
                    for part in parts
                )
            ):
                raise ValueError("Unsafe update archive path.")
            normalized = tuple(part.casefold() for part in parts)
            if normalized in paths:
                raise ValueError("Duplicate update archive path.")
            paths.add(normalized)
            target = output.joinpath(*parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as source, target.open("xb") as destination:
                    shutil.copyfileobj(source, destination)

    if not (output / "Pivluma/Pivluma.exe").is_file() or not (output / "Pivluma/_internal").is_dir():
        raise ValueError("Update archive does not contain a complete Windows application.")


def prepare_update(update: Update, root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".staging-", dir=root) as directory:
        stage = Path(directory)
        archive = stage / "download.zip"
        digest = hashlib.sha256()
        size = 0
        deadline = monotonic() + 600
        request = Request(update.url, headers={"User-Agent": "Pivluma"})
        with urlopen(request, timeout=20) as source, archive.open("wb") as destination:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > update.size or monotonic() > deadline:
                    raise ValueError("Update download exceeded its size or time limit.")
                digest.update(chunk)
                destination.write(chunk)
        if size != update.size or digest.hexdigest() != update.sha256:
            raise ValueError("Update package failed verification.")
        _extract(archive, stage)
        target = root / f"{update.version}-{uuid4().hex}"
        (stage / "Pivluma").rename(target)
    return target / "Pivluma.exe"
