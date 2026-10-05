"""No test may accidentally access the network or execute target code."""

import os
import socket
import subprocess

import pytest
from hypothesis import HealthCheck, settings

settings.register_profile(
    "ci",
    max_examples=75,
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
settings.load_profile("ci")


@pytest.fixture(autouse=True)
def deny_network_and_processes(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Tests must not access real networks or execute child processes")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(subprocess, "Popen", denied)
    monkeypatch.setattr(os, "system", denied)
    monkeypatch.setattr(os, "popen", denied)
