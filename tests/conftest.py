"""Pytest fixtures for the Lightwave Smart custom integration."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from lightwave_smart import lightwave_smart

from tests.helpers.link import prepare_link

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/lightwave_smart as an integration under test."""
    return


class LinkScript:
    """Controls the next LWLink2 the integration constructs."""

    def __init__(self) -> None:
        self.activate = "ok"
        self.activate_error: Exception | None = None
        self.deactivate_raises = False
        self.clear_access_token = False
        self.drop_refresh_token = False
        self.token_rejected = False
        self.last_error: Exception | None = None
        self.links: list = []


@pytest.fixture
def link_script() -> LinkScript:
    return LinkScript()


@pytest.fixture
def mock_link(link_script: LinkScript):
    """Return prepared real LWLink2 instances from the integration's constructor."""
    real = lightwave_smart.LWLink2

    def factory(*args, **kwargs):
        link = real(*args, **kwargs)
        websocket = prepare_link(link)
        if link_script.deactivate_raises:
            websocket.deactivate_error = RuntimeError("deactivate failed")
        if link_script.token_rejected and getattr(link, "auth", None) is not None:
            link.auth.token_rejected = True
        if link_script.last_error is not None and getattr(link, "auth", None) is not None:
            link.auth.last_error = link_script.last_error

        if link_script.activate != "ok" or link_script.clear_access_token or link_script.drop_refresh_token:
            original_get_tokens = link.auth.get_tokens
            cleared = {"value": False}

            def get_tokens():
                tokens = dict(original_get_tokens() or {})
                if cleared["value"] and link_script.clear_access_token:
                    tokens.pop("access_token", None)
                if cleared["value"] and link_script.drop_refresh_token:
                    tokens.pop("refresh_token", None)
                return tokens

            link.auth.get_tokens = get_tokens

            async def scripted_activate(*args, **kwargs):
                cleared["value"] = True
                if link_script.activate == "false":
                    return False
                if link_script.activate == "error":
                    raise link_script.activate_error or RuntimeError("timeout")
                if link_script.activate == "cancel":
                    import asyncio

                    raise asyncio.CancelledError
                if link_script.activate == "keyboard":
                    raise KeyboardInterrupt
                return False

            link.async_activate = scripted_activate

        link_script.links.append(link)
        return link

    with patch("lightwave_smart.lightwave_smart.LWLink2", side_effect=factory):
        yield link_script
