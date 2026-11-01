"""State machine and polling coordinator for BabyCam."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from pathlib import Path
import secrets
from typing import Any

from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .camera_client import CameraError, CameraSource
from .const import (
    AUDIO_VIEW_URL,
    CONF_ABSENCE_GRACE,
    CONF_CONNECTION_TIMEOUT,
    CONF_CRY_COOLDOWN,
    CONF_CRY_DETECTION,
    CONF_CRY_MODEL,
    CONF_CRY_THRESHOLD,
    CONF_DETECTION_THRESHOLD,
    CONF_MEDIA_PLAYERS,
    CONF_MODEL_DOWNLOAD,
    CONF_MODEL_MANIFEST_URL,
    CONF_NOTIFY_SERVICES,
    CONF_PRESENCE_MODEL,
    CONF_ROI,
    CONF_SNAPSHOT_INTERVAL,
    DEFAULT_ABSENCE_GRACE,
    DEFAULT_CONNECTION_TIMEOUT,
    DEFAULT_CRY_COOLDOWN,
    DEFAULT_CRY_MODEL,
    DEFAULT_CRY_THRESHOLD,
    DEFAULT_DETECTION_THRESHOLD,
    DEFAULT_MODEL_DOWNLOAD,
    DEFAULT_MODEL_MANIFEST_URL,
    DEFAULT_PRESENCE_MODEL,
    DEFAULT_ROI,
    DEFAULT_SNAPSHOT_INTERVAL,
    DEFAULT_STREAM_MODE,
    DOMAIN,
    EVENT_BABY_DETECTED,
    EVENT_CONNECTION_LOST,
    EVENT_CRY_DETECTED,
    EVENT_MODELS_UPDATED,
    MODE_HIGH,
    MODE_LOW,
    MODEL_DOWNLOAD_AUTO,
    MODEL_DOWNLOAD_OFF,
    MODEL_DOWNLOAD_ONCE,
    MODEL_UPDATE_CHECK_INTERVAL_HOURS,
    MODELS_SUBDIR,
    MONITOR_AUTO,
    MONITOR_MANUAL,
    MONITOR_OFF,
    OPT_AI_ENABLED,
    OPT_NOTIFICATIONS_ENABLED,
    OPT_STREAM_MODE,
    SIGNAL_UPDATE,
)
from .cry_detector import CryClassifier, CryMonitor
from .detector import PresenceDetector, PresenceResult, parse_roi
from .model_downloader import DownloadResult, ModelDownloader, ModelDownloadError

_LOGGER = logging.getLogger(__name__)

CRY_HOLD = timedelta(seconds=15)


@dataclass
class BabyCamData:
    """Snapshot of the integration state exposed to entities and the panel."""

    baby_present: bool = False
    presence_confidence: float = 0.0
    monitoring: str = MONITOR_OFF
    stream_mode: str = DEFAULT_STREAM_MODE
    crying: bool = False
    cry_score: float = 0.0
    connection_lost: bool = False
    camera_online: bool = False
    ai_enabled: bool = True
    cry_detection_enabled: bool = True
    notifications_enabled: bool = True
    last_snapshot: datetime | None = None
    last_detection: datetime | None = None
    last_cry: datetime | None = None
    last_error: str | None = None
    presence_model_ready: bool = False
    cry_model_ready: bool = False
    detections: list[dict[str, Any]] = field(default_factory=list)
    model_download_mode: str = DEFAULT_MODEL_DOWNLOAD
    models_version: str | None = None
    models_available_version: str | None = None
    models_downloading: bool = False
    models_last_check: datetime | None = None
    models_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "baby_present": self.baby_present,
            "presence_confidence": round(self.presence_confidence, 3),
            "monitoring": self.monitoring,
            "stream_mode": self.stream_mode,
            "crying": self.crying,
            "cry_score": round(self.cry_score, 3),
            "connection_lost": self.connection_lost,
            "camera_online": self.camera_online,
            "ai_enabled": self.ai_enabled,
            "cry_detection_enabled": self.cry_detection_enabled,
            "notifications_enabled": self.notifications_enabled,
            "last_snapshot": self.last_snapshot.isoformat() if self.last_snapshot else None,
            "last_detection": self.last_detection.isoformat() if self.last_detection else None,
            "last_cry": self.last_cry.isoformat() if self.last_cry else None,
            "last_error": self.last_error,
            "presence_model_ready": self.presence_model_ready,
            "cry_model_ready": self.cry_model_ready,
            "detections": self.detections,
            "model_download_mode": self.model_download_mode,
            "models_version": self.models_version,
            "models_available_version": self.models_available_version,
            "models_downloading": self.models_downloading,
            "models_last_check": self.models_last_check.isoformat() if self.models_last_check else None,
            "models_error": self.models_error,
        }


class BabyCamCoordinator(DataUpdateCoordinator[BabyCamData]):
    """Polls snapshots, runs the detectors and drives monitoring."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, source: CameraSource) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(seconds=DEFAULT_SNAPSHOT_INTERVAL),
            config_entry=entry,
        )
        self.entry = entry
        self.source = source
        self.state = BabyCamData()
        self.last_image: bytes | None = None
        self.audio_token = secrets.token_hex(32)
        self._lock = asyncio.Lock()
        self._misses = 0
        self._last_ok: datetime | None = None
        self._last_cry_notify: datetime | None = None
        self._cry_hold_handle: asyncio.TimerHandle | None = None
        self._presence: PresenceDetector | None = None
        self._cry_monitor: CryMonitor | None = None
        self._cry_classifier: CryClassifier | None = None
        self._downloader: ModelDownloader | None = None
        self._download_lock = asyncio.Lock()
        self._unsub_model_check: Callable[[], None] | None = None
        self._unsub_model_initial: Callable[[], None] | None = None
        self._model_download_config: tuple[str, str] | None = None
        self.apply_options()

    # ------------------------------------------------------------------ options
    @property
    def options(self) -> dict[str, Any]:
        return {**self.entry.data, **self.entry.options}

    def opt(self, key: str, default: Any) -> Any:
        return self.options.get(key, default)

    @property
    def models_dir(self) -> Path:
        return Path(self.hass.config.path(MODELS_SUBDIR))

    def _model_path(self, key: str, default: str) -> Path:
        value = Path(self.opt(key, default))
        return value if value.is_absolute() else self.models_dir / value

    @callback
    def apply_options(self) -> None:
        """Re-read entry options (called on options update, no reload needed)."""
        interval = int(self.opt(CONF_SNAPSHOT_INTERVAL, DEFAULT_SNAPSHOT_INTERVAL))
        self.update_interval = timedelta(seconds=interval)
        self.state.stream_mode = self.opt(OPT_STREAM_MODE, DEFAULT_STREAM_MODE)
        self.state.ai_enabled = bool(self.opt(OPT_AI_ENABLED, True))
        self.state.notifications_enabled = bool(self.opt(OPT_NOTIFICATIONS_ENABLED, True))
        cry_enabled = bool(self.opt(CONF_CRY_DETECTION, True))
        cry_changed = cry_enabled != self.state.cry_detection_enabled
        self.state.cry_detection_enabled = cry_enabled
        try:
            self._roi = parse_roi(self.opt(CONF_ROI, DEFAULT_ROI))
        except ValueError as err:
            _LOGGER.warning("Invalid ROI, using the full frame: %s", err)
            self._roi = parse_roi(DEFAULT_ROI)

        presence_path = self._model_path(CONF_PRESENCE_MODEL, DEFAULT_PRESENCE_MODEL)
        threshold = float(self.opt(CONF_DETECTION_THRESHOLD, DEFAULT_DETECTION_THRESHOLD))
        if self._presence is None or self._presence.model_path != presence_path:
            self._presence = PresenceDetector(presence_path, threshold)
        else:
            self._presence.threshold = threshold
        self.state.presence_model_ready = presence_path.is_file()

        cry_path = self._model_path(CONF_CRY_MODEL, DEFAULT_CRY_MODEL)
        if self._cry_classifier is None or self._cry_classifier.model_path != cry_path:
            self._cry_classifier = CryClassifier(cry_path)
            self._cry_monitor = None
        self.state.cry_model_ready = cry_path.is_file()

        if cry_changed and self.state.monitoring != MONITOR_OFF:
            if cry_enabled:
                self.hass.async_create_task(self._start_cry_monitor())
            else:
                self._set_crying(False)
                self.hass.async_create_task(self._stop_cry_monitor())

        self._apply_model_download_options()
        self._notify()

    # ----------------------------------------------------------- model download
    @callback
    def _apply_model_download_options(self) -> None:
        mode = self.opt(CONF_MODEL_DOWNLOAD, DEFAULT_MODEL_DOWNLOAD)
        url = self.opt(CONF_MODEL_MANIFEST_URL, DEFAULT_MODEL_MANIFEST_URL)
        self.state.model_download_mode = mode
        if self._downloader is None or self._downloader.manifest_url != url:
            self._downloader = ModelDownloader(self.hass, self.models_dir, url)
            self.hass.async_create_task(self._async_load_installed_version())

        # Unrelated option changes (e.g. stream mode) must not re-trigger a check.
        if self._model_download_config == (mode, url):
            return
        self._model_download_config = (mode, url)

        self._cancel_model_schedule()
        if mode == MODEL_DOWNLOAD_OFF:
            return
        # Once: the scheduled check bails out if both models are already present.
        self._unsub_model_initial = async_call_later(self.hass, 5, self._scheduled_model_check)
        if mode == MODEL_DOWNLOAD_AUTO:
            self._unsub_model_check = async_track_time_interval(
                self.hass, self._scheduled_model_check, timedelta(hours=MODEL_UPDATE_CHECK_INTERVAL_HOURS)
            )

    async def _async_load_installed_version(self) -> None:
        assert self._downloader is not None
        self.state.models_version = await self.hass.async_add_executor_job(self._downloader.installed_version)
        self._notify()

    @callback
    def _cancel_model_schedule(self) -> None:
        for attr in ("_unsub_model_check", "_unsub_model_initial"):
            unsub = getattr(self, attr)
            if unsub is not None:
                unsub()
                setattr(self, attr, None)

    async def _scheduled_model_check(self, _now: datetime | None = None) -> None:
        self._unsub_model_initial = None
        mode = self.state.model_download_mode
        if mode == MODEL_DOWNLOAD_OFF:
            return
        if mode == MODEL_DOWNLOAD_ONCE and self.state.presence_model_ready and self.state.cry_model_ready:
            return
        with contextlib.suppress(ModelDownloadError):  # already recorded in state.models_error
            await self.async_download_models()

    async def async_check_model_update(self) -> bool:
        """Fetch the manifest and report whether an update is available (no download)."""
        assert self._downloader is not None
        try:
            manifest, needs_update = await self._downloader.async_check_update()
        except ModelDownloadError as err:
            self.state.models_error = str(err)
            self._notify()
            raise
        self.state.models_available_version = manifest.version
        self.state.models_last_check = dt_util.utcnow()
        self.state.models_error = None
        self._notify()
        return needs_update

    async def async_download_models(self, force: bool = False) -> DownloadResult | None:
        """Download missing/outdated models (used by the service, button, panel and scheduler)."""
        assert self._downloader is not None
        if self._download_lock.locked():
            return None
        async with self._download_lock:
            self.state.models_downloading = True
            self.state.models_error = None
            self._notify()
            try:
                result = await self._downloader.async_update(force=force)
            except ModelDownloadError as err:
                _LOGGER.warning("Model download failed: %s", err)
                self.state.models_error = str(err)
                raise
            finally:
                self.state.models_downloading = False
                self.state.models_last_check = dt_util.utcnow()
                self._notify()

        self.state.models_version = result.version
        self.state.models_available_version = result.version
        if result.updated:
            _LOGGER.info("Models updated to %s: %s", result.version, ", ".join(result.updated))
            self.hass.bus.async_fire(
                EVENT_MODELS_UPDATED,
                {"entry_id": self.entry.entry_id, "version": result.version, "updated": result.updated},
            )
            await self._reload_models(result)
        self._notify()
        return result

    async def _reload_models(self, result: DownloadResult) -> None:
        """Point the detectors at the downloaded files and drop cached sessions."""
        presence_file = result.files.get("presence")
        cry_file = result.files.get("cry")
        if presence_file and self._presence is not None:
            self._presence = PresenceDetector(self.models_dir / presence_file, self._presence.threshold)
            self.state.presence_model_ready = self._presence.model_path.is_file()
        if cry_file and self._cry_classifier is not None:
            self._cry_classifier = CryClassifier(self.models_dir / cry_file)
            self.state.cry_model_ready = self._cry_classifier.model_path.is_file()
            await self._stop_cry_monitor()
            self._cry_monitor = None
            if self.state.monitoring != MONITOR_OFF:
                await self._start_cry_monitor()
        # Keep the configured file names in sync so a restart uses the same files.
        new_opts = {**self.entry.options}
        if presence_file:
            new_opts[CONF_PRESENCE_MODEL] = presence_file
        if cry_file:
            new_opts[CONF_CRY_MODEL] = cry_file
        if new_opts != self.entry.options:
            self.hass.config_entries.async_update_entry(self.entry, options=new_opts)

    # ------------------------------------------------------------------ helpers
    @property
    def audio_url(self) -> str:
        return f"{AUDIO_VIEW_URL.format(entry_id=self.entry.entry_id)}?token={self.audio_token}"

    @property
    def roi(self) -> tuple[float, float, float, float]:
        return self._roi

    @callback
    def _notify(self) -> None:
        self.data = self.state
        self.async_update_listeners()
        async_dispatcher_send(self.hass, SIGNAL_UPDATE.format(self.entry.entry_id))

    async def async_stream_source(self, mode: str | None = None) -> str | None:
        mode = mode or self.state.stream_mode
        if mode not in (MODE_LOW, MODE_HIGH):
            mode = MODE_LOW
        return await self.source.async_get_stream_source(mode)

    async def async_shutdown(self) -> None:
        self._cancel_model_schedule()
        self._set_crying(False)
        await self._stop_cry_monitor()
        await super().async_shutdown()

    # ------------------------------------------------------------------ polling
    async def _async_update_data(self) -> BabyCamData:
        async with self._lock:
            await self._poll()
        return self.state

    async def async_check_now(self) -> None:
        """Force an immediate snapshot + detection."""
        await self.async_refresh()

    async def _poll(self) -> None:
        now = dt_util.utcnow()
        try:
            image = await self.source.async_get_snapshot()
        except CameraError as err:
            self.state.last_error = str(err)
            self.state.camera_online = False
            _LOGGER.debug("Snapshot failed: %s", err)
            await self._check_connection_lost(now)
            self._notify()
            return

        self.last_image = image
        self._last_ok = now
        self.state.last_snapshot = now
        self.state.camera_online = True
        self.state.last_error = None
        if self.state.connection_lost:
            self.state.connection_lost = False
            _LOGGER.info("Camera connection restored")

        if self.state.ai_enabled and self._presence is not None:
            await self._run_presence(image, now)
        self._notify()

    async def _run_presence(self, image: bytes, now: datetime) -> None:
        assert self._presence is not None
        if not self._presence.model_path.is_file():
            self.state.presence_model_ready = False
            self.state.last_error = f"Presence model missing: {self._presence.model_path}"
            return
        self.state.presence_model_ready = True
        try:
            result: PresenceResult = await self.hass.async_add_executor_job(
                self._presence.detect, image, self._roi
            )
        except Exception as err:  # noqa: BLE001
            self.state.last_error = f"Presence detection failed: {err}"
            _LOGGER.warning("Presence detection failed: %s", err)
            return

        self.state.presence_confidence = result.confidence
        self.state.detections = [
            {"class_id": d.class_id, "confidence": round(d.confidence, 3), "box": [d.x1, d.y1, d.x2, d.y2]}
            for d in result.detections
        ]
        grace = int(self.opt(CONF_ABSENCE_GRACE, DEFAULT_ABSENCE_GRACE))

        if result.present:
            self._misses = 0
            self.state.last_detection = now
            if not self.state.baby_present:
                self.state.baby_present = True
                self.hass.bus.async_fire(
                    EVENT_BABY_DETECTED, {"entry_id": self.entry.entry_id, "confidence": result.confidence}
                )
            if self.state.monitoring == MONITOR_OFF:
                await self.async_start_monitoring(MONITOR_AUTO)
        else:
            self._misses += 1
            if self.state.baby_present and self._misses >= grace:
                self.state.baby_present = False
                if self.state.monitoring == MONITOR_AUTO:
                    await self.async_stop_monitoring()

    async def _check_connection_lost(self, now: datetime) -> None:
        if self.state.monitoring == MONITOR_OFF or self.state.connection_lost:
            return
        timeout = timedelta(seconds=int(self.opt(CONF_CONNECTION_TIMEOUT, DEFAULT_CONNECTION_TIMEOUT)))
        reference = self._last_ok or self.state.last_snapshot
        if reference is None or now - reference >= timeout:
            self.state.connection_lost = True
            _LOGGER.warning("Camera connection lost while monitoring")
            self.hass.bus.async_fire(EVENT_CONNECTION_LOST, {"entry_id": self.entry.entry_id})
            await self._send_notification(
                "BabyCam: camera connection lost",
                f"No snapshot from {self.source.name} for {int(timeout.total_seconds())} s while monitoring.",
            )

    # --------------------------------------------------------------- monitoring
    async def async_start_monitoring(self, mode: str = MONITOR_MANUAL) -> None:
        if self.state.monitoring != MONITOR_OFF and not (
            self.state.monitoring == MONITOR_AUTO and mode == MONITOR_MANUAL
        ):
            return
        _LOGGER.info("Starting monitoring (%s)", mode)
        self.state.monitoring = mode
        self.state.connection_lost = False
        self._last_ok = self._last_ok or dt_util.utcnow()
        await self._start_cry_monitor()
        await self._play_audio_on_players()
        self._notify()

    async def async_stop_monitoring(self) -> None:
        if self.state.monitoring == MONITOR_OFF:
            return
        _LOGGER.info("Stopping monitoring")
        self.state.monitoring = MONITOR_OFF
        self.state.connection_lost = False
        self._set_crying(False)
        await self._stop_cry_monitor()
        await self._stop_audio_on_players()
        self._notify()

    @callback
    def async_update_option(self, key: str, value: Any) -> None:
        """Persist a single option; the entry update listener re-applies options."""
        self.hass.config_entries.async_update_entry(
            self.entry, options={**self.entry.options, key: value}
        )

    # ------------------------------------------------------------------- audio
    async def _play_audio_on_players(self) -> None:
        players: list[str] = self.opt(CONF_MEDIA_PLAYERS, [])
        if not players:
            return
        base = self._external_base_url()
        if base is None:
            _LOGGER.warning("Cannot determine Home Assistant URL for media players")
            return
        await self.hass.services.async_call(
            "media_player",
            "play_media",
            {
                "entity_id": players,
                "media_content_id": f"{base}{self.audio_url}",
                "media_content_type": "music",
            },
            blocking=False,
        )

    async def _stop_audio_on_players(self) -> None:
        players: list[str] = self.opt(CONF_MEDIA_PLAYERS, [])
        if players:
            await self.hass.services.async_call(
                "media_player", "media_stop", {"entity_id": players}, blocking=False
            )

    def _external_base_url(self) -> str | None:
        from homeassistant.helpers.network import NoURLAvailableError, get_url  # noqa: PLC0415

        try:
            return get_url(self.hass, prefer_external=False, allow_cloud=False)
        except NoURLAvailableError:
            return None

    # --------------------------------------------------------------------- cry
    async def _start_cry_monitor(self) -> None:
        if not self.state.cry_detection_enabled or self._cry_classifier is None:
            return
        if not self._cry_classifier.model_path.is_file():
            self.state.cry_model_ready = False
            _LOGGER.info("Cry model missing (%s); cry detection disabled", self._cry_classifier.model_path)
            return
        self.state.cry_model_ready = True
        source = await self.async_stream_source(MODE_LOW)
        if source is None:
            _LOGGER.warning("No stream source available for cry detection")
            return
        if self._cry_monitor is None:
            self._cry_monitor = CryMonitor(
                self._cry_classifier, get_ffmpeg_manager(self.hass).binary, self._async_on_cry_score
            )
        await self._cry_monitor.async_start(source)

    async def _stop_cry_monitor(self) -> None:
        if self._cry_monitor is not None:
            await self._cry_monitor.async_stop()

    async def _async_on_cry_score(self, score: float) -> None:
        self.state.cry_score = score
        threshold = float(self.opt(CONF_CRY_THRESHOLD, DEFAULT_CRY_THRESHOLD))
        if score >= threshold:
            now = dt_util.utcnow()
            self.state.last_cry = now
            self._schedule_cry_release()
            if not self.state.crying:
                self._set_crying(True)
                self.hass.bus.async_fire(
                    EVENT_CRY_DETECTED, {"entry_id": self.entry.entry_id, "score": score}
                )
                await self._maybe_notify_cry(now, score)
        self._notify()

    def _schedule_cry_release(self) -> None:
        if self._cry_hold_handle is not None:
            self._cry_hold_handle.cancel()
        self._cry_hold_handle = self.hass.loop.call_later(
            CRY_HOLD.total_seconds(), lambda: self._set_crying(False, notify=True)
        )

    @callback
    def _set_crying(self, value: bool, notify: bool = False) -> None:
        if not value and self._cry_hold_handle is not None:
            self._cry_hold_handle.cancel()
            self._cry_hold_handle = None
        if self.state.crying != value:
            self.state.crying = value
            if notify:
                self._notify()

    async def _maybe_notify_cry(self, now: datetime, score: float) -> None:
        cooldown = timedelta(seconds=int(self.opt(CONF_CRY_COOLDOWN, DEFAULT_CRY_COOLDOWN)))
        if self._last_cry_notify is not None and now - self._last_cry_notify < cooldown:
            return
        self._last_cry_notify = now
        await self._send_notification("BabyCam: crying detected", f"Cry probability {score:.0%}.")

    # ------------------------------------------------------------ notifications
    async def _send_notification(self, title: str, message: str) -> None:
        if not self.state.notifications_enabled:
            return
        services: list[str] = self.opt(CONF_NOTIFY_SERVICES, [])
        for service in services:
            domain, _, name = service.partition(".")
            if domain != "notify" or not name:
                name = service
            if not self.hass.services.has_service("notify", name):
                _LOGGER.warning("Notify service notify.%s does not exist", name)
                continue
            try:
                await self.hass.services.async_call(
                    "notify", name, {"title": title, "message": message}, blocking=False
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Notification via notify.%s failed: %s", name, err)
