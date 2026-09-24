from __future__ import annotations

import os

import pytest

from confidence_scorer.i18n import ENV_VAR, set_config_language


@pytest.fixture(autouse=True)
def _english_output():
    # The language lives in process-wide state (a module global and an env var that
    # `--lang` sets for worker subprocesses), so one test switching to Russian
    # would otherwise leak into every test after it.
    saved = os.environ.pop(ENV_VAR, None)
    set_config_language("en")
    yield
    os.environ.pop(ENV_VAR, None)
    if saved is not None:
        os.environ[ENV_VAR] = saved
    set_config_language("en")
