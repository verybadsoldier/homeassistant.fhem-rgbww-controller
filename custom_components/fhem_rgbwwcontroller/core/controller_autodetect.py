import asyncio
import ipaddress
import logging
import os
import random
import time

import netifaces

from homeassistant.core import HomeAssistant

from .rgbww_controller import ControllerUnavailableError, RgbwwController

_logger = logging.getLogger(__name__)


def get_scan_range() -> ipaddress.IPv4Network | None:
    """
    Finds the active network interface and returns its IP range.
    """
    try:
        # Find the default gateway to determine the active interface
        gateways = netifaces.gateways()
        default_gateway = gateways.get("default", {}).get(netifaces.AF_INET)

        if not default_gateway:
            _logger.error(
                "❌ Could not find the default gateway. Please check your network connection."
            )
            return None

        interface = default_gateway[1]
        _logger.info(f"🌐 Found active interface: {interface}")

        # Get the addresses for the found interface
        addresses = netifaces.ifaddresses(interface)
        ipv4_info = addresses.get(netifaces.AF_INET)

        if not ipv4_info:
            _logger.info("❌ No IPv4 address found for interface %s.", str(interface))
            return None

        # Extract IP and netmask
        ip_address = ipv4_info[0]["addr"]
        netmask = ipv4_info[0]["netmask"]

        # Create a network object from the IP and netmask
        # The 'strict=False' part handles cases where the IP might be a network/broadcast address
        return ipaddress.IPv4Network(f"{ip_address}/{netmask}", strict=False)

    except Exception as e:
        _logger.exception("An error occurred when detecting the IP range.", exc_info=e)
        return None


def scan(
    hass: HomeAssistant, network: ipaddress.IPv4Network
) -> list[asyncio.Task[RgbwwController]]:
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
    devices = await scan(network)
    now2 = time.monotonic()
    print(f"Found {len(devices)} devices:")

    for device in devices:
        print(f"- {device.host}")


if __name__ == "__main__":
    asyncio.run(main_autodetect())
