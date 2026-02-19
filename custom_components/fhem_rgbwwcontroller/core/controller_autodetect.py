import asyncio
from collections.abc import Awaitable
import ipaddress
import logging
import os
import random

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
    return RgbwwController(ip)


async def _check_ip(hass: HomeAssistant, ip: str) -> RgbwwController | None:
    async with _scan_semaphore:
        controller = RgbwwController(hass, ip, http_request_timeout=2)

        try:
            await controller.refresh()
            mac = controller.info["connection"]["mac"]
            _logger.debug("Found device at %s with MAC %s", ip, mac)
        except ControllerUnavailableError:
            return None
        else:
            return controller
