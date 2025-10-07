# noqa: D102
import asyncio
import contextlib
import enum
import json
import logging
from dataclasses import dataclass
from typing import Literal, Protocol
import async_timeout
from aiohttp import ClientError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.core import HomeAssistant
from homeassistant.util import aiohttp

_logger = logging.getLogger(__name__)


class _HttpMethod(enum.Enum):
    GET = enum.auto()
    POST = enum.auto()


class ControllerUnavailableError(Exception):
    """Custom exception for when the controller is unavailable."""

    pass


class RgbwwStateUpdate(Protocol):
    def on_update_color() -> None: ...
    def on_connection_update() -> None: ...
    def on_transition_finished(name: str, requeued: bool) -> None: ...
    def on_config_update() -> None: ...
    def on_state_completed() -> None: ...


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


class RgbwwController:
    """The actual binding to the controller via network."""

    _TCP_PORT = 9090
    _WATCHDOG_DISCONNECT_TIMEOUT = 70
    _HTTP_REQUEST_TIMEOUT = 30

    def __init__(self, hass: HomeAssistant, host: str) -> None:
        self._hass = hass
        self.host = host
        self.connected = False
        self.color = _ColorState(0, 0, 0, 0, "raw", 0, 0, 0, 0, 0)
        self._connection_task: asyncio.Task | None = None
        self._info_cached: dict | None = None
        self._config_cached: dict | None = None

        self._callbacks: list[RgbwwStateUpdate] = []
        self._buffer = ""
        self._stop_event = asyncio.Event()
        self._writer: asyncio.StreamWriter | None = None
        self.state_completed = False

    def _consume_json_msg(self) -> dict | None:
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
                        self.on_json_message(json_msg)
                    # -----------------------------

            except (ConnectionRefusedError, OSError) as e:
                # This happens if the server is not running or unreachable
                _logger.warning("❌ Connection failed: %s", str(e))

            except (ConnectionResetError, asyncio.IncompleteReadError) as e:
                # This happens if an established connection is lost mid-communication
                _logger.warning("💔 Connection lost: %s", str(e))

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
            pass

    def register_callback(self, rcv: RgbwwStateUpdate) -> None:
        """Register a callback object."""
        if rcv in self._callbacks:
            raise ValueError("Already registered")

        self._callbacks.append(rcv)

    def unregister_callback(self, rcv: RgbwwStateUpdate) -> None:
        """Un-Register a callback object."""
        if rcv not in self._callbacks:
            raise ValueError("Receiver not registered")

        self._callbacks.remove(rcv)

    async def on_connect_status_change(self, connected: bool) -> None:
        if connected == self.connected:
            return  # No change

        self.connected = connected
        for x in self._callbacks:
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

    async def set_hsv(
        self,
        hue: int | None = None,
        saturation: int | None = None,
        brightness: int | None = None,
        t: float | None = None,
        ct: int | None = None,
    ) -> None:
        # data = {
        #    "hsv": {"h": 100, "s": 100, "v": 100, "ct": 2700},
        #    "cmd": "",  # transition type
        #    "t": 2.0,  # fade time
        #    "s": 1.0,  # fade speed
        #    "q": 1,
        # }
        data: dict[str, any] = {"hsv": {}, "cmd": "fade", "q": 1}

        if hue is not None:
            data["hsv"]["h"] = hue

        if brightness is not None:
            data["hsv"]["v"] = brightness

        if saturation is not None:
            data["hsv"]["s"] = saturation

        if ct is not None:
            data["hsv"]["ct"] = ct

        if t is not None:
            data["t"] = t

        await self._send_http_post("color", data)

    async def set_raw(
        self,
        r: int | None = None,
        g: int | None = None,
        b: int | None = None,
        cw: int | None = None,
        ww: int | None = None,
    ) -> None:
        data: dict[str, any] = {"raw": {}}

        if r is not None:
            data["raw"]["r"] = r

        if g is not None:
            data["raw"]["g"] = g

        if b is not None:
            data["raw"]["b"] = b

        if cw is not None:
            data["raw"]["cw"] = cw

        if ww is not None:
            data["raw"]["ww"] = ww

        await self._send_http_post("color", data)

    def _update_colorstate_from_json(self, json_msg: dict) -> None:
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

    def on_json_message(self, json_msg: dict) -> None:
        # ANY data from the server resets the timer.
        match json_msg["method"]:
            case "color_event":
                self._update_colorstate_from_json(json_msg["params"])
                print(f"{self.host} - {self.color}")

                for x in self._callbacks:
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
                for x in self._callbacks:
                    x.on_transition_finished(
                        json_msg["params"]["name"], json_msg["params"]["requeued"]
                    )
            # elsif ( $obj->{method} eq "transition_finished" ) {
            # my $msg = $obj->{params}{name} . "," . ($obj->{params}{requeued} ? "requeued" : "finished");
            # readingsSingleUpdate( $hash, "tranisitionFinished", $msg, 1 );
            # }
            case "config":
                self._config_cached = json_msg["params"]
                for x in self._callbacks:
                    x.on_config_update()
            case "keep_alive":
                ...
            case "state_completed":
                self.state_completed = True
                for x in self._callbacks:
                    x.on_state_completed()

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
        json_data = await self._send_http_get("info")
        self._info_cached = json.loads(json_data)

    async def _refresh_config(self) -> None:
        json_data = await self._send_http_get("config")
        self._config_cached = json.loads(json_data)

    async def _refresh_color(self) -> None:
        json_data = await self._send_http_get("color")
        self._update_colorstate_from_json(json.loads(json_data))

    @property
    def info(self) -> dict:
        if self._info_cached is None:
            raise RuntimeError("Info not loaded yet")
        return self._info_cached

    @property
    def config(self) -> dict:
        if self._config_cached is None:
            raise RuntimeError("Config not loaded yet")
        return self._config_cached

    @property
    def device_name(self) -> str:
        return self._config_cached["general"]["device_name"]

    async def _send_http_post(self, endpoint: str, payload: dict[str, any]) -> None:
        headers = {
            "user-agent": "homeassistant-fhem_rgbwwcontroller",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        session = async_get_clientsession(self._hass)
        try:
            # Use a timeout to prevent the request from hanging indefinitely
            async with async_timeout.timeout(self._HTTP_REQUEST_TIMEOUT):
                # The actual request using the shared session
                response = await session.post(
                    f"http://{self.host}/{endpoint}", json=payload, headers=headers
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

    async def _send_http_get(self, endpoint: str) -> str:
        headers = {
            "user-agent": "homeassistant-fhem_rgbwwcontroller",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        session = async_get_clientsession(self._hass)
        try:
            # Use a timeout to prevent the request from hanging indefinitely
            async with async_timeout.timeout(self._HTTP_REQUEST_TIMEOUT):
                # The actual request using the shared session
                response = await session.get(
                    f"http://{self.host}/{endpoint}", headers=headers
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
