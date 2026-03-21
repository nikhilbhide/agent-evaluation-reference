import pytest
import os

@pytest.fixture
def mock_env_project(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "mock-test-project")
