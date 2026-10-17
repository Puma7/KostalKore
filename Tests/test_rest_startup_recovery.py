"""A persisted outage survives partial startup until all real readers agree."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.kostal_kore.communication import CommunicationState
from custom_components.kostal_kore.coordinator import (
    Plenticore,
    ProcessDataUpdateCoordinator,
    SettingDataUpdateCoordinator,
)
from Tests.test_third_review_communication import coordinator


def restored_owner(hass):
    entry = SimpleNamespace(entry_id="startup", data={"host": "127.0.0.1"})
    owner = Plenticore(hass, entry)
    owner._communication_store = SimpleNamespace(async_save=AsyncMock())
    previous = CommunicationState()
    previous.observe(["rest"], dt_util.now())
    owner.rest_communication = CommunicationState(previous.snapshot())
    return owner


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_index", [0, 1, 2])
async def test_restored_fault_waits_for_discovery_and_every_active_channel(tmp_path, failed_index):
    hass = HomeAssistant(str(tmp_path))
    owner = restored_owner(hass)
    process = coordinator(ProcessDataUpdateCoordinator, hass, owner)
    process._fetch["battery"] = ["SoC"]
    with patch("homeassistant.components.persistent_notification.async_create") as notice:
        # The process platform can poll before number and switch even exist.
        await owner.async_report_rest_communication(True)
        assert owner.rest_communication.faults == ("rest",)
        number, switch = (coordinator(SettingDataUpdateCoordinator, hass, owner) for _ in range(2))
        channels = [process, number, switch]
        for item in channels:
            item._fetch["battery"] = ["limit"]
        await owner.async_finish_rest_setup()
        for index, item in enumerate(channels):
            await owner.async_report_rest_communication(index != failed_index, source=item.rest_communication_source)
        assert owner.rest_communication.faults == ("rest",)
        assert owner.rest_communication.incidents == 1
        assert notice.call_count == 1  # Reannounce the restored outage, never recovery.
        assert "wiederhergestellt" not in notice.call_args.args[1]
        await owner.async_report_rest_communication(True, source=channels[failed_index].rest_communication_source)
        assert owner.rest_communication.faults == ()
        assert owner.rest_communication.incidents == 1
        assert notice.call_count == 2


@pytest.mark.asyncio
async def test_disabled_platform_does_not_block_a_tested_active_reader(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    owner = restored_owner(hass)
    active = coordinator(ProcessDataUpdateCoordinator, hass, owner)
    inactive = coordinator(SettingDataUpdateCoordinator, hass, owner)
    active._fetch["battery"] = ["SoC"]
    with patch("homeassistant.components.persistent_notification.async_create") as notice:
        await owner.async_report_rest_communication(True)
        # An early empty refresh cannot retire a not-yet-discovered platform.
        await inactive._async_update_data()
        assert owner._rest_poll_health[inactive.rest_communication_source] is None
        await owner.async_finish_rest_setup()
        assert owner._rest_poll_health == {"process": True}
        assert owner.rest_communication.faults == ()
        assert notice.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("restart", ["homeassistant", "integration"])
async def test_shutdown_cleanup_cannot_persist_false_recovery(tmp_path, restart):
    hass = HomeAssistant(str(tmp_path))
    owner = restored_owner(hass)
    item = coordinator(ProcessDataUpdateCoordinator, hass, owner)
    owner._rest_setup_complete = True
    if restart == "homeassistant":
        hass.set_state(CoreState.stopping)
    with patch("homeassistant.components.persistent_notification.async_create") as notice:
        if restart == "homeassistant":
            await item.async_shutdown()
        else:
            await owner.async_unload()
        assert owner._rest_poll_health == {}
        assert owner.rest_communication.faults == ("rest",)
        notice.assert_not_called()
        owner._communication_store.async_save.assert_not_awaited()
