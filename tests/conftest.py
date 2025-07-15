import os
import pathlib
import tempfile

import logging_bullet_train
import pytest


@pytest.fixture(scope="session", autouse=True)
def set_env_vars():
    os.environ["ENVIRONMENT"] = "test"
    os.environ["PYTEST_RUNNING"] = "true"
    os.environ["PYTEST_IS_RUNNING"] = "true"

    logging_bullet_train.set_logger("cachetic")


@pytest.fixture(scope="function")
def temp_cache_url():
    with tempfile.TemporaryDirectory() as temp_dir:
        yield pathlib.Path(temp_dir).joinpath(".cachetic")
