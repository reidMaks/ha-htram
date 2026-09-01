"""Fixtures for the Home Assistant integration tests."""

from __future__ import annotations

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture
def custom_integration(enable_custom_integrations):
    """Let Home Assistant load custom_components/htram.

    Requested explicitly rather than autouse: the protocol tests are plain
    functions and have no business paying for a Home Assistant fixture.
    """
    yield
