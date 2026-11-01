"""Optional model downloader.

Disabled by default. When the user opts in, BabyCam fetches a small JSON manifest from a
configurable URL (default: the ``models`` release of this GitHub repository), compares the version
with the locally installed one and downloads new/updated ONNX files. Every file is verified against
the SHA-256 in the manifest and written atomically.

Manifest format::

    {
      "version": "2026.11.1",
      "models": {
        "presence": {"file": "yolov8n.onnx", "url": "https://.../yolov8n.onnx", "sha256": "..."},
        "cry":      {"file": "yamnet.onnx",  "url": "https://.../yamnet.onnx",  "sha256": "..."}
      }
    }
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

INSTALLED_FILE = "installed.json"
MANIFEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=900, sock_read=60)
CHUNK = 1 << 16
MODEL_KEYS = ("presence", "cry")


class ModelDownloadError(Exception):
    """Raised when the manifest or a model cannot be fetched/verified."""


@dataclass(slots=True)
class ModelInfo:
    key: str
    file: str
    url: str
    sha256: str


@dataclass(slots=True)
class Manifest:
    version: str
    models: dict[str, ModelInfo]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Manifest:
        try:
            models = {
                key: ModelInfo(key, m["file"], m["url"], m["sha256"].lower())
                for key, m in data["models"].items()
                if key in MODEL_KEYS
            }
            return cls(version=str(data["version"]), models=models)
        except (KeyError, TypeError, AttributeError) as err:
            raise ModelDownloadError(f"Malformed model manifest: {err}") from err


@dataclass(slots=True)
class DownloadResult:
    version: str
    updated: list[str]
    files: dict[str, str]  # model key -> file name


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_installed(models_dir: Path) -> dict[str, Any]:
    path = models_dir / INSTALLED_FILE
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _write_installed(models_dir: Path, data: dict[str, Any]) -> None:
    tmp = models_dir / f"{INSTALLED_FILE}.tmp"
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(models_dir / INSTALLED_FILE)


class ModelDownloader:
    """Fetch and verify models described by a manifest."""

    def __init__(self, hass: HomeAssistant, models_dir: Path, manifest_url: str) -> None:
        self._hass = hass
        self._models_dir = models_dir
        self._manifest_url = manifest_url

    @property
    def manifest_url(self) -> str:
        return self._manifest_url

    def installed_version(self) -> str | None:
        return _read_installed(self._models_dir).get("version")

    def installed_files(self) -> dict[str, str]:
        return dict(_read_installed(self._models_dir).get("files", {}))

    async def async_fetch_manifest(self) -> Manifest:
        session = async_get_clientsession(self._hass)
        try:
            async with session.get(self._manifest_url, timeout=MANIFEST_TIMEOUT) as resp:
                if resp.status != 200:
                    raise ModelDownloadError(f"Manifest HTTP {resp.status} from {self._manifest_url}")
                data = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise ModelDownloadError(f"Manifest request failed: {err}") from err
        except TimeoutError as err:
            raise ModelDownloadError("Manifest request timed out") from err
        except json.JSONDecodeError as err:
            raise ModelDownloadError("Manifest is not valid JSON") from err
        return Manifest.from_dict(data)

    async def async_check_update(self) -> tuple[Manifest, bool]:
        """Return the remote manifest and whether anything would change."""
        manifest = await self.async_fetch_manifest()
        return manifest, await self._hass.async_add_executor_job(self._needs_update, manifest)

    def _needs_update(self, manifest: Manifest) -> bool:
        installed = _read_installed(self._models_dir)
        if installed.get("version") != manifest.version:
            return True
        return any(not (self._models_dir / info.file).is_file() for info in manifest.models.values())

    async def async_update(self, force: bool = False) -> DownloadResult:
        """Download whatever is missing or outdated. Returns the list of updated model keys."""
        manifest = await self.async_fetch_manifest()
        installed = await self._hass.async_add_executor_job(_read_installed, self._models_dir)
        same_version = installed.get("version") == manifest.version and not force
        updated: list[str] = []

        for info in manifest.models.values():
            target = self._models_dir / info.file
            if same_version and await self._hass.async_add_executor_job(self._file_ok, target, info.sha256):
                continue
            await self._download(info, target)
            updated.append(info.key)

        await self._hass.async_add_executor_job(
            _write_installed,
            self._models_dir,
            {
                "version": manifest.version,
                "source": self._manifest_url,
                "files": {k: m.file for k, m in manifest.models.items()},
                "sha256": {k: m.sha256 for k, m in manifest.models.items()},
            },
        )
        return DownloadResult(manifest.version, updated, {k: m.file for k, m in manifest.models.items()})

    @staticmethod
    def _file_ok(path: Path, sha256: str) -> bool:
        return path.is_file() and _sha256(path) == sha256

    async def _download(self, info: ModelInfo, target: Path) -> None:
        _LOGGER.info("Downloading %s model from %s", info.key, info.url)
        tmp = target.with_suffix(target.suffix + ".part")
        session = async_get_clientsession(self._hass)
        digest = hashlib.sha256()
        try:
            async with session.get(info.url, timeout=DOWNLOAD_TIMEOUT) as resp:
                if resp.status != 200:
                    raise ModelDownloadError(f"{info.key}: HTTP {resp.status} from {info.url}")
                with tmp.open("wb") as fh:
                    async for chunk in resp.content.iter_chunked(CHUNK):
                        fh.write(chunk)
                        digest.update(chunk)
        except aiohttp.ClientError as err:
            tmp.unlink(missing_ok=True)
            raise ModelDownloadError(f"{info.key}: download failed: {err}") from err
        except TimeoutError as err:
            tmp.unlink(missing_ok=True)
            raise ModelDownloadError(f"{info.key}: download timed out") from err
        except OSError as err:
            tmp.unlink(missing_ok=True)
            raise ModelDownloadError(f"{info.key}: cannot write {tmp}: {err}") from err

        if digest.hexdigest() != info.sha256:
            tmp.unlink(missing_ok=True)
            raise ModelDownloadError(f"{info.key}: checksum mismatch, file discarded")
        tmp.replace(target)
        _LOGGER.info("Installed %s model -> %s", info.key, target)
