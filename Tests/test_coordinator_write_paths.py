"""Focused coordinator tests for write verification and event fallback paths."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pykoplenti import ApiException
from aiohttp import ClientError

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

from pytest_homeassistant_custom_component.common import MockConfigEntry


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain="kostal_plenticore",
        title="coordinator-test",
        data={"host": "192.168.1.2", "password": "pw", "service_code": "12345"},
    )


class _DummyMixin:
    name = "dummy"

    def __init__(self, plenticore) -> None:
        self._plenticore = plenticore
        self.config_entry = plenticore.config_entry


async def test_async_write_data_verification_and_validation_paths(
    hass: HomeAssistant,
) -> None:
    """Write path covers strict verification, mismatches and validation errors."""
    from kostal_plenticore.coordinator import DataUpdateCoordinatorMixin, Plenticore

    class Dummy(DataUpdateCoordinatorMixin, _DummyMixin):
        pass

    entry = _entry()
    plenticore = Plenticore(hass, entry)
    mix = Dummy(plenticore)

    with patch("kostal_plenticore.coordinator.is_rest_write_supported_target", return_value=True), patch(
        "kostal_plenticore.coordinator.is_allowed_write_target",
        return_value=True,
    ), patch(
        "kostal_plenticore.coordinator.clear_issue"
    ), patch(
        "kostal_plenticore.coordinator.validate_cross_field_write_rules",
        return_value=None,
    ):
        # strict verify without readback capability
        plenticore._client = SimpleNamespace(set_setting_values=AsyncMock(return_value=None))
        plenticore.arm_advanced_writes()
        with patch(
            "kostal_plenticore.coordinator.requires_advanced_write_arm",
            return_value=True,
        ):
            with pytest.raises(HomeAssistantError, match="requires readback verification"):
                await mix.async_write_data("devices:local", {"Battery:Danger": "1"})

        # pre-read failure is tolerated, but cross-field validation may still stop the write
        client = MagicMock()
        client.get_setting_values = AsyncMock(side_effect=RuntimeError("pre-read boom"))
        client.set_setting_values = AsyncMock(return_value=None)
        plenticore._client = client
        with patch(
            "kostal_plenticore.coordinator.requires_advanced_write_arm",
            return_value=False,
        ), patch(
            "kostal_plenticore.coordinator.validate_cross_field_write_rules",
            return_value="cross-field mismatch",
        ):
            with pytest.raises(HomeAssistantError, match="cross-field mismatch"):
                await mix.async_write_data("devices:local", {"Battery:MinSoc": "10"})

        # strict verify: verification readback failure after successful write
        client = MagicMock()
        client.get_setting_values = AsyncMock(
            side_effect=[
                {"devices:local": {"Battery:Danger": "1"}},
                ApiException("verify failed"),
            ]
        )
        client.set_setting_values = AsyncMock(return_value=None)
        plenticore._client = client
        plenticore.arm_advanced_writes()
        with patch(
            "kostal_plenticore.coordinator.requires_advanced_write_arm",
            return_value=True,
        ):
            with pytest.raises(HomeAssistantError, match="verification read failed"):
                await mix.async_write_data("devices:local", {"Battery:Danger": "1"})

        # strict verify: mismatch in numeric readback
        client = MagicMock()
        client.get_setting_values = AsyncMock(
            side_effect=[
                {"devices:local": {"Battery:Danger": "1"}},
                {"devices:local": {"Battery:Danger": "0"}},
            ]
        )
        client.set_setting_values = AsyncMock(return_value=None)
        plenticore._client = client
        plenticore.arm_advanced_writes()
        with patch(
            "kostal_plenticore.coordinator.requires_advanced_write_arm",
            return_value=True,
        ):
            with pytest.raises(HomeAssistantError, match="Write verification failed"):
                await mix.async_write_data("devices:local", {"Battery:Danger": "1"})

        # non-strict verify: unexpected verification exception is downgraded
        client = MagicMock()
        client.get_setting_values = AsyncMock(
            side_effect=[
                {"devices:local": {"Battery:MinSoc": "10"}},
                RuntimeError("unexpected verify"),
            ]
        )
        client.set_setting_values = AsyncMock(return_value=None)
        plenticore._client = client
        with patch(
            "kostal_plenticore.coordinator.requires_advanced_write_arm",
            return_value=False,
        ):
            assert await mix.async_write_data("devices:local", {"Battery:MinSoc": "10"}) is True

        # non-strict verify: missing readback key and mismatches stay non-fatal
        client = MagicMock()
        client.get_setting_values = AsyncMock(
            side_effect=[
                {"devices:local": {"Battery:MinSoc": "10"}},
                {"devices:local": {"Other:Key": "0"}},
            ]
        )
        client.set_setting_values = AsyncMock(return_value=None)
        plenticore._client = client
        with patch(
            "kostal_plenticore.coordinator.requires_advanced_write_arm",
            return_value=False,
        ):
            assert await mix.async_write_data("devices:local", {"Battery:MinSoc": "10"}) is True


async def test_event_coordinator_fallback_paths(hass: HomeAssistant) -> None:
    """Event coordinator returns stable snapshots on empty/error/no-client paths."""
    from kostal_plenticore.coordinator import EventDataUpdateCoordinator, Plenticore

    entry = _entry()
    plenticore = Plenticore(hass, entry)
    with patch(
        "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
        return_value=None,
    ):
        coordinator = EventDataUpdateCoordinator.__new__(EventDataUpdateCoordinator)
        coordinator.hass = hass
        coordinator.logger = logging.getLogger(__name__)
        coordinator.name = "events"
        coordinator.data = None
        coordinator._listeners = {}
        coordinator._unsub_refresh = None
        coordinator.last_update_success = True
        coordinator.update_interval = timedelta(seconds=30)
        EventDataUpdateCoordinator.__init__(
            coordinator, hass, entry, coordinator.logger, coordinator.name,
            coordinator.update_interval, plenticore,
        )

    coordinator._last_result = {"cached": True}
    plenticore._client = None
    assert await coordinator._async_update_data() == {"cached": True}

    client = MagicMock()
    client.get_events = AsyncMock(return_value=[])
    plenticore._client = client
    result = await coordinator._async_update_data()
    assert result["fetched_count"] == 0
    assert result["last_event_code"] is None

    coordinator._last_result = {"cached": "busy"}
    client.get_events = AsyncMock(side_effect=ApiException("[503] internal communication error"))
    assert await coordinator._async_update_data() == {"cached": "busy"}

    coordinator._last_result = {"cached": "unexpected"}
    client.get_events = AsyncMock(side_effect=RuntimeError("boom"))
    assert await coordinator._async_update_data() == {"cached": "unexpected"}


async def test_plenticore_cache_helpers_and_write_edge_paths(
    hass: HomeAssistant,
) -> None:
    """Cover remaining cache, write-guard and verification edge paths."""
    from kostal_plenticore.coordinator import DataUpdateCoordinatorMixin, Plenticore

    class Dummy(DataUpdateCoordinatorMixin, _DummyMixin):
        pass

    entry = _entry()
    plenticore = Plenticore(hass, entry)

    assert await plenticore.async_get_settings_cached() == {}
    assert await plenticore.async_get_process_data_cached() == {}

    plenticore._client = MagicMock()
    plenticore._settings_cache = {"devices:local": ["cached"]}
    plenticore._settings_cache_ts = time.monotonic()
    plenticore._process_data_cache = {"devices:local": ["proc"]}
    plenticore._process_data_cache_ts = time.monotonic()
    assert await plenticore.async_get_settings_cached(ttl_seconds=999) == {"devices:local": ["cached"]}
    assert await plenticore.async_get_process_data_cached(ttl_seconds=999) == {"devices:local": ["proc"]}

    plenticore.invalidate_capability_cache()
    assert plenticore._settings_cache is None
    assert plenticore._settings_cache_ts == 0.0
    assert plenticore._process_data_cache is None
    assert plenticore._process_data_cache_ts == 0.0

    plenticore._client = MagicMock()
    plenticore._shutdown_remove_listener = lambda: None
    plenticore._client.logout = AsyncMock(side_effect=ClientError("logout failed"))
    await plenticore.async_unload()
    assert plenticore._client is None

    setup_plenticore = Plenticore(hass, entry)
    with patch("kostal_plenticore.coordinator.ExtendedApiClient") as client_cls:
        client = MagicMock()
        client.login = AsyncMock(return_value=None)
        client_cls.return_value = client
        setup_plenticore._fetch_modules = AsyncMock(return_value=None)
        setup_plenticore._fetch_device_metadata = AsyncMock(return_value=None)
        setup_plenticore.async_get_process_data_cached = AsyncMock(side_effect=RuntimeError("prewarm fail"))
        setup_plenticore.async_get_settings_cached = AsyncMock(return_value={})
        assert await setup_plenticore.async_setup() is True

    mix = Dummy(setup_plenticore)
    setup_plenticore._client = MagicMock()
    setup_plenticore.hass = None
    setup_plenticore._client.get_setting_values = AsyncMock(
        return_value={"devices:local": {"Key": "1"}}
    )
    assert await mix.async_read_data("devices:local", "Key") == {"devices:local": {"Key": "1"}}

    with patch("kostal_plenticore.coordinator.is_rest_write_supported_target", return_value=False):
        with pytest.raises(HomeAssistantError, match="REST write disabled"):
            await mix.async_write_data("devices:local", {"Blocked:Key": "1"})

    with (
        patch("kostal_plenticore.coordinator.is_rest_write_supported_target", return_value=True),
        patch("kostal_plenticore.coordinator.is_allowed_write_target", return_value=True),
        patch("kostal_plenticore.coordinator.validate_cross_field_write_rules", return_value=None),
        patch("kostal_plenticore.coordinator.requires_advanced_write_arm", return_value=True),
    ):
        setup_plenticore._client.get_setting_values = AsyncMock(return_value={"devices:local": {}})
        setup_plenticore._client.set_setting_values = AsyncMock(return_value=None)
        with pytest.raises(HomeAssistantError, match="Arm advanced writes first"):
            await mix.async_write_data("devices:local", {"Battery:Danger": "1"})

    with (
        patch("kostal_plenticore.coordinator.is_rest_write_supported_target", return_value=True),
        patch("kostal_plenticore.coordinator.is_allowed_write_target", return_value=True),
        patch("kostal_plenticore.coordinator.validate_cross_field_write_rules", return_value=None),
        patch("kostal_plenticore.coordinator.requires_advanced_write_arm", return_value=False),
        patch("kostal_plenticore.coordinator.clear_issue"),
    ):
        client = MagicMock()
        client.get_setting_values = AsyncMock(
            side_effect=[
                {"devices:local": {"FooOnPowerThreshold": "10", "FooOffPowerThreshold": "5"}},
                {"devices:local": {"FooOnPowerThreshold": "mismatch"}},
            ]
        )
        client.set_setting_values = AsyncMock(return_value=None)
        setup_plenticore._client = client
        assert await mix.async_write_data("devices:local", {"FooOnPowerThreshold": "10"}) is True
        first_fetch = client.get_setting_values.await_args_list[0].args[0]["devices:local"]
        assert "FooOffPowerThreshold" in first_fetch

        client = MagicMock()
        client.get_setting_values = AsyncMock(
            side_effect=[
                {"devices:local": {"FooOnPowerThreshold": "10", "FooOffPowerThreshold": "5"}},
                {"devices:local": {"FooOffPowerThreshold": "5"}},
            ]
        )
        client.set_setting_values = AsyncMock(return_value=None)
        setup_plenticore._client = client
        assert await mix.async_write_data("devices:local", {"FooOffPowerThreshold": "5"}) is True
        first_fetch = client.get_setting_values.await_args_list[0].args[0]["devices:local"]
        assert "FooOnPowerThreshold" in first_fetch

        client = MagicMock()
        client.get_setting_values = AsyncMock(
            side_effect=[
                {"devices:local": {"Battery:Label": "same"}},
                {"devices:local": {"Battery:Label": "same"}},
            ]
        )
        client.set_setting_values = AsyncMock(return_value=None)
        setup_plenticore._client = client
        assert await mix.async_write_data("devices:local", {"Battery:Label": "same"}) is True

        client = MagicMock()
        client.get_setting_values = AsyncMock(
            side_effect=[
                {"devices:local": {"Battery:MinSoc": "10"}},
                ApiException("verify failed"),
            ]
        )
        client.set_setting_values = AsyncMock(return_value=None)
        setup_plenticore._client = client
        assert await mix.async_write_data("devices:local", {"Battery:MinSoc": "10"}) is True

        import kostal_plenticore.coordinator as coordinator_module

        from kostal_plenticore.coordinator import (
            ModbusIllegalDataAddressError,
            ModbusIllegalDataValueError,
            ModbusServerDeviceFailureError,
        )

        for translated_err in (
            ModbusIllegalDataValueError(),
            ModbusIllegalDataAddressError(),
        ):
            client = MagicMock()
            client.get_setting_values = AsyncMock(return_value={"devices:local": {}})
            client.set_setting_values = AsyncMock(side_effect=ApiException("illegal"))
            setup_plenticore._client = client
            with patch.object(
                coordinator_module,
                "parse_modbus_exception",
                return_value=translated_err,
            ):
                with pytest.raises(HomeAssistantError):
                    await mix.async_write_data("devices:local", {"Battery:MinSoc": "10"})

        client = MagicMock()
        client.get_setting_values = AsyncMock(return_value={"devices:local": {}})
        client.set_setting_values = AsyncMock(side_effect=ApiException("server device failure"))
        setup_plenticore._client = client
        with patch.object(
            coordinator_module,
            "parse_modbus_exception",
            return_value=ModbusServerDeviceFailureError(),
        ):
            with pytest.raises(HomeAssistantError):
                await mix.async_write_data("devices:local", {"Battery:MinSoc": "10"})

        for err in (
            ApiException("server device busy"),
            ClientError("Unknown API response [500]"),
            ClientError("generic client failure"),
        ):
            client = MagicMock()
            client.get_setting_values = AsyncMock(return_value={"devices:local": {}})
            client.set_setting_values = AsyncMock(side_effect=err)
            setup_plenticore._client = client
            with pytest.raises(HomeAssistantError):
                await mix.async_write_data("devices:local", {"Battery:MinSoc": "10"})

    with (
        patch("kostal_plenticore.coordinator.is_rest_write_supported_target", return_value=True),
        patch("kostal_plenticore.coordinator.is_allowed_write_target", return_value=True),
        patch("kostal_plenticore.coordinator.validate_cross_field_write_rules", return_value=None),
        patch("kostal_plenticore.coordinator.requires_advanced_write_arm", return_value=True),
    ):
        client = MagicMock()
        client.get_setting_values = AsyncMock(
            side_effect=[
                {"devices:local": {"Battery:Danger": "1"}},
                RuntimeError("verify exploded"),
            ]
        )
        client.set_setting_values = AsyncMock(return_value=None)
        setup_plenticore._client = client
        setup_plenticore.arm_advanced_writes()
        with pytest.raises(HomeAssistantError, match="setting may already be applied"):
            await mix.async_write_data("devices:local", {"Battery:Danger": "1"})


async def test_process_setting_event_and_select_remaining_edges(
    hass: HomeAssistant,
) -> None:
    """Cover remaining coordinator fallback and lifecycle branches."""
    from kostal_plenticore.coordinator import (
        EventDataUpdateCoordinator,
        Plenticore,
        ProcessDataUpdateCoordinator,
        SelectDataUpdateCoordinator,
        SettingDataUpdateCoordinator,
    )

    entry = _entry()
    plenticore = Plenticore(hass, entry)
    plenticore._client = MagicMock()
    plenticore.hass = None

    with patch(
        "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
        return_value=None,
    ):
        proc = ProcessDataUpdateCoordinator.__new__(ProcessDataUpdateCoordinator)
        proc.hass = hass
        proc.logger = logging.getLogger(__name__)
        proc.name = "proc-edge"
        proc.data = None
        proc._listeners = {}
        proc._unsub_refresh = None
        proc.last_update_success = True
        proc.update_interval = timedelta(seconds=10)
        ProcessDataUpdateCoordinator.__init__(
            proc, hass, entry, proc.logger, proc.name, proc.update_interval, plenticore,
        )
    proc._fetch = {"devices:local": ["P"]}
    plenticore._client.get_process_data_values = AsyncMock(
        return_value={"devices:local": {"P": SimpleNamespace(value="1")}}
    )
    assert await proc._async_update_data() == {"devices:local": {"P": "1"}}

    proc.async_request_refresh = AsyncMock(side_effect=RuntimeError("refresh boom"))
    proc.start_fetch_data("devices:local", "P2")
    await hass.async_block_till_done()

    proc._fetch = {}
    proc.stop_fetch_data("missing:module", "missing:data")

    proc._last_result = {"cached": "generic"}
    proc._fetch = {"devices:local": ["P"]}
    plenticore._client.get_process_data_values = AsyncMock(side_effect=ClientError("generic process failure"))
    assert await proc._async_update_data() == {"cached": "generic"}

    proc._last_result = {"cached": "503"}
    proc._fetch = {"devices:local": ["P"]}
    plenticore._client.get_process_data_values = AsyncMock(
        side_effect=ClientError("[503] internal communication error")
    )
    assert await proc._async_update_data() == {"cached": "503"}

    with patch(
        "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
        return_value=None,
    ):
        settings = SettingDataUpdateCoordinator.__new__(SettingDataUpdateCoordinator)
        settings.hass = hass
        settings.logger = logging.getLogger(__name__)
        settings.name = "settings-edge"
        settings.data = None
        settings._listeners = {}
        settings._unsub_refresh = None
        settings.last_update_success = True
        settings.update_interval = timedelta(seconds=10)
        SettingDataUpdateCoordinator.__init__(
            settings, hass, entry, settings.logger, settings.name, settings.update_interval, plenticore,
        )
    settings._fetch = {"devices:local": ["Battery:MinSoc"]}
    plenticore._client.get_setting_values = AsyncMock(
        return_value={"devices:local": {"Battery:MinSoc": "8"}}
    )
    assert await settings._async_update_data() == {"devices:local": {"Battery:MinSoc": "8"}}

    with patch(
        "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
        return_value=None,
    ):
        events = EventDataUpdateCoordinator.__new__(EventDataUpdateCoordinator)
        events.hass = hass
        events.logger = logging.getLogger(__name__)
        events.name = "events-edge"
        events.data = None
        events._listeners = {}
        events._unsub_refresh = None
        events.last_update_success = True
        events.update_interval = timedelta(seconds=10)
        EventDataUpdateCoordinator.__init__(
            events, hass, entry, events.logger, events.name, events.update_interval, plenticore,
        )
    # Stale TTL fix (HIGH-06): cache return requires a recent _last_success_ts.
    import time as _time_mod
    events._last_result = {"cached": "api"}
    events._last_success_ts = _time_mod.monotonic()
    plenticore._client.get_events = AsyncMock(side_effect=ApiException("illegal function"))
    assert await events._async_update_data() == {"cached": "api"}
    events._last_result = {"cached": "client"}
    events._last_success_ts = _time_mod.monotonic()
    plenticore._client.get_events = AsyncMock(side_effect=ClientError("client boom"))
    assert await events._async_update_data() == {"cached": "client"}

    with patch(
        "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.__init__",
        return_value=None,
    ):
        select = SelectDataUpdateCoordinator.__new__(SelectDataUpdateCoordinator)
        select.hass = hass
        select.logger = logging.getLogger(__name__)
        select.name = "select-edge"
        select.data = None
        select._listeners = {}
        select._unsub_refresh = None
        select.last_update_success = True
        select.update_interval = timedelta(seconds=10)
        SelectDataUpdateCoordinator.__init__(
            select, hass, entry, select.logger, select.name, select.update_interval, plenticore,
        )
    select.stop_fetch_data("missing:module", "Mode", ["A", "None"])
    # Batch-read coordinator queries client.get_setting_values({mid: [ids]}) once
    # per module; return "A"=1 only for the "filled" module so the empty module
    # path is also exercised.
    plenticore._client.get_setting_values = AsyncMock(
        return_value={"devices:local:filled": {"A": "1"}}
    )
    result = await select._async_get_current_option(
        {
            "devices:local:empty": {},
            "devices:local:filled": {"Mode": ["A", "None"]},
        }
    )
    assert result == {"devices:local:filled": {"Mode": "A"}}
