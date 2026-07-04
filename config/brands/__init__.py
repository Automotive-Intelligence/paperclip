"""config.brands -- per-brand configs for the Universal Intent-to-Revenue workflow.

Each `<brand>.yaml` is validated against BrandConfig on load
(services/intent_workflow_runner.py::_load_brand_config). Adding a new brand
means creating a new YAML file that validates -- nothing in the runner changes.
"""
from config.brands._schema import BrandConfig  # noqa: F401
