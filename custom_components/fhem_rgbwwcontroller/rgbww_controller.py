import enum
import httpx
import asyncio, socket
import json


class _HttpMethod(enum.Enum):
    GET = enum.auto()
    POST = enum.auto()


class _TcpReceiver(asyncio.Protocol):
    def __init__(
        self, receive_callback: callable[str], on_con_lost: asyncio.Future
    ) -> None:
        self._receive_callback = receive_callback
        self.on_con_lost = on_con_lost
        self._buffer: list[bytes] = []

    def connection_made(self, transport):
        pass
        # transport.write(self.message.encode())
        # print("Data sent: {!r}".format(self.message))

    def _find_complete_json(self) -> tuple[str | None, list[bytes]]:
        """
        Scans the buffer for a complete, brace-balanced JSON object.
        Returns the complete JSON string and the remaining buffer.
        """
        brace_count = 0
        in_string = False
        start_index = -1

        for i, char in enumerate(self._buffer):
            if char == '"' and (i == 0 or self._buffer[i - 1] != "\\"):
                # Toggle in_string state, handling escaped quotes
                in_string = not in_string

            if not in_string:
                if char == "{":
                    if brace_count == 0:
                        start_index = i  # Mark the start of a new object
                    brace_count += 1
                elif char == "}":
                    brace_count -= 1
                    if brace_count == 0 and start_index != -1:
                        # Found a complete, top-level object
                        json_str = self._buffer[start_index : i + 1]
                        remaining_buffer = self._buffer[i + 1 :]
                        return json_str, remaining_buffer

        return None, self.buffer  # No complete object found

    def data_received(self, data):
        self._buffer += data
        json_str, self._buffer = self._find_complete_json()
        if json_str is not None:
            self._receive_callback(json_str)

    def connection_lost(self, exc):
        print("The server closed the connection")
        self.on_con_lost.set_result(True)


class RgbwwController:
    """The actual binding to the controller via network."""

    _TCP_PORT = 9090

    def __init__(self, host: str) -> None:
        self._host = host

    async def set_hsv(self, hue: int | None) -> None:
        data = {
            "hsv": {"h": 100, "s": 100, "v": 100, "ct": 2700},
            "cmd": "",  # transition type
            "t": 2.0,  # fade time
            "s": 1.0,  # fade speed
            "q": 1,
        }

        if hue is not None:
            data["hsv"]["h"] = hue

        await self._send_http_post("color", data)

    async def _on_json_received(self, json_str: str):
        payload = json.loads(json_str)

        match payload["method"]:
            case "color_event":
                ...
            # my $colorMode = "raw";
            # if ( exists $obj->{params}->{hsv} ) {
            #    $colorMode = "hsv";
            #    EspLedController_UpdateReadingsHsv( $hash, $obj->{params}{hsv}{h}, $obj->{params}{hsv}{s}, $obj->{params}{hsv}{v}, $obj->{params}{hsv}{ct} );
            # }
            # EspLedController_UpdateReadingsRaw( $hash, $obj->{params}{raw}{r}, $obj->{params}{raw}{g}, $obj->{params}{raw}{b}, $obj->{params}{raw}{cw}, $obj->{params}{raw}{ww} );
            # readingsSingleUpdate( $hash, 'colorMode', $colorMode, 1 );
            # }

            case "transition_finished":
                ...
            # elsif ( $obj->{method} eq "transition_finished" ) {
            # my $msg = $obj->{params}{name} . "," . ($obj->{params}{requeued} ? "requeued" : "finished");
            # readingsSingleUpdate( $hash, "tranisitionFinished", $msg, 1 );
            # }
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

    async def connect(self):
        """Connect to the device and keep connection alive in the event of a connection loss."""
        while True:
            loop = asyncio.get_running_loop()
            on_con_lost = loop.create_future()

            transport, protocol = await loop.create_connection(
                lambda: _TcpReceiver(self._on_data_received, on_con_lost),
                self._host,
                RgbwwController._TCP_PORT,
            )

            # Wait until the protocol signals that the connection
            # is lost and close the transport.
            try:
                await on_con_lost
            finally:
                transport.close()

            await asyncio.sleep(60)

    async def _send_http_post(self, endpoint: str, payload: dict[str, any]) -> None:
        headers = {
            "user-agent": "homeassistant-fhem_rgbwwcontroller",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"http://{self._host}/{endpoint}",
                json=payload,
                headers=headers,
            )

            if r.status_code != 200:
                raise RuntimeError("HTTP error response")
