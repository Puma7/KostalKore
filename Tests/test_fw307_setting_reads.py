"""Plenticore G3 FW 3.07.03.27419: single-setting GET reads return 404.

Only the {module: [ids]} form (POST /settings) still works, and the hostname
arrives JSON-quoted. See home-assistant/core#186638, #187116 and #187002.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pykoplenti import ModuleNotFoundException

from custom_components.kostal_kore import config_flow
from custom_components.kostal_kore.const import CONF_HOST, CONF_PASSWORD
from custom_components.kostal_kore.helper import normalize_hostname

PACKAGE = Path(__file__).resolve().parent.parent / "custom_components" / "kostal_kore"


def test_all_setting_reads_use_the_dict_form() -> None:
    """Every get_setting_values call passes exactly one {module: ids} mapping."""
    offenders: list[str] = []
    calls = 0
    for path in sorted(PACKAGE.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get_setting_values"):
                calls += 1
                if node.keywords or len(node.args) != 1 or isinstance(
                    node.args[0], (ast.Constant, ast.JoinedStr)
                ):
                    offenders.append(f"{path.name}:{node.lineno}")
    assert calls > 5
    assert offenders == []


@pytest.mark.parametrize(("raw", "expected"), [
    ("scb", "scb"),
    ('"scb"', "scb"),
    ('  " scb-g3 "  ', "scb-g3"),
    ('"broken"json"', '"broken"json"'),
    ('""', "192.0.2.1"),
    ("", "192.0.2.1"),
    (None, "192.0.2.1"),
])
def test_normalize_hostname(raw, expected) -> None:
    assert normalize_hostname(raw, "192.0.2.1") == expected


class _Fw307Client:
    """Fake ApiClient with the FW 3.07.03 single-setting behaviour."""

    def __init__(self, *_args, **_kwargs) -> None:
        self.requests: list[object] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def login(self, _password, service_code=None):
        return None

    async def get_setting_values(self, module_id, setting_id=None):
        self.requests.append((module_id, setting_id))
        if isinstance(module_id, str) and isinstance(setting_id, str):
            raise ModuleNotFoundException(404, "Module or setting not found")
        assert isinstance(module_id, dict) and setting_id is None
        return {module: {sid: '"scb"' for sid in ids} for module, ids in module_id.items()}

    async def get_me(self):
        return SimpleNamespace(role="USER")


async def test_config_flow_connects_on_fw_307(hass: HomeAssistant) -> None:
    with patch.object(config_flow, "ApiClient", _Fw307Client), patch.object(
        config_flow, "get_hostname_id", AsyncMock(return_value="Network:Hostname"),
    ):
        result = await config_flow.test_connection_safe(
            hass, {CONF_HOST: "192.0.2.1", CONF_PASSWORD: "pw"}
        )
    assert result.hostname == "scb"


async def test_missing_process_value_keyerror_is_transient(hass: HomeAssistant) -> None:
    """A KeyError from pykoplenti's virtual PV sum uses the stale cache or fails cleanly."""
    import logging
    import time
    from datetime import timedelta
    from unittest.mock import MagicMock

    from homeassistant.helpers.update_coordinator import UpdateFailed

    from custom_components.kostal_kore.coordinator import ProcessDataUpdateCoordinator

    plenticore = MagicMock()
    plenticore.client.get_process_data_values = AsyncMock(side_effect=KeyError("P"))
    coordinator = ProcessDataUpdateCoordinator(
        hass, MagicMock(), logging.getLogger(__name__), "Process Data",
        timedelta(seconds=10), plenticore,
    )
    coordinator._fetch = {"_virt_": {"pv_P"}}

    with pytest.raises(UpdateFailed, match="'P'"):
        await coordinator._async_fetch_process_data()

    coordinator._last_result = {"_virt_": {"pv_P": "0.0"}}
    coordinator._last_success_ts = time.monotonic()
    assert await coordinator._async_fetch_process_data() == {"_virt_": {"pv_P": "0.0"}}
