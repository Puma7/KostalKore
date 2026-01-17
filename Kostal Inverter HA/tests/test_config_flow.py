"""Tests for the Kostal Plenticore config flow.

This test suite provides comprehensive coverage for the config flow,
including unit tests for the configuration steps, error handling, and
integration tests for the complete setup process.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.config_entries import ConfigEntry
from homeassistant.data_entry_flow import FlowResultType

from pykoplenti import ApiException, AuthenticationException

from .common import MockPlenticoreClient
from ..config_flow import KostalPlenticoreConfigFlow, test_connection
from ..const import DOMAIN, CONF_SERVICE_CODE


class TestTestConnection:
    """Test the test_connection function."""
    
    @pytest.fixture
    def mock_hass(self) -> HomeAssistant:
        """Create a mock Home Assistant instance."""
        hass = MagicMock()
        return hass
    
    @pytest.fixture
    def mock_client(self) -> MockPlenticoreClient:
        """Create a mock client."""
        return MockPlenticoreClient()
    
    @pytest.fixture
    def connection_data(self) -> dict:
        """Create connection data."""
        return {
            CONF_HOST: "192.168.1.100",
            CONF_PASSWORD: "test_password",
            CONF_SERVICE_CODE: "12345",
        }
    
    @pytest.mark.asyncio
    async def test_test_connection_success(
        self, mock_hass: HomeAssistant, mock_client: MockPlenticoreClient, connection_data: dict
    ) -> None:
        """Test successful connection test."""
        mock_client.set_settings_data({
            "scb:network": {
                "Network:Hostname": "test-inverter",
            },
        })
        
        with patch("pykoplenti.ApiClient", return_value=mock_client), \
             patch("kostal_plenticore.helper.get_hostname_id", return_value="Network:Hostname"):
            
            result = await test_connection(mock_hass, connection_data)
            
            assert result == "test-inverter"
            mock_client.login.assert_called_once_with("test_password", service_code="12345")
            mock_client.get_setting_values.assert_called_once_with("scb:network", "Network:Hostname")
    
    @pytest.mark.asyncio
    async def test_test_connection_auth_failure(
        self, mock_hass: HomeAssistant, mock_client: MockPlenticoreClient, connection_data: dict
    ) -> None:
        """Test connection test with authentication failure."""
        mock_client.set_should_fail_login(True)
        
        with patch("pykoplenti.ApiClient", return_value=mock_client):
            
            with pytest.raises(AuthenticationException):
                await test_connection(mock_hass, connection_data)
    
    @pytest.mark.asyncio
    async def test_test_connection_network_error(
        self, mock_hass: HomeAssistant, mock_client: MockPlenticoreClient, connection_data: dict
    ) -> None:
        """Test connection test with network error."""
        mock_client.set_timeout(True)
        
        with patch("pykoplenti.ApiClient", return_value=mock_client):
            
            with pytest.raises(Exception):  # Should raise some network error
                await test_connection(mock_hass, connection_data)
    
    @pytest.mark.asyncio
    async def test_test_connection_api_error(
        self, mock_hass: HomeAssistant, mock_client: MockPlenticoreClient, connection_data: dict
    ) -> None:
        """Test connection test with API error."""
        mock_client.set_should_fail_settings(True)
        
        with patch("pykoplenti.ApiClient", return_value=mock_client), \
             patch("kostal_plenticore.helper.get_hostname_id", return_value="Network:Hostname"):
            
            with pytest.raises(ApiException):
                await test_connection(mock_hass, connection_data)
    
    @pytest.mark.asyncio
    async def test_test_connection_no_service_code(
        self, mock_hass: HomeAssistant, mock_client: MockPlenticoreClient
    ) -> None:
        """Test connection test without service code."""
        connection_data = {
            CONF_HOST: "192.168.1.100",
            CONF_PASSWORD: "test_password",
        }
        
        mock_client.set_settings_data({
            "scb:network": {
                "Network:Hostname": "test-inverter",
            },
        })
        
        with patch("pykoplenti.ApiClient", return_value=mock_client), \
             patch("kostal_plenticore.helper.get_hostname_id", return_value="Network:Hostname"):
            
            result = await test_connection(mock_hass, connection_data)
            
            assert result == "test-inverter"
            mock_client.login.assert_called_once_with("test_password", service_code=None)


class TestKostalPlenticoreConfigFlow:
    """Test the KostalPlenticoreConfigFlow class."""
    
    @pytest.fixture
    def mock_hass(self) -> HomeAssistant:
        """Create a mock Home Assistant instance."""
        hass = MagicMock()
        return hass
    
    @pytest.fixture
    def config_flow(self, mock_hass: HomeAssistant) -> KostalPlenticoreConfigFlow:
        """Create a config flow instance."""
        flow = KostalPlenticoreConfigFlow()
        flow.hass = mock_hass
        return flow
    
    def test_config_flow_version(self, config_flow: KostalPlenticoreConfigFlow) -> None:
        """Test config flow version."""
        assert config_flow.VERSION == 1
    
    def test_config_flow_domain(self, config_flow: KostalPlenticoreConfigFlow) -> None:
        """Test config flow domain."""
        assert config_flow.domain == DOMAIN
    
    @pytest.mark.asyncio
    async def test_async_step_user_no_input(self, config_flow: KostalPlenticoreConfigFlow) -> None:
        """Test user step with no input."""
        result = await config_flow.async_step_user(None)
        
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"
        assert "data_schema" in result
        assert "errors" in result
    
    @pytest.mark.asyncio
    async def test_async_step_user_success(
        self, config_flow: KostalPlenticoreConfigFlow, mock_hass: HomeAssistant
    ) -> None:
        """Test user step with successful connection."""
        user_input = {
            CONF_HOST: "192.168.1.100",
            CONF_PASSWORD: "test_password",
            CONF_SERVICE_CODE: "12345",
        }
        
        # Mock the connection test
        mock_client = MockPlenticoreClient()
        mock_client.set_settings_data({
            "scb:network": {
                "Network:Hostname": "test-inverter",
            },
        })
        
        with patch("pykoplenti.ApiClient", return_value=mock_client), \
             patch("kostal_plenticore.helper.get_hostname_id", return_value="Network:Hostname"), \
             patch.object(config_flow, "_async_abort_entries_match"):
            
            result = await config_flow.async_step_user(user_input)
            
            assert result["type"] == FlowResultType.CREATE_ENTRY
            assert result["title"] == "test-inverter"
            assert result["data"] == user_input
    
    @pytest.mark.asyncio
    async def test_async_step_user_auth_failure(
        self, config_flow: KostalPlenticoreConfigFlow, mock_hass: HomeAssistant
    ) -> None:
        """Test user step with authentication failure."""
        user_input = {
            CONF_HOST: "192.168.1.100",
            CONF_PASSWORD: "wrong_password",
            CONF_SERVICE_CODE: "12345",
        }
        
        # Mock the connection test to fail
        mock_client = MockPlenticoreClient()
        mock_client.set_should_fail_login(True)
        
        with patch("pykoplenti.ApiClient", return_value=mock_client), \
             patch.object(config_flow, "_async_abort_entries_match"):
            
            result = await config_flow.async_step_user(user_input)
            
            assert result["type"] == FlowResultType.FORM
            assert result["step_id"] == "user"
            assert result["errors"][CONF_PASSWORD] == "invalid_auth"
    
    @pytest.mark.asyncio
    async def test_async_step_user_network_error(
        self, config_flow: KostalPlenticoreConfigFlow, mock_hass: HomeAssistant
    ) -> None:
        """Test user step with network error."""
        user_input = {
            CONF_HOST: "192.168.1.100",
            CONF_PASSWORD: "test_password",
            CONF_SERVICE_CODE: "12345",
        }
        
        # Mock the connection test to fail
        mock_client = MockPlenticoreClient()
        mock_client.set_timeout(True)
        
        with patch("pykoplenti.ApiClient", return_value=mock_client), \
             patch.object(config_flow, "_async_abort_entries_match"):
            
            result = await config_flow.async_step_user(user_input)
            
            assert result["type"] == FlowResultType.FORM
            assert result["step_id"] == "user"
            assert result["errors"][CONF_HOST] == "cannot_connect"
    
    @pytest.mark.asyncio
    async def test_async_step_user_api_error(
        self, config_flow: KostalPlenticoreConfigFlow, mock_hass: HomeAssistant
    ) -> None:
        """Test user step with API error."""
        user_input = {
            CONF_HOST: "192.168.1.100",
            CONF_PASSWORD: "test_password",
            CONF_SERVICE_CODE: "12345",
        }
        
        # Mock the connection test to fail
        mock_client = MockPlenticoreClient()
        mock_client.set_should_fail_settings(True)
        
        with patch("pykoplenti.ApiClient", return_value=mock_client), \
             patch.object(config_flow, "_async_abort_entries_match"):
            
            result = await config_flow.async_step_user(user_input)
            
            assert result["type"] == FlowResultType.FORM
            assert result["step_id"] == "user"
            assert result["errors"][CONF_HOST] == "cannot_connect"
    
    @pytest.mark.asyncio
    async def test_async_step_user_unknown_error(
        self, config_flow: KostalPlenticoreConfigFlow, mock_hass: HomeAssistant
    ) -> None:
        """Test user step with unknown error."""
        user_input = {
            CONF_HOST: "192.168.1.100",
            CONF_PASSWORD: "test_password",
            CONF_SERVICE_CODE: "12345",
        }
        
        # Mock the connection test to raise an unexpected error
        with patch("kostal_plenticore.config_flow.test_connection", side_effect=Exception("Unexpected error")), \
             patch.object(config_flow, "_async_abort_entries_match"):
            
            result = await config_flow.async_step_user(user_input)
            
            assert result["type"] == FlowResultType.FORM
            assert result["step_id"] == "user"
            assert result["errors"]["base"] == "unknown"
    
    @pytest.mark.asyncio
    async def test_async_step_user_already_configured(
        self, config_flow: KostalPlenticoreConfigFlow, mock_hass: HomeAssistant
    ) -> None:
        """Test user step with already configured device."""
        user_input = {
            CONF_HOST: "192.168.1.100",
            CONF_PASSWORD: "test_password",
            CONF_SERVICE_CODE: "12345",
        }
        
        # Mock the connection test to succeed
        mock_client = MockPlenticoreClient()
        mock_client.set_settings_data({
            "scb:network": {
                "Network:Hostname": "test-inverter",
            },
        })
        
        with patch("pykoplenti.ApiClient", return_value=mock_client), \
             patch("kostal_plenticore.helper.get_hostname_id", return_value="Network:Hostname"), \
             patch.object(config_flow, "_async_abort_entries_match", side_effect=Exception("Already configured")):
            
            result = await config_flow.async_step_user(user_input)
            
            # Should handle the already configured case
            assert result["type"] in [FlowResultType.ABORT, FlowResultType.FORM]
    
    @pytest.mark.asyncio
    async def test_async_step_reconfigure_no_input(self, config_flow: KostalPlenticoreConfigFlow) -> None:
        """Test reconfigure step with no input."""
        result = await config_flow.async_step_reconfigure(None)
        
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reconfigure"
        assert "data_schema" in result
        assert "errors" in result
    
    @pytest.mark.asyncio
    async def test_async_step_reconfigure_success(
        self, config_flow: KostalPlenticoreConfigFlow, mock_hass: HomeAssistant
    ) -> None:
        """Test reconfigure step with successful connection."""
        user_input = {
            CONF_HOST: "192.168.1.100",
            CONF_PASSWORD: "new_password",
            CONF_SERVICE_CODE: "12345",
        }
        
        # Mock the connection test
        mock_client = MockPlenticoreClient()
        mock_client.set_settings_data({
            "scb:network": {
                "Network:Hostname": "test-inverter",
            },
        })
        
        # Mock the existing entry
        mock_entry = MagicMock()
        config_flow._get_reconfigure_entry = MagicMock(return_value=mock_entry)
        
        with patch("pykoplenti.ApiClient", return_value=mock_client), \
             patch("kostal_plenticore.helper.get_hostname_id", return_value="Network:Hostname"), \
             patch.object(config_flow, "_async_abort_entries_match"), \
             patch.object(config_flow, "async_update_reload_and_abort"):
            
            result = await config_flow.async_step_reconfigure(user_input)
            
            assert result["type"] == FlowResultType.ABORT
            config_flow.async_update_reload_and_abort.assert_called_once_with(
                entry=mock_entry, title="test-inverter", data=user_input
            )
    
    @pytest.mark.asyncio
    async def test_async_step_reconfigure_auth_failure(
        self, config_flow: KostalPlenticoreConfigFlow, mock_hass: HomeAssistant
    ) -> None:
        """Test reconfigure step with authentication failure."""
        user_input = {
            CONF_HOST: "192.168.1.100",
            CONF_PASSWORD: "wrong_password",
            CONF_SERVICE_CODE: "12345",
        }
        
        # Mock the connection test to fail
        mock_client = MockPlenticoreClient()
        mock_client.set_should_fail_login(True)
        
        config_flow._get_reconfigure_entry = MagicMock(return_value=MagicMock())
        
        with patch("pykoplenti.ApiClient", return_value=mock_client), \
             patch.object(config_flow, "_async_abort_entries_match"):
            
            result = await config_flow.async_step_reconfigure(user_input)
            
            assert result["type"] == FlowResultType.FORM
            assert result["step_id"] == "reconfigure"
            assert result["errors"][CONF_PASSWORD] == "invalid_auth"
    
    @pytest.mark.asyncio
    async def test_async_step_reconfigure_network_error(
        self, config_flow: KostalPlenticoreConfigFlow, mock_hass: HomeAssistant
    ) -> None:
        """Test reconfigure step with network error."""
        user_input = {
            CONF_HOST: "192.168.1.100",
            CONF_PASSWORD: "test_password",
            CONF_SERVICE_CODE: "12345",
        }
        
        # Mock the connection test to fail
        mock_client = MockPlenticoreClient()
        mock_client.set_timeout(True)
        
        config_flow._get_reconfigure_entry = MagicMock(return_value=MagicMock())
        
        with patch("pykoplenti.ApiClient", return_value=mock_client), \
             patch.object(config_flow, "_async_abort_entries_match"):
            
            result = await config_flow.async_step_reconfigure(user_input)
            
            assert result["type"] == FlowResultType.FORM
            assert result["step_id"] == "reconfigure"
            assert result["errors"][CONF_HOST] == "cannot_connect"
    
    @pytest.mark.asyncio
    async def test_async_step_reconfigure_api_error(
        self, config_flow: KostalPlenticoreConfigFlow, mock_hass: HomeAssistant
    ) -> None:
        """Test reconfigure step with API error."""
        user_input = {
            CONF_HOST: "192.168.1.100",
            CONF_PASSWORD: "test_password",
            CONF_SERVICE_CODE: "12345",
        }
        
        # Mock the connection test to fail
        mock_client = MockPlenticoreClient()
        mock_client.set_should_fail_settings(True)
        
        config_flow._get_reconfigure_entry = MagicMock(return_value=MagicMock())
        
        with patch("pykoplenti.ApiClient", return_value=mock_client), \
             patch.object(config_flow, "_async_abort_entries_match"):
            
            result = await config_flow.async_step_reconfigure(user_input)
            
            assert result["type"] == FlowResultType.FORM
            assert result["step_id"] == "reconfigure"
            assert result["errors"][CONF_HOST] == "cannot_connect"
    
    @pytest.mark.asyncio
    async def test_async_step_reconfigure_unknown_error(
        self, config_flow: KostalPlenticoreConfigFlow, mock_hass: HomeAssistant
    ) -> None:
        """Test reconfigure step with unknown error."""
        user_input = {
            CONF_HOST: "192.168.1.100",
            CONF_PASSWORD: "test_password",
            CONF_SERVICE_CODE: "12345",
        }
        
        # Mock the connection test to raise an unexpected error
        config_flow._get_reconfigure_entry = MagicMock(return_value=MagicMock())
        
        with patch("kostal_plenticore.config_flow.test_connection", side_effect=Exception("Unexpected error")), \
             patch.object(config_flow, "_async_abort_entries_match"):
            
            result = await config_flow.async_step_reconfigure(user_input)
            
            assert result["type"] == FlowResultType.FORM
            assert result["step_id"] == "reconfigure"
            assert result["errors"]["base"] == "unknown"
    
    @pytest.mark.asyncio
    async def test_async_step_reconfigure_already_configured(
        self, config_flow: KostalPlenticoreConfigFlow, mock_hass: HomeAssistant
    ) -> None:
        """Test reconfigure step with already configured device."""
        user_input = {
            CONF_HOST: "192.168.1.100",
            CONF_PASSWORD: "test_password",
            CONF_SERVICE_CODE: "12345",
        }
        
        # Mock the connection test to succeed
        mock_client = MockPlenticoreClient()
        mock_client.set_settings_data({
            "scb:network": {
                "Network:Hostname": "test-inverter",
            },
        })
        
        config_flow._get_reconfigure_entry = MagicMock(return_value=MagicMock())
        
        with patch("pykoplenti.ApiClient", return_value=mock_client), \
             patch("kostal_plenticore.helper.get_hostname_id", return_value="Network:Hostname"), \
             patch.object(config_flow, "_async_abort_entries_match", side_effect=Exception("Already configured")):
            
            result = await config_flow.async_step_reconfigure(user_input)
            
            # Should handle the already configured case
            assert result["type"] in [FlowResultType.ABORT, FlowResultType.FORM]


# Integration tests
class TestConfigFlowIntegration:
    """Integration tests for the config flow."""
    
    @pytest.fixture
    def mock_hass(self) -> HomeAssistant:
        """Create a mock Home Assistant instance."""
        hass = MagicMock()
        return hass
    
    @pytest.mark.asyncio
    async def test_full_config_flow_success(self, mock_hass: HomeAssistant) -> None:
        """Test full config flow from start to success."""
        flow = KostalPlenticoreConfigFlow()
        flow.hass = mock_hass
        
        # Start the flow
        result = await flow.async_step_user(None)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"
        
        # Submit valid credentials
        user_input = {
            CONF_HOST: "192.168.1.100",
            CONF_PASSWORD: "test_password",
            CONF_SERVICE_CODE: "12345",
        }
        
        # Mock the connection test
        mock_client = MockPlenticoreClient()
        mock_client.set_settings_data({
            "scb:network": {
                "Network:Hostname": "test-inverter",
            },
        })
        
        with patch("pykoplenti.ApiClient", return_value=mock_client), \
             patch("kostal_plenticore.helper.get_hostname_id", return_value="Network:Hostname"), \
             patch.object(flow, "_async_abort_entries_match"):
            
            result = await flow.async_step_user(user_input)
            
            assert result["type"] == FlowResultType.CREATE_ENTRY
            assert result["title"] == "test-inverter"
            assert result["data"] == user_input
    
    @pytest.mark.asyncio
    async def test_full_config_flow_with_errors(self, mock_hass: HomeAssistant) -> None:
        """Test full config flow with errors and recovery."""
        flow = KostalPlenticoreConfigFlow()
        flow.hass = mock_hass
        
        # Start the flow
        result = await flow.async_step_user(None)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"
        
        # Submit invalid credentials
        user_input = {
            CONF_HOST: "192.168.1.100",
            CONF_PASSWORD: "wrong_password",
            CONF_SERVICE_CODE: "12345",
        }
        
        # Mock the connection test to fail
        mock_client = MockPlenticoreClient()
        mock_client.set_should_fail_login(True)
        
        with patch("pykoplenti.ApiClient", return_value=mock_client), \
             patch.object(flow, "_async_abort_entries_match"):
            
            result = await flow.async_step_user(user_input)
            
            assert result["type"] == FlowResultType.FORM
            assert result["step_id"] == "user"
            assert result["errors"][CONF_PASSWORD] == "invalid_auth"
        
        # Submit valid credentials
        user_input = {
            CONF_HOST: "192.168.1.100",
            CONF_PASSWORD: "correct_password",
            CONF_SERVICE_CODE: "12345",
        }
        
        # Mock the connection test to succeed
        mock_client.set_should_fail_login(False)
        mock_client.set_settings_data({
            "scb:network": {
                "Network:Hostname": "test-inverter",
            },
        })
        
        with patch("pykoplenti.ApiClient", return_value=mock_client), \
             patch("kostal_plenticore.helper.get_hostname_id", return_value="Network:Hostname"), \
             patch.object(flow, "_async_abort_entries_match"):
            
            result = await flow.async_step_user(user_input)
            
            assert result["type"] == FlowResultType.CREATE_ENTRY
            assert result["title"] == "test-inverter"
            assert result["data"] == user_input
    
    @pytest.mark.asyncio
    async def test_full_reconfigure_flow(self, mock_hass: HomeAssistant) -> None:
        """Test full reconfigure flow."""
        flow = KostalPlenticoreConfigFlow()
        flow.hass = mock_hass
        
        # Mock existing entry
        mock_entry = MagicMock()
        flow._get_reconfigure_entry = MagicMock(return_value=mock_entry)
        
        # Start reconfigure
        result = await flow.async_step_reconfigure(None)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reconfigure"
        
        # Submit new credentials
        user_input = {
            CONF_HOST: "192.168.1.100",
            CONF_PASSWORD: "new_password",
            CONF_SERVICE_CODE: "12345",
        }
        
        # Mock the connection test
        mock_client = MockPlenticoreClient()
        mock_client.set_settings_data({
            "scb:network": {
                "Network:Hostname": "test-inverter",
            },
        })
        
        with patch("pykoplenti.ApiClient", return_value=mock_client), \
             patch("kostal_plenticore.helper.get_hostname_id", return_value="Network:Hostname"), \
             patch.object(flow, "_async_abort_entries_match"), \
             patch.object(flow, "async_update_reload_and_abort"):
            
            result = await flow.async_step_reconfigure(user_input)
            
            assert result["type"] == FlowResultType.ABORT
            flow.async_update_reload_and_abort.assert_called_once_with(
                entry=mock_entry, title="test-inverter", data=user_input
            )


# Performance tests
class TestConfigFlowPerformance:
    """Performance tests for the config flow."""
    
    @pytest.mark.asyncio
    async def test_config_flow_performance(
        self, mock_hass: HomeAssistant, performance_monitor
    ) -> None:
        """Test config flow performance."""
        flow = KostalPlenticoreConfigFlow()
        flow.hass = mock_hass
        
        performance_monitor.start()
        
        # Start the flow
        result = await flow.async_step_user(None)
        
        performance_monitor.stop()
        
        # Performance assertions
        assert performance_monitor.duration < 0.1  # Should be very fast
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"
        
        performance_monitor.record_metric("flow_start_duration", performance_monitor.duration)
    
    @pytest.mark.asyncio
    async def test_connection_test_performance(
        self, mock_hass: HomeAssistant, performance_monitor
    ) -> None:
        """Test connection test performance."""
        mock_client = MockPlenticoreClient()
        mock_client.set_settings_data({
            "scb:network": {
                "Network:Hostname": "test-inverter",
            },
        })
        
        connection_data = {
            CONF_HOST: "192.168.1.100",
            CONF_PASSWORD: "test_password",
            CONF_SERVICE_CODE: "12345",
        }
        
        performance_monitor.start()
        
        with patch("pykoplenti.ApiClient", return_value=mock_client), \
             patch("kostal_plenticore.helper.get_hostname_id", return_value="Network:Hostname"):
            
            result = await test_connection(mock_hass, connection_data)
        
        performance_monitor.stop()
        
        # Performance assertions
        assert performance_monitor.duration < 1.0  # Should be fast
        assert result == "test-inverter"
        
        performance_monitor.record_metric("connection_test_duration", performance_monitor.duration)
    
    @pytest.mark.asyncio
    async def test_multiple_connection_tests_performance(
        self, mock_hass: HomeAssistant, performance_monitor
    ) -> None:
        """Test multiple connection tests performance."""
        mock_client = MockPlenticoreClient()
        mock_client.set_settings_data({
            "scb:network": {
                "Network:Hostname": "test-inverter",
            },
        })
        
        connection_data = {
            CONF_HOST: "192.168.1.100",
            CONF_PASSWORD: "test_password",
            CONF_SERVICE_CODE: "12345",
        }
        
        performance_monitor.start()
        
        # Run multiple connection tests
        for i in range(10):
            with patch("pykoplenti.ApiClient", return_value=mock_client), \
                 patch("kostal_plenticore.helper.get_hostname_id", return_value="Network:Hostname"):
                
                result = await test_connection(mock_hass, connection_data)
                assert result == "test-inverter"
        
        performance_monitor.stop()
        
        # Performance assertions
        assert performance_monitor.duration < 2.0  # Should be fast even with multiple tests
        assert mock_client.login.call_count == 10
        assert mock_client.get_setting_values.call_count == 10
        
        performance_monitor.record_metric("multiple_connection_tests_duration", performance_monitor.duration)
        performance_monitor.record_metric("connection_test_count", 10)
