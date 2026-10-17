"""A removed polling source cannot leave or resurrect an orphaned outage."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant, is_callback
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.kostal_kore.communication import CommunicationState
from custom_components.kostal_kore.coordinator import Plenticore, SettingDataUpdateCoordinator
from Tests.test_third_review_communication import coordinator


def owner_for(hass):
    owner = object.__new__(Plenticore)
    owner.hass, owner.config_entry = hass, SimpleNamespace(entry_id="lifecycle")
    owner.rest_communication, owner._rest_poll_health = CommunicationState(), {}
    owner._communication_store = SimpleNamespace(async_save=AsyncMock())
    owner._client = SimpleNamespace(get_setting_values=AsyncMock(side_effect=TimeoutError()))
    return owner


async def drain():
    # Both a scheduled callback and its task must be allowed to run.
    for _ in range(4):
        await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("other_failed", [False, True])
async def test_last_fetch_removal_unregisters_only_its_own_health(tmp_path, other_failed):
    hass = HomeAssistant(str(tmp_path))
    owner = owner_for(hass)
    item = coordinator(SettingDataUpdateCoordinator, hass, owner)
    item._fetch["battery"] = ["limit"]
    with patch("homeassistant.components.persistent_notification.async_create"):
        await owner.async_report_rest_communication(not other_failed)
        with pytest.raises(UpdateFailed):
            await item._async_update_data()
        source = f"settings:{id(item)}"
        assert owner._rest_poll_health[source] is False
        item.stop_fetch_data("battery", "limit")
        await drain()
        assert source not in owner._rest_poll_health
        assert bool(owner.rest_communication.faults) is other_failed
        assert owner._rest_poll_health == {"process": not other_failed}


@pytest.mark.asyncio
@pytest.mark.parametrize("remove_first", [False, True])
async def test_context_and_explicit_removal_work_in_either_order(tmp_path, remove_first):
    hass = HomeAssistant(str(tmp_path))
    owner = owner_for(hass)
    item = coordinator(SettingDataUpdateCoordinator, hass, owner)
    item._fetch["battery"] = ["limit"]
    with patch.object(item, "_schedule_refresh"):
        remove = item.async_add_listener(lambda: None, ("battery", "limit"))
    assert is_callback(remove)
    with patch("homeassistant.components.persistent_notification.async_create"):
        with pytest.raises(UpdateFailed):
            await item._async_update_data()
        first, last = ((remove, lambda: item.stop_fetch_data("battery", "limit")) if remove_first else
                       (lambda: item.stop_fetch_data("battery", "limit"), remove))
        first()
        await drain()
        assert owner.rest_communication.faults
        last()
        await drain()
        assert owner._rest_poll_health == {}
        assert not owner.rest_communication.faults


@pytest.mark.asyncio
async def test_context_only_removal_releases_the_source(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    owner = owner_for(hass)
    item = coordinator(SettingDataUpdateCoordinator, hass, owner)
    with patch.object(item, "_schedule_refresh"):
        remove = item.async_add_listener(lambda: None, ("battery", "limit"))
    with patch("homeassistant.components.persistent_notification.async_create"):
        with pytest.raises(UpdateFailed):
            await item._async_update_data()
        remove()
        await drain()
        assert owner._rest_poll_health == {}


@pytest.mark.asyncio
async def test_queued_cleanup_rechecks_new_demand(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    owner = owner_for(hass)
    item = coordinator(SettingDataUpdateCoordinator, hass, owner)
    item._fetch["battery"] = ["limit"]
    with patch("homeassistant.components.persistent_notification.async_create"):
        with pytest.raises(UpdateFailed):
            await item._async_update_data()
        item.stop_fetch_data("battery", "limit")
        item._fetch["battery"] = ["another_limit"]
        await drain()
        assert owner._rest_poll_health[f"settings:{id(item)}"] is False
        assert owner.rest_communication.faults


@pytest.mark.asyncio
@pytest.mark.parametrize("shutdown", [False, True])
async def test_late_failure_cannot_resurrect_a_removed_source(tmp_path, shutdown):
    hass = HomeAssistant(str(tmp_path))
    owner = owner_for(hass)
    item = coordinator(SettingDataUpdateCoordinator, hass, owner)
    item._fetch["battery"] = ["limit"]
    started, finish = asyncio.Event(), asyncio.Event()

    async def fail_later(*args):
        started.set()
        await finish.wait()
        raise TimeoutError()

    with patch("homeassistant.components.persistent_notification.async_create"):
        await owner.async_report_rest_communication(False, source=f"settings:{id(item)}")
        owner._client.get_setting_values.side_effect = fail_later
        task = asyncio.create_task(item._async_update_data())
        await started.wait()
        if shutdown:
            await item.async_shutdown()
        else:
            item.stop_fetch_data("battery", "limit")
            await drain()
        assert owner._rest_poll_health == {}
        finish.set()
        with pytest.raises(UpdateFailed):
            await task
        assert owner._rest_poll_health == {}
        assert not owner.rest_communication.faults
