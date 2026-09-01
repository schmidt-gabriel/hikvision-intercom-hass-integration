"""Coordinator with layered doorbell detection.

This is the piece that sets the integration apart. Existing integrations bet on
a single event channel and go silent when it fails -- which is exactly what
happens on the DS-KB8113-IME1(B), whose alertStream answers 200 with an empty
body and closes immediately.

Here we use the best available channel and fall back on our own:

    1. httpHosts   -- the device POSTs to an HA webhook (real push)
    2. alertStream -- HTTP multipart stream (broken on this firmware)
    3. callStatus  -- light polling, ~1s (always works)

A watchdog guards the push channel: if it stops delivering and callStatus
diverges from our internal state, polling takes over automatically. That is
what prevents the silent failure mode.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_ANSWERED_RING_THRESHOLD,
    DEFAULT_ANSWERED_RING_THRESHOLD,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    HEALTHCHECK_INTERVAL,
    PUSH_WATCHDOG_TIMEOUT,
)
from .isapi.client import AuthError, CannotConnect, IsapiClient, IsapiError
from .isapi.const import CALL_IDLE, CALL_ON_CALL, CALL_RING
from .isapi.models import Capabilities, DeviceInfo

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class IntercomState:
    """Current intercom state, handed to the entities."""

    call_status: str = CALL_IDLE
    available: bool = True
    ring_channel: str = "polling"
    push_proven: bool = False
    last_call_answered: bool | None = None
    last_ring_duration: float | None = None
    push_degraded: bool = False
    last_event: dict | None = None

    @property
    def is_ringing(self) -> bool:
        return self.call_status == CALL_RING

    @property
    def is_on_call(self) -> bool:
        return self.call_status == CALL_ON_CALL


class HikvisionIntercomCoordinator(DataUpdateCoordinator[IntercomState]):
    """Keeps intercom state: push when possible, polling when not."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: IsapiClient,
        device_info: DeviceInfo,
        capabilities: Capabilities,
    ) -> None:
        self.client = client
        self.device_info = device_info
        self.capabilities = capabilities
        self.entry = entry

        # We ALWAYS start on polling, even when a push channel is available.
        # A push endpoint existing does not prove it delivers -- this
        # firmware's httpHosts accepts the configuration and never POSTs.
        # Trusting it up front would mean polling every 30s and missing rings
        # until the watchdog noticed. Polling is cheap (one GET); push is an
        # optimisation we only enable after a real event arrives.
        self._push_active = False
        self._push_candidate = capabilities.push_candidate
        self._last_push = time.monotonic()
        self._ring_callbacks: list = []
        self._call_result_callbacks: list = []
        self._ring_started_at: float | None = None

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {device_info.model}",
            update_interval=self._interval_for_mode(),
        )
        self.data = IntercomState(ring_channel="polling")

    def _interval_for_mode(self) -> timedelta:
        """Confirmed push allows a long interval; without push, tight polling."""
        seconds = HEALTHCHECK_INTERVAL if self._push_active else DEFAULT_POLL_INTERVAL
        return timedelta(seconds=seconds)

    # ------------------------------------------------------------------
    # Update cycle
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> IntercomState:
        """Read callStatus.

        In push mode this is only a health check (every 30s) that feeds the
        watchdog. In polling mode it is the source of truth for the doorbell.
        """
        try:
            status = await self.client.get_call_status()
        except AuthError as err:
            # Credentials revoked: ask for reauth instead of retrying forever.
            raise UpdateFailed(f"Authentication rejected: {err}") from err
        except (CannotConnect, IsapiError) as err:
            raise UpdateFailed(f"Failed to read intercom state: {err}") from err

        previous = self.data or IntercomState()
        self._check_push_watchdog(status, previous)

        state = replace(
            previous,
            call_status=status,
            available=True,
            push_degraded=previous.push_proven and not self._push_active,
        )

        # In polling mode this is where the doorbell is detected.
        if not self._push_active and status == CALL_RING and previous.call_status != CALL_RING:
            self._fire_ring(source="polling")

        return self._track_call_outcome(state, previous, status)

    def _track_call_outcome(
        self, state: IntercomState, previous: IntercomState, status: str
    ) -> IntercomState:
        """Decide whether the call that just ended was answered or missed.

        The device reports this nowhere, but the DURATION of the `ring` state
        gives it away: with nobody answering it runs out the ~31.2s timeout;
        when answered, `ring` ends the moment someone picks up (we measured
        13.0s answering on the indoor station and 4.4s in the app -- the two
        are indistinguishable from each other, but both far from the timeout).

        This is what makes a "nobody answered the door" automation possible.
        """
        if status == CALL_RING and previous.call_status != CALL_RING:
            self._ring_started_at = time.monotonic()
            return state

        # We left `ring`: this is where we can judge.
        if previous.call_status == CALL_RING and status != CALL_RING:
            if self._ring_started_at is None:
                return state
            duration = time.monotonic() - self._ring_started_at
            self._ring_started_at = None
            threshold = self.entry.options.get(
                CONF_ANSWERED_RING_THRESHOLD, DEFAULT_ANSWERED_RING_THRESHOLD
            )
            answered = duration < threshold
            # Always logged so the threshold can be calibrated: if your missed
            # calls do not last ~31s, this number tells you what to set the
            # option to.
            _LOGGER.debug(
                "Ring lasted %.1fs (threshold %.1fs) -> %s",
                duration,
                threshold,
                "answered" if answered else "missed",
            )
            for listener in list(self._call_result_callbacks):
                listener(answered, duration)
            return replace(state, last_call_answered=answered, last_ring_duration=duration)

        return state

    @callback
    def async_add_call_result_listener(self, listener) -> callback:
        """Register a callback invoked when a ring ends, with (answered, duration)."""
        self._call_result_callbacks.append(listener)

        @callback
        def remove() -> None:
            if listener in self._call_result_callbacks:
                self._call_result_callbacks.remove(listener)

        return remove

    def _check_push_watchdog(self, status: str, previous: IntercomState) -> None:
        """Demote to polling if the push channel stops delivering.

        Two triggers: divergence (polling saw a ring that push never reported)
        and prolonged silence. The first is immediate and the one that really
        matters -- it means we missed an actual doorbell press.
        """
        if not self._push_active:
            return

        diverged = status == CALL_RING and previous.call_status != CALL_RING
        silent = (time.monotonic() - self._last_push) > PUSH_WATCHDOG_TIMEOUT

        if diverged:
            _LOGGER.warning(
                "Push channel (%s) missed a ring that polling caught; "
                "switching to callStatus polling",
                self._push_candidate,
            )
            self._downgrade_to_polling()
        elif silent and status != CALL_IDLE:
            _LOGGER.warning(
                "Push channel (%s) has delivered nothing for %.0f min while the "
                "device was active; switching to polling",
                self._push_candidate,
                PUSH_WATCHDOG_TIMEOUT / 60,
            )
            self._downgrade_to_polling()

    def _downgrade_to_polling(self) -> None:
        self._push_active = False
        self.update_interval = self._interval_for_mode()

    # ------------------------------------------------------------------
    # Push input (httpHosts webhook)
    # ------------------------------------------------------------------

    @callback
    def async_handle_push_event(self, event: dict) -> None:
        """Handle an event the device pushed to the HA webhook."""
        self._last_push = time.monotonic()
        previous = self.data or IntercomState()

        if not self._push_active:
            # This is the only place push proves itself. Until the first real
            # event arrives we stay on polling, so we never miss a ring while
            # finding out whether this firmware pushes anything at all.
            _LOGGER.info(
                "Push channel (%s) delivered an event; switching to push mode",
                self._push_candidate,
            )
            self._push_active = True
            self.update_interval = self._interval_for_mode()

        status = event.get("call_status")
        new_status = (
            status if status in (CALL_IDLE, CALL_RING, CALL_ON_CALL) else previous.call_status
        )

        self.async_set_updated_data(
            replace(
                previous,
                call_status=new_status,
                last_event=event,
                available=True,
                push_proven=True,
                push_degraded=False,
                ring_channel=self._push_candidate or "polling",
            )
        )

        if new_status == CALL_RING and previous.call_status != CALL_RING:
            self._fire_ring(source="push")

    # ------------------------------------------------------------------
    # Doorbell
    # ------------------------------------------------------------------

    @callback
    def async_add_ring_listener(self, listener) -> callback:
        """Register a callback fired on each ring. Returns the remover."""
        self._ring_callbacks.append(listener)

        @callback
        def remove() -> None:
            if listener in self._ring_callbacks:
                self._ring_callbacks.remove(listener)

        return remove

    @callback
    def _fire_ring(self, *, source: str) -> None:
        _LOGGER.debug("Doorbell detected via %s", source)
        for listener in list(self._ring_callbacks):
            listener(source)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def async_execute(self, command, *, refresh: bool = True) -> None:
        """Run a command on the device and refresh state afterwards.

        Entities never talk to the client directly, so every write goes
        through here.
        """
        try:
            await command(self.client)
        except IsapiError as err:
            raise UpdateFailed(f"Command failed: {err}") from err
        if refresh:
            await self.async_request_refresh()
