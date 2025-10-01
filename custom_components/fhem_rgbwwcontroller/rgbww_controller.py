# noqa: D102
import ipaddress
import netifaces
import time
from dataclasses import dataclass
from datetime import timedelta
import enum
import httpx
import asyncio
import json
from typing import Literal, Protocol

import logging

_logger = logging.getLogger(__name__)


class _HttpMethod(enum.Enum):
    GET = enum.auto()
    POST = enum.auto()


class RgbwwStateUpdate(Protocol):
    def on_update_hsv(h: int | None, s: int | None, v: int | None) -> None: ...
    def on_connection_update(connected: bool) -> None: ...
    def on_transition_finished(name: str, requeued: bool) -> None: ...
    def on_sync_status(connected: bool) -> None: ...
    def on_config_update(config: dict) -> None: ...


class RgbwwReceiver(Protocol):
    def on_json_message(self, json_msg: dict) -> None:
        pass

    def on_connect_status_change(self, connected: bool) -> None:
        pass


class _TcpReceiver(asyncio.Protocol):
    def __init__(
        self,
        host: str,
        sink: RgbwwReceiver,
        on_con_lost: asyncio.Future,
    ) -> None:
        self._sink = sink
        self.on_con_lost = on_con_lost
        self._host = host

        self._buffer = ""
        self._transport: asyncio.Transport | None = None

    def connection_made(self, transport):
        _logger.error("%s - Connection established", self._host)
        self._sink.on_connect_status_change(True)

    def _consume_json_msg(self) -> dict | None:
        try:
            # Try to decode an object from the current position
            decoder = json.JSONDecoder()
            json_obj, end_pos = decoder.raw_decode(self._buffer)

            self._buffer = self._buffer[end_pos:]
            # If successful, process the message
            return json_obj
        except json.JSONDecodeError:
            # Not a complete JSON object yet, break and wait for more data
            return None

    def data_received(self, data):
        self._buffer += data.decode("utf-8")

        while (json_msg := self._consume_json_msg()) is not None:
            self._sink.on_json_message(json_msg)

    def connection_lost(self, exc):
        _logger.error("%s - Connection lost: %s", self._host, exc)
        self._sink.on_connect_status_change(False)

        if not self.on_con_lost.cancelled():
            self.on_con_lost.set_result(True)

        self._buffer = ""


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


class RgbwwController(RgbwwReceiver):
    """The actual binding to the controller via network."""

    _TCP_PORT = 9090
    TIMEOUT = 70

    def __init__(self, host: str) -> None:
        self.host = host
        self._connected = False
        self.color = _ColorState(0, 0, 0, 0, "raw", 0, 0, 0, 0, 0)
        self._connection_task: asyncio.Task | None = None
        self._info_cached: dict | None = None
        self._config_cached: dict | None = None

        self._on_con_lost: asyncio.Future | None = None
        self._on_con_established: asyncio.Future | None = None
        self._callbacks: list[RgbwwStateUpdate] = []
        self._transport: asyncio.Transport | None = None
        self._watchdog_handle: asyncio.TimerHandle | None = None
        self._buffer = ""
        self._stop_event = asyncio.Event()
        self._writer: asyncio.StreamWriter | None = None

    def _reset_watchdog(self):
        """Resets or starts the connection watchdog."""
        # Cancel the old timer if it exists
        if self._watchdog_handle:
            self._watchdog_handle.cancel()

        # Schedule the timeout_occurred method to be called after TIMEOUT seconds
        loop = asyncio.get_running_loop()
        self._watchdog_handle = loop.call_later(
            RgbwwController.TIMEOUT, self._timeout_occurred
        )

    def _consume_json_msg(self) -> dict | None:
        try:
            # Try to decode an object from the current position
            decoder = json.JSONDecoder()
            json_obj, end_pos = decoder.raw_decode(self._buffer)

            self._buffer = self._buffer[end_pos:]
            # If successful, process the message
            return json_obj
        except json.JSONDecodeError:
            # Not a complete JSON object yet, break and wait for more data
            return None

    def __data_received(self, data):
        self._buffer += data.decode("utf-8")

        while (json_msg := self._consume_json_msg()) is not None:
            self._sink.on_json_message(json_msg)

    async def _run_connection_task(self):
        """
        Connects to a server and automatically reconnects if the connection is lost.
        """
        host = self.host
        port = self._TCP_PORT
        while not self._stop_event.is_set():
            try:
                # 1. Attempt to connect
                _logger.info(f"🔌 Attempting to connect to %s:%s...", host, port)
                reader, self._writer = await asyncio.open_connection(host, port)

                # 2. Connection Established Notification
                # If we reach this line, the connection was successful.
                ##peername = writer.get_extra_info('peername')
                ##print(f"✅ Connection established with {peername}")
                self.on_connect_status_change(True)
                KEEPALIVE_TIMEOUT = 70
                # 3. Main loop to read data (your "work" goes here)
                while not self._stop_event.is_set():
                    # For your LED controller, this is where you'd wait for events.
                    try:
                        data = await asyncio.wait_for(reader.read(4096), timeout=KEEPALIVE_TIMEOUT) # Read up to 4KB
                    except asyncio.TimeoutError:
                        # No data, controller is gone...
                        _logger.warning("🔥 Keep-alive timeout! No data received for %s s.", KEEPALIVE_TIMEOUT)
                        break

                    if not data:
                        # This indicates the server has closed the connection gracefully.
                        _logger.warning("🚪 Server closed the connection.")
                        break # Exit the inner loop to trigger reconnection logic.

                    # --- PROCESS YOUR DATA HERE ---
                    self._buffer += data.decode("utf-8")

                    while (json_msg := self._consume_json_msg()) is not None:
                        self.on_json_message(json_msg)
                    # -----------------------------

            except (ConnectionRefusedError, OSError) as e:
                # This happens if the server is not running or unreachable
                _logger.warning(f"❌ Connection failed: {e}")

            except (ConnectionResetError, asyncio.IncompleteReadError) as e:
                # This happens if an established connection is lost mid-communication
                _logger.warning(f"💔 Connection lost: {e}")
                self.on_connect_status_change(False)

            except Exception as e:
                # Catch any other unexpected errors
                _logger.error(f"An unexpected error occurred: %s", str(e))
                self.on_connect_status_change(False)

            finally:
                # 4. Cleanup before retrying
                if self._writer:
                    self._writer.close()
                    await self._writer.wait_closed()

            # 5. Wait before reconnecting
            reconnect_delay = 60
            _logger.info(f"🔄 Reconnecting in %s seconds...", reconnect_delay)
            try:
                await asyncio.wait_for(self._stop_event, reconnect_delay)
            except asyncio.TimeoutError:
                pass

    async def _run_connection_task_(self):
        """Connect to the device and keep connection alive in the event of a connection loss."""
        while True:
            try:
                loop = asyncio.get_running_loop()
                self._on_con_lost = loop.create_future()
                self._on_con_established = loop.create_future()

                self._transport, _ = await loop.create_connection(
                    lambda: _TcpReceiver(
                        self.host,
                        self,
                        self._on_con_lost,
                    ),
                    self.host,
                    RgbwwController._TCP_PORT,
                )
                # Most of the time, the task will be waiting here
                await self._on_con_lost
            except Exception as e:
                _logger.error("%s - Connection error: %s", self.host, e)
            await asyncio.sleep(60)

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

    def on_connect_status_change(self, connected: bool) -> None:
        if connected:
            self._on_con_established.set_result(None)
            self._reset_watchdog()
        else:
            self._watchdog_handle.cancel()
            self._watchdog_handle = None
            self._transport = None

        self._connected = connected
        for x in self._callbacks:
            x.on_connection_update(connected)

    async def connect(self) -> None:
        """Connect to the controller (including reconnects)."""
        if self._connection_task is not None:
            return  # Connection task already running

        self._stop_event = asyncio.Event()
        self._connection_task = asyncio.create_task(
            self._run_connection_task(), name="fhem_rgbwwcontroller_connection"
        )



    async def connect_wait_for_connection(
        self, timeout: timedelta = timedelta(seconds=10)
    ) -> None:
        """Connect to the controller (including reconnects)."""
        self.connect()
        await asyncio.wait_for(self._on_con_established, timeout)

    async def disconnect(self):
        """
        External function to signal the client to stop.
        """
        if self._stop_event.is_set():
            return # Already stopping

        _logger.error("%s - Disconnecting", self.host)

        # 1. Signal the loop to not attempt reconnection
        self._stop_event.set()

        # 2. If there's an active connection, close it to interrupt reader.read()
        if self._writer:
            _logger.info("Closing active connection...")
            self._writer.close()
            await self._writer.wait_closed()

    def _timeout_occurred(self):
        _logger.warning(
            "%s - ❌ Watchdog timeout! No message received for %s seconds. Closing the connection.",
            self.host,
            RgbwwController.TIMEOUT,
        )
        self.disconnect()

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
        data: dict[str, any] = {"hsv": {}}

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

    @property
    def connected(self):
        return self._connected

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
        self._reset_watchdog()

        match json_msg["method"]:
            case "color_event":
                self._update_colorstate_from_json(json_msg["params"])
                # if "hsv" in json_msg["params"]:
                #    self.color.hue = json_msg["params"]["hsv"]["h"]
                #    self.color.saturation = json_msg["params"]["hsv"]["s"]
                #    self.state.color_temp = json_msg["params"]["hsv"]["ct"]
                #    self.color.brightness = json_msg["params"]["hsv"]["v"]

                # if "raw" in json_msg["params"]:
                #    self.color.raw_ww = json_msg["params"]["raw"]["ww"]
                #    self.color.raw_cw = json_msg["params"]["raw"]["cw"]
                #    self.color.raw_r = json_msg["params"]["raw"]["r"]
                #    self.color.raw_g = json_msg["params"]["raw"]["g"]
                #    self.color.raw_b = json_msg["params"]["raw"]["b"]

                # self.state.color_mode = json_msg["params"]["mode"]
                print(f"{self.host} - {self.color}")

                for x in self._callbacks:
                    x.on_update_hsv(
                        self.color.hue, self.color.saturation, self.color.brightness
                    )
            # my $colorMode = "raw";
            # if ( exists $obj->{params}->{hsv} ) {
            #    $colorMode = "hsv";
            #    EspLedController_UpdateReadingsHsv( $hash, $obj->{params}{hsv}{h}, $obj->{params}{hsv}{s}, $obj->{params}{hsv}{v}, $obj->{params}{hsv}{ct} );
            # }
            # EspLedController_UpdateReadingsRaw( $hash, $obj->{params}{raw}{r}, $obj->{params}{raw}{g}, $obj->{params}{raw}{b}, $obj->{params}{raw}{cw}, $obj->{params}{raw}{ww} );
            # readingsSingleUpdate( $hash, 'colorMode', $colorMode, 1 );
            # }

            case "transition_finished":
                for x in self._callbacks:
                    x.on_transition_finished(
                        json_msg["params"]["name"], json_msg["params"]["requeued"]
                    )
            # elsif ( $obj->{method} eq "transition_finished" ) {
            # my $msg = $obj->{params}{name} . "," . ($obj->{params}{requeued} ? "requeued" : "finished");
            # readingsSingleUpdate( $hash, "tranisitionFinished", $msg, 1 );
            # }
            case "config_event":
                self._config_cached = json_msg["params"]
                for x in self._callbacks:
                    x.on_config_update(self._config_cached)
            case "keep_alive":
                ...
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

        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"http://{self.host}/{endpoint}",
                json=payload,
                headers=headers,
            )

            r.raise_for_status()

    async def _send_http_get(self, endpoint: str) -> str:
        headers = {
            "user-agent": "homeassistant-fhem_rgbwwcontroller",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"http://{self.host}/{endpoint}",
                headers=headers,
            )

            r.raise_for_status()

            return r.text


# class AutoDetector:
#    @staticmethod
#    def get_scan_range() -> ipaddress.IPv4Network | None:
#        """
#        Finds the active network interface and returns its IP range.
#        """
#        try:
#            # Find the default gateway to determine the active interface
#            gateways = netifaces.gateways()
#            default_gateway = gateways.get("default", {}).get(netifaces.AF_INET)
#
#            if not default_gateway:
#                _logger.error(
#                    "❌ Could not find the default gateway. Please check your network connection."
#                )
#                return None
#
#            interface = default_gateway[1]
#            _logger.info(f"🌐 Found active interface: {interface}")
#
#            # Get the addresses for the found interface
#            addresses = netifaces.ifaddresses(interface)
#            ipv4_info = addresses.get(netifaces.AF_INET)
#
#            if not ipv4_info:
#                _logger.info(
#                    "❌ No IPv4 address found for interface %s.", str(interface)
#                )
#                return None
#
#            # Extract IP and netmask
#            ip_address = ipv4_info[0]["addr"]
#            netmask = ipv4_info[0]["netmask"]
#
#            # Create a network object from the IP and netmask
#            # The 'strict=False' part handles cases where the IP might be a network/broadcast address
#            network = ipaddress.IPv4Network(f"{ip_address}/{netmask}", strict=False)
#            return network
#
#        except Exception as e:
#            _logger.exception(
#                "An error occurred when detecting the IP range.", exc_info=e
#            )
#            return None
#
#    @staticmethod
#    async def scan(network: ipaddress.IPv4Network) -> list[RgbwwController]:
#        tasks = []
#
#        for ip in network.hosts():
#            tasks.append(AutoDetector._check_ip(str(ip)))
#
#        results = await asyncio.gather(*tasks)
#
#        found_devices = [res for res in results if res is not None]
#
#        return found_devices
#
#    @staticmethod
#    async def _check_ip(ip: str) -> RgbwwController | None:
#        controller = RgbwwController(ip)
#
#        try:
#            info = await controller.get_info()
#            mac = info["connection"]["mac"]
#            print(f"Found device at {ip} with MAC {mac}")
#            return controller
#        except (httpx.HTTPError, asyncio.TimeoutError):
#            pass
#
#
# async def main_autodetect():
#    now = time.monotonic()
#    # mask = AutoDetector.get_scan_range()
#
#    network = ipaddress.IPv4Network("192.168.2.0/24")
#    devices = await AutoDetector.scan(network)
#    now2 = time.monotonic()
#    print(f"Found {len(devices)} devices:")
#
#    for device in devices:
#        print(f"- {device.host}")
#
#
# async def main():
#    a = RgbwwController("192.168.2.53")
#    info = await a.get_info()
#    await a.connect()
#
#    await a.set_hsv(brightness=100)
#
#    await asyncio.sleep(120)
#
#
# if __name__ == "__main__":
#    asyncio.run(main_autodetect())
