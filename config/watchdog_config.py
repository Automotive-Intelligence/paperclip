"""Loader for config/watchdog.yaml.

One cached accessor, mirroring config/pricing.py, so the watchdog checks and any
future consumer read the SAME coverage config. Brand/threshold specifics live in
the yaml, never hardcoded in a check, so extending to a new business is a config
edit rather than a code change.
"""
from __future__ import annotations

import os
from functools import lru_cache

import yaml

_PATH = os.path.join(os.path.dirname(__file__), "watchdog.yaml")


@lru_cache(maxsize=1)
def load_watchdog_config() -> dict:
    with open(_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}
