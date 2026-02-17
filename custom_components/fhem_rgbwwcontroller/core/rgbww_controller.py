import asyncio
from collections.abc import Sequence
import contextlib
from dataclasses import asdict, dataclass
import json
import logging
import os
import random
import time
from typing import Any, Literal, Protocol, Self

from aiohttp import ClientError

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .color_commands import ColorCommandBase, ColorCommandHsv, ColorCommandRgbww

_logger = logging.getLogger(__name__)

_HTTP_HEADERS = {
    "user-agent": "homeassistant-fhem_rgbwwcontroller",
    "Accept": "application/json",
    "Content-Type": "application/json",
}


class ControllerUnavailableError(Exception):
    """Custom exception for when the controller is unavailable."""

    ...


class RgbwwStateUpdate(Protocol):
    def on_update_color(self) -> None: ...
    def on_connection_update(self) -> None: ...
    def on_transition_finished(self, name: str, requeued: bool) -> None: ...
    def on_config_update(self) -> None: ...
    def on_state_completed(self) -> None: ...
    def on_clock_slave_status_update(self) -> None: ...
    def delme_func(self) -> None: ...


@dataclass
class _ColorState:
    color_temp: int
    hue: int
    saturation: int
    brightness: int
    color_mode: Literal["raw", "hsv"]
    raw_r: int
    raw_g: int
    raw_b: int
    raw_ww: int
    raw_cw: int


@dataclass
class ControllerColorHsv:
    h: str | None = None
    s: str | None = None
    v: str | None = None
    ct: str | None = None


@dataclass
class ControllerColorRaw:
    r: str | None = None
    g: str | None = None
    b: str | None = None
    cw: str | None = None
    ww: str | None = None


@dataclass
class ControllerApiColorCommand:
    """Command to be sent to the controller API. Using the exact field names as expected by the API."""

    hsv: ControllerColorHsv | None = None
    raw: ControllerColorRaw | None = None
    s: int | None = None  # fade speed
    t: int | None = None  # fade duration
    stay: int | None = None
    q: None = None
    name: str | None = None
    r: bool | None = None
    d: Literal["long", "short"] | None = None

    @staticmethod
    def _gather_base_args(cmd: ColorCommandBase) -> dict[str, Any]:
        args: dict[str, Any] = {}
        args["name"] = cmd.anim_name

        if cmd.use_speed:
            args["s"] = cmd.speed_or_fade_duration
        else:
            args["t"] = cmd.speed_or_fade_duration

        args["stay"] = cmd.stay

        if cmd.queue_policy is not None:
            args["q"] = cmd.queue_policy.value

        args["r"] = cmd.requeue

        if cmd.direction_long is not None:
            args["d"] = "long" if cmd.direction_long else "short"
        return args

    @classmethod
    def from_color_command(cls, cmd: ColorCommandHsv | ColorCommandRgbww) -> Self:
        base_args = cls._gather_base_args(cmd)
        ctrl_cmd = cls(**base_args)
        ctrl_cmd.t = cmd.speed_or_fade_duration

        if isinstance(cmd, ColorCommandHsv):
            hsv = ControllerColorHsv()
            hsv.h = cmd.h
            hsv.s = cmd.s
            hsv.v = cmd.v
            hsv.ct = cmd.ct
            ctrl_cmd.hsv = hsv
        else:  # ColorCommandRgbww
            raw = ControllerColorRaw()
            raw.r = cmd.r
            raw.g = cmd.g
            raw.b = cmd.b
            raw.cw = cmd.cw
            raw.ww = cmd.ww
            ctrl_cmd.raw = raw

        return ctrl_cmd

    def asdict_compact(self):
        return asdict(
            self, dict_factory=lambda x: {k: v for (k, v) in x if v is not None}
        )


_SIM_RESPONSES: dict[str, Any] = {
    "info": {
        "firmware": "9.0-sim",
        "heap_free": 21123,
        "connection": {"mac": "a020a60836aa"},
        "git_version": "9.00-sim.git",
        "webapp_version": "1.0-Shojo",
    },
    "config": {
        "network": {"mqtt": {"enabled": True, "server": "mqtthost"}},
        "color": {"colortemp": {"cw": 5000, "ww": 2700}},
        "sync": {"cmd_slave_enabled": True},
    },
    "color": {
        "hsv": {"h": 54, "s": 50, "v": 50, "ct": 3000},
        "rgbww": {"r": 500, "g": 500, "b": 500, "cw": 500, "ww": 500},
        "mode": "hsv",
    },
    "clock_slave_status": {
        "offset": 0,
        "current_interval": 50,
    },
    "state_completed": {},
}


class RgbwwController:
    """The actual binding to the controller via network."""

    _TCP_PORT = 9090
    _WATCHDOG_DISCONNECT_TIMEOUT = 70
    _HTTP_REQUEST_TIMEOUT = 2

    def __init__(self, hass: HomeAssistant, host: str) -> None:
        self._hass = hass
        self.host = host
        self.connected = False
        self.color = _ColorState(0, 0, 0, 0, "raw", 0, 0, 0, 0, 0)
        self._connection_task: asyncio.Task[None] | None = None
        self._info_cached: dict[str, Any] | None = None
        self._config_cached: dict[str, Any] | None = None
        self._clock_slave_status_cache: dict[str, Any] | None = None

        self._callbacks: dict[int, RgbwwStateUpdate] = {}
        self._buffer = ""
        self._stop_event = asyncio.Event()
        self._writer: asyncio.StreamWriter | None = None
        self.state_completed = False
        self._simulation = os.getenv("SIMULATION")

    def _consume_json_msg(self) -> dict[str, Any] | None:
        try:
            # Try to decode an object from the current position
            decoder = json.JSONDecoder()
            json_obj, end_pos = decoder.raw_decode(self._buffer)

            self._buffer = self._buffer[end_pos:]
        except json.JSONDecodeError:
            # Not a complete JSON object yet, break and wait for more data
            return None
        else:
            return json_obj

    async def _run_connection_task(self):
        """
        Connects to a server and automatically reconnects if the connection is lost.
        """
        self._buffer = ""
        
        if self._simulation:
            try:
                init = True
                last_slave_offset = time.monotonic()
                while not self._stop_event.is_set():

                    def _get_rpc(name: str) -> dict[str, Any]:
                        method = name
                        if method == "color":
                            method = "color_event"
                        return {"method": method, "params": _SIM_RESPONSES[name]}

                    if init:
                        await asyncio.sleep(0.3)
                        self._on_json_message(_get_rpc("info"))
                        await asyncio.sleep(0.3)
                        self._on_json_message(_get_rpc("config"))
                        await asyncio.sleep(0.3)
                        self._on_json_message(_get_rpc("color"))
                        self._on_json_message(_get_rpc("state_completed"))
                        init = False
                    await asyncio.sleep(1)

                    now = time.monotonic()
                    if now - last_slave_offset > 5:
                        last_slave_offset = now
                        status = _get_rpc("clock_slave_status")
                        status["params"]["current_interval"] = random.randint(
                            19000, 21000
                        )
                        status["params"]["offset"] = random.randint(-10, 10)
                        self._on_json_message(status)
            except Exception as e:
                # Catch any other unexpected errors
                _logger.exception("An unexpected error occurred", exc_info=e)

        while not self._stop_event.is_set():
            try:
                # 1. Attempt to connect
                _logger.info(
                    "🔌 Attempting to connect to %s:%s...", self.host, self._TCP_PORT
                )
                reader, self._writer = await asyncio.open_connection(
                    self.host, self._TCP_PORT
                )

                # 2. Connection Established Notification
                # If we reach this line, the connection was successful.
                ##peername = writer.get_extra_info('peername')
                ##print(f"✅ Connection established with {peername}")
                await self.on_connect_status_change(True)

                # 3. Main loop to read data (your "work" goes here)
                while not self._stop_event.is_set():
                    # For your LED controller, this is where you'd wait for events.
                    try:
                        data = await asyncio.wait_for(
                            reader.read(4096), timeout=self._WATCHDOG_DISCONNECT_TIMEOUT
                        )  # Read up to 4KB
                    except TimeoutError:
                        # No data, controller is gone...
                        _logger.warning(
                            "🔥 Keep-alive timeout! No data received for %s s.",
                            self._WATCHDOG_DISCONNECT_TIMEOUT,
                        )
                        break

                    if not data:
                        # This indicates the server has closed the connection gracefully.
                        _logger.warning("🚪 Server closed the connection.")
                        break  # Exit the inner loop to trigger reconnection logic.

                    # --- PROCESS YOUR DATA HERE ---
                    self._buffer += data.decode("utf-8")

                    while (json_msg := self._consume_json_msg()) is not None:
                        self._on_json_message(json_msg)
                    # -----------------------------
            except (ConnectionResetError, asyncio.IncompleteReadError) as e:
                # This happens if an established connection is lost mid-communication
                _logger.warning("💔 Connection lost: %s", str(e))

            except (ConnectionRefusedError, OSError) as e:
                # This happens if the server is not running or unreachable
                _logger.warning("❌ Connection failed: %s", str(e))

            except Exception as e:
                # Catch any other unexpected errors
                _logger.error("An unexpected error occurred: %s", str(e))

            finally:
                # 4. Cleanup before retrying
                if self._writer:
                    self._writer.close()
                    await self._writer.wait_closed()
                await self.on_connect_status_change(False)

            reconnect_delay = 10
            _logger.info("🔄 Reconnecting in %s seconds...", reconnect_delay)

            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), reconnect_delay)

    def register_callback(self, rcv: RgbwwStateUpdate) -> None:
        """Register a callback object."""
        rcv_id = id(rcv)
        if rcv_id in self._callbacks:
            raise ValueError("Already registered")

        self._callbacks[rcv_id] = rcv

    def unregister_callback(self, rcv: RgbwwStateUpdate) -> None:
        """Un-Register a callback object."""
        rcv_id = id(rcv)
        if rcv_id not in self._callbacks:
            raise ValueError("Receiver not registered")

        del self._callbacks[rcv_id]

    async def on_connect_status_change(self, connected: bool) -> None:
        if connected == self.connected:
            return  # No change

        self.connected = connected
        for x in self._callbacks.values():
            x.on_connection_update()

    async def connect(self) -> None:
        """Connect to the controller (including reconnects)."""
        if self._connection_task is not None:
            return  # Connection task already running

        self._stop_event = asyncio.Event()
        self._connection_task = asyncio.create_task(
            self._run_connection_task(), name="fhem_rgbwwcontroller_connection"
        )

    async def disconnect(self):
        """
        External function to signal the client to stop.
        """
        if self._stop_event.is_set():
            return  # Already stopping

        _logger.error("%s - Disconnecting", self.host)

        # 1. Signal the loop to not attempt reconnection
        self._stop_event.set()

        # 2. If there's an active connection, close it to interrupt reader.read()
        if self._writer:
            _logger.info("Closing active connection...")
            self._writer.close()
            await self._writer.wait_closed()

    async def send_color_command(
        self, color_command: ColorCommandHsv | ColorCommandRgbww
    ) -> None:
        await self._send_color(
            payload=ControllerApiColorCommand.from_color_command(
                color_command
            ).asdict_compact()
        )

    async def send_color_commands(
        self, anim_commands: Sequence[ColorCommandHsv | ColorCommandRgbww]
    ) -> None:
        cmds = {
            "cmds": [
                ControllerApiColorCommand.from_color_command(x).asdict_compact()
                for x in anim_commands
            ]
        }
        await self._send_color(cmds)

    async def _send_color(self, payload: dict[str, Any]) -> None:
        await self._send_http_post("color", payload=payload)

    async def send_channel_command(
        self,
        command: Literal["pause", "continue", "stop"],
        channels: list[str],
    ) -> None:
        channel_name_map = {
            "hue": "h",
            "saturation": "s",
            "value": "v",
            "color_temp": "ct",
        }
        if command not in ["pause", "continue", "stop"]:
            raise ValueError("Invalid command")

        for ch in channels:
            if ch not in channel_name_map:
                raise ValueError(f"Invalid channel: {ch}")

        channels = [channel_name_map[ch] for ch in channels]
        data: dict[str, Any] = {"channels": channels}

        await self._send_http_post(command, data)

    def _update_colorstate_from_json(self, json_msg: dict[str, Any]) -> None:
        if "hsv" in json_msg:
            self.color.hue = json_msg["hsv"].get("h", self.color.hue)
            self.color.saturation = json_msg["hsv"].get("s", self.color.saturation)
            self.color.color_temp = json_msg["hsv"].get("ct", self.color.color_temp)
            self.color.brightness = json_msg["hsv"].get("v", self.color.brightness)

        if "raw" in json_msg:
            self.color.raw_ww = json_msg["raw"].get("ww", self.color.raw_ww)
            self.color.raw_cw = json_msg["raw"].get("cw", self.color.raw_cw)
            self.color.raw_r = json_msg["raw"].get("r", self.color.raw_r)
            self.color.raw_g = json_msg["raw"].get("g", self.color.raw_g)
            self.color.raw_b = json_msg["raw"].get("b", self.color.raw_b)

        if "mode" in json_msg:
            self.color.color_mode = json_msg["mode"]

    def _on_json_message(self, json_msg: dict[str, Any]) -> None:
        # ANY data from the server resets the timer.
        match json_msg["method"]:
            case "color_event":
                self._update_colorstate_from_json(json_msg["params"])
                print(f"{self.host} - {self.color}")

                for x in self._callbacks.values():
                    x.on_update_color()
            # my $colorMode = "raw";
            # if ( exists $obj->{params}->{hsv} ) {
            #    $colorMode = "hsv";
            #    EspLedController_UpdateReadingsHsv( $hash, $obj->{params}{hsv}{h}, $obj->{params}{hsv}{s}, $obj->{params}{hsv}{v}, $obj->{params}{hsv}{ct} );
            # }
            # EspLedController_UpdateReadingsRaw( $hash, $obj->{params}{raw}{r}, $obj->{params}{raw}{g}, $obj->{params}{raw}{b}, $obj->{params}{raw}{cw}, $obj->{params}{raw}{ww} );
            # readingsSingleUpdate( $hash, 'colorMode', $colorMode, 1 );
            # }
            case "info":
                self._info_cached = json_msg["params"]
            case "transition_finished":
                for x in self._callbacks.values():
                    x.on_transition_finished(
                        json_msg["params"]["name"], json_msg["params"]["requeued"]
                    )
            # elsif ( $obj->{method} eq "transition_finished" ) {
            # my $msg = $obj->{params}{name} . "," . ($obj->{params}{requeued} ? "requeued" : "finished");
            # readingsSingleUpdate( $hash, "tranisitionFinished", $msg, 1 );
            # }
            case "config":
                self._config_cached = json_msg["params"]
                for x in self._callbacks.values():
                    x.on_config_update()
            case "keep_alive":
                ...
            case "state_completed":
                self.state_completed = True
                for x in self._callbacks.values():
                    x.on_state_completed()
            case "clock_slave_status":
                self._clock_slave_status_cache = json_msg["params"]
                for x in self._callbacks.values():
                    x.on_clock_slave_status_update()

            # elsif ( $obj->{method} eq "keep_alive" ) {
            # Log3( $hash, 4, "$hash->{NAME}: EspLedController_Read: keep_alive received" );
            # $hash->{LAST_RECV} = $now;
            # }
            case "clock_slave_status":
                ...
            # elsif ( $obj->{method} eq "clock_slave_status" ) {
            # readingsBeginUpdate($hash);
            # readingsBulkUpdate( $hash, 'clockSlaveOffset',     $obj->{params}{offset} );
            # readingsBulkUpdate( $hash, 'clockCurrentInterval', $obj->{params}{current_interval} );
            # readingsEndUpdate( $hash, 1 );
            # }
            case _:
                ...
            # else {
            # Log3( $name, 3, "$hash->{NAME}: EspLedController_ProcessRead: Unknown message type: " . $obj->{method} );
            # }

    async def refresh(self) -> None:
        """Refresh the state by requesting it from the controller."""
        await self._refresh_info()
        await self._refresh_config()
        await self._refresh_color()

    async def _refresh_info(self) -> None:
        self._info_cached = await self._send_http_get("info")

    async def _refresh_config(self) -> None:
        self._config_cached = await self._send_http_get("config")

    async def _refresh_color(self) -> None:
        json_data = await self._send_http_get("color")
        self._update_colorstate_from_json(json_data)

    @property
    def info(self) -> dict[str, Any]:
        if self._info_cached is None:
            raise RuntimeError("Info not loaded yet")
        return self._info_cached

    @property
    def config(self) -> dict[str, Any]:
        if self._config_cached is None:
            raise RuntimeError("Config not loaded yet")
        return self._config_cached

    @property
    def device_name(self) -> str:
        if self._config_cached is None:
            raise RuntimeError("Config not loaded yet")
        return self._config_cached["general"]["device_name"]

    @property
    def clock_slave_status(self) -> dict[str, Any] | None:
        return self._clock_slave_status_cache

    async def _send_http_post(self, endpoint: str, payload: dict[str, Any]) -> None:
        if self._simulation:
            if endpoint == "config":
                return None
            raise HomeAssistantError("Endpoint not supported by simulation")

        print("POST %s - Payload: %s", endpoint, payload)

        session = async_get_clientsession(self._hass)
        try:
            # Use a timeout to prevent the request from hanging indefinitely
            async with asyncio.timeout(self._HTTP_REQUEST_TIMEOUT):
                # The actual request using the shared session
                response = await session.post(
                    f"http://{self.host}/{endpoint}",
                    json=payload,
                    headers=_HTTP_HEADERS,
                )

                # Raise an exception if the response has an error status (4xx or 5xx)
                response.raise_for_status()

                return await response.json()

        # Handle cases where the device is offline or the connection fails
        except (ClientError, asyncio.TimeoutError) as err:
            raise ControllerUnavailableError(
                f"Failed to connect to controller: {err}"
            ) from err

    async def _send_http_get(self, endpoint: str) -> dict[str, Any]:
        if self._simulation:
            if endpoint not in _SIM_RESPONSES:
                raise HomeAssistantError("Endpoint not supported by simulation")
            return _SIM_RESPONSES[endpoint]

        session = async_get_clientsession(self._hass)
        try:
            # Use a timeout to prevent the request from hanging indefinitely
            async with asyncio.timeout(self._HTTP_REQUEST_TIMEOUT):
                # The actual request using the shared session
                response = await session.get(
                    f"http://{self.host}/{endpoint}", headers=_HTTP_HEADERS
                )

                # Raise an exception if the response has an error status (4xx or 5xx)
                response.raise_for_status()

                # Return the JSON response
                return await response.json()

        # Handle cases where the device is offline or the connection fails
        except (ClientError, asyncio.TimeoutError) as err:
            raise ControllerUnavailableError(
                f"Failed to connect to controller: {err}"
            ) from err


if __name__ == "__main__":
    ctrl = RgbwwController(None, "192.168.2.53")
    ctrl.send_color_command(ColorCommandHsv(h=120, s=100, v=100))
