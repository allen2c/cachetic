import os

import logging_bullet_train
import pytest

import cachetic


@pytest.fixture(scope="session", autouse=True)
def set_env_vars():
    os.environ["PYTEST_RUNNING"] = "true"
    os.environ["PYTEST_IS_RUNNING"] = "true"

    logging_bullet_train.set_logger(cachetic.LOGGER_NAME)
