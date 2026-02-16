"""Config flow for the FHEM RGBWW Controller integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import datetime
import ipaddress
import logging
from typing import Any, cast

from httpx import HTTPError
import voluptuous as vol

from .core.rgbww_controller import (
    RgbwwController,
)
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.helpers.selector import TextSelector, selector
from homeassistant.util import dt as dt_util

from .const import DISCOVERY_RESULTS, DOMAIN
from .core import controller_autodetect

_logger = logging.getLogger(__name__)


class _InvalidHostError(RuntimeError):
    """Error to indicate that the controller host is invalid."""

    def __init__(self, host: str) -> None:
        super().__init__(f"Cannot retrieve MAC address from host {host}")
        self.host = host


@dataclass
class DiscoveryResult:
    controllers: dict[str, RgbwwController]
    timestamp: datetime


class RgbwwConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for FHEM RGBWW Controller."""

    VERSION = 1

    def __init__(self):
        super().__init__()
        self._scan_tasks: list[asyncio.Task] | None = None
        self._scan_monitor_task: asyncio.TaskGroup | None = None
        self._scan_network: ipaddress.IPv4Network | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self.hass.data.setdefault(DOMAIN, {})
        self.hass.data[DOMAIN].setdefault(DISCOVERY_RESULTS, None)

        options = []

        if self.hass.data[DOMAIN][DISCOVERY_RESULTS] is not None:
            options.append(
                "process_scan_results"
            )  # directly jump to results of last scan

        options += ["scan_form", "add_manually"]

        return self.async_show_menu(
            step_id="user",
            menu_options=options,
        )

    async def async_step_scan_form(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_form(
            step_id="scan_start",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "scan_network", default="192.168.2.0/24"
                    ): TextSelector(),
                }
            ),
            errors={},
        )

    async def _monitor_progress(self):
        """Monitors the progress of the tasks and updates the progress bar."""
        while True:
            num_done_tasks = len([x for x in self._scan_tasks if x.done()])

            if num_done_tasks < len(self._scan_tasks):
                self.async_update_progress(
                    num_done_tasks / float(len(self._scan_tasks))
                )
            else:
                break

            # Wait for one second before the next update
            await asyncio.sleep(1)

        # Await all tasks to ensure they are fully completed
        await asyncio.gather(*self._scan_tasks)

    async def async_step_scan_start(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._scan_tasks is None:
            self._scan_network = ipaddress.IPv4Network(user_input["scan_network"])
            self._scan_tasks = [
                asyncio.create_task(x)
                for x in controller_autodetect.scan(self.hass, self._scan_network)
            ]

            self._scan_monitor_task = self.hass.async_create_task(
                self._monitor_progress()
            )

            return self.async_show_progress(
                progress_action="scanning",
                progress_task=self._scan_monitor_task,
            )
        else:
            return self.async_show_progress_done(next_step_id="process_scan_results")

    async def async_step_process_scan_results(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._scan_tasks is not None:
            scan_time = datetime.datetime.now(datetime.UTC)
            found_controllers = {
                t.result().host: t.result()
                for t in self._scan_tasks
                if t.result() is not None
            }

            if not found_controllers:
                self.hass.data[DOMAIN][DISCOVERY_RESULTS] = None
                return self.async_abort(
                    reason="scan_no_controllers",
                    description_placeholders={"network": str(self._scan_network)},
                )

            self.hass.data[DOMAIN][DISCOVERY_RESULTS] = DiscoveryResult(
                found_controllers, scan_time
            )

        controller_options = [
            {
                "label": f"{x.device_name} ({x.host})",
                "value": x.host,
            }
            for x in self.hass.data[DOMAIN][DISCOVERY_RESULTS].controllers.values()
        ]

        data_schema = vol.Schema(
            {
                vol.Required(CONF_NAME): str,
                vol.Required(CONF_HOST): selector(
                    {
                        "select": {
                            "options": controller_options,
                        }
                    }
                ),
            }
        )

        discovery_result = cast(
            DiscoveryResult, self.hass.data[DOMAIN][DISCOVERY_RESULTS]
        )
        return self.async_show_form(
            step_id="add_controller_from_scan",
            data_schema=data_schema,
            description_placeholders={
                "num_controllers": str(len(discovery_result.controllers)),
                "scan_time": dt_util.as_local(discovery_result.timestamp).strftime(
                    "%H:%M:%S"
                ),
            },
            errors={},
        )

    async def async_step_add_manually(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                host = user_input[CONF_HOST]
                return await self._create_entry_from_host(
                    host=host, title=user_input[CONF_NAME]
                )
            except _InvalidHostError:
                return self.async_show_form(
                    step_id="add_manually",
                    data_schema=vol.Schema(
                        {vol.Required(CONF_NAME): str, vol.Required(CONF_HOST): str}
                    ),
                    errors={CONF_HOST: "cannot_connect"},
                )

        return self.async_show_form(
            step_id="add_manually",
            data_schema=vol.Schema(
                {vol.Required(CONF_NAME): str, vol.Required(CONF_HOST): str}
            ),
            errors=errors,
        )

    async def _create_entry_from_controller(
        self, controller: RgbwwController, title: str
    ):
        return await self._create_entry(
            unique_id=controller.info["connection"]["mac"],
            title=title,
            data={CONF_HOST: controller.host, CONF_NAME: title},
            do_check=False,  # already checked during scan
        )

    async def _create_entry_from_host(self, host: str, title: str):
        controller = RgbwwController(self.hass, host)
        try:
            # just check if reachable
            await controller.refresh()
        except HTTPError:
            raise _InvalidHostError(host)

        return await self._create_entry(
            unique_id=controller.info["connection"]["mac"],
            title=title,
            host=host,
        )

    async def _create_entry(
        self, unique_id: str, title: str, host: str
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=title, data={CONF_HOST: host, CONF_NAME: title}
        )

    async def async_step_add_controller_from_scan(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (
            ctrl := self.hass.data[DOMAIN][DISCOVERY_RESULTS].controllers[
                user_input[CONF_HOST]
            ]
        ) is None:
            ctrl = RgbwwController(user_input[CONF_HOST])
            try:
                await ctrl.refresh()
            except HTTPError:
                self.async_abort(reason="cannot_connect")

        return await self._create_entry(
            unique_id=ctrl.info["connection"]["mac"],
            title=user_input[CONF_NAME],
            host=ctrl.host,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of the integration."""
        cur_data = self._get_reconfigure_entry().data
        errors: dict[str, str] = {}

        if user_input:
            host = user_input[CONF_HOST]
            controller = RgbwwController(self.hass, host)
            try:
                # just check if reachable
                await controller.refresh()
            except HTTPError:
                errors[CONF_HOST] = f"Cannot retrieve MAC address from host {host}"
                cur_data = user_input
            else:
                # to support the scenario that a physical device has been replaced by another device
                # we don't change the unique_id and we allow it to differ from the MAC
                # mac = info["connection"]["mac"]
                # await self.async_set_unique_id(mac)
                # self._abort_if_unique_id_mismatch(reason="wrong_account")

                return self.async_update_reload_and_abort(
                    self._get_reconfigure_entry(),
                    data_updates=user_input,
                    reason="Host changed successfully.",
                )
        else:
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=vol.Schema(
                    {
                        vol.Required(
                            CONF_HOST,
                            default=cur_data[CONF_HOST],
                        ): TextSelector(),
                    }
                ),
                errors=errors,
            )


OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required("show_things"): bool,
    }
)


class RgbwwFlowHandler(OptionsFlowWithReload):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="mqtt",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "mqtt.enabled",
                    ): bool,
                    vol.Required(
                        "mqtt.host",
                    ): str,
                }
            ),
            errors=errors,
        )
