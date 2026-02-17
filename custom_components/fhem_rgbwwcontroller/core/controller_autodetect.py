import asyncio
import ipaddress
import logging
import os
import random
import time
from collections.abc import Awaitable


from homeassistant.core import HomeAssistant

from .rgbww_controller import ControllerUnavailableError, RgbwwController

_logger = logging.getLogger(__name__)

_scan_semaphore = asyncio.Semaphore(25)  # Limit to 25 concurrent scans


def get_scan_coros(
    hass: HomeAssistant, network: ipaddress.IPv4Network
) -> list[Awaitable[RgbwwController | None]]:
    """Scans the given network for FHEM RGBWW Controller devices."""
    if network.prefixlen < 13:
        raise ValueError(
            "Network prefix is too broad. Please use a subnet mask of /12 or smaller."
        )

    if os.getenv("SIMULATION"):
        return [_check_ip_dummy(hass, str(ip)) for ip in network.hosts()]

    return [_check_ip(hass, str(ip)) for ip in network.hosts()]


async def _check_ip_dummy(ip: str) -> RgbwwController | None:
    await asyncio.sleep(random.randint(2, 20))
    if random.choice([True, False]):
        return None
    else:
        return RgbwwController(ip)


async def _check_ip(hass: HomeAssistant, ip: str) -> RgbwwController | None:
    async with _scan_semaphore:
        controller = RgbwwController(hass, ip)

        try:
            await controller.refresh()
            mac = controller.info["connection"]["mac"]
            _logger.debug("Found device at %s with MAC %s", ip, mac)
        except ControllerUnavailableError:
            return None
        else:
            return controller


async def main_autodetect():
    now = time.monotonic()
    # mask = AutoDetector.get_scan_range()

    network = ipaddress.IPv4Network("192.168.2.0/24")
    devices = await get_scan_coros(network)
    now2 = time.monotonic()
    print(f"Found {len(devices)} devices:")

    for device in devices:
        print(f"- {device.host}")


if __name__ == "__main__":
    asyncio.run(main_autodetect())
