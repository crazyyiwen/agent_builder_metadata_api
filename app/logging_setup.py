"""Logging configuration. Module is named ``logging_setup`` to avoid shadowing
the stdlib ``logging`` module."""

from __future__ import annotations

import logging
import logging.config


def setup_logging(level: str = "INFO") -> None:
    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                "datefmt": "%Y-%m-%dT%H:%M:%S%z",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "stream": "ext://sys.stdout",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["console"], "level": level, "propagate": False},
            "uvicorn.error": {"handlers": ["console"], "level": level, "propagate": False},
            "uvicorn.access": {"handlers": ["console"], "level": level, "propagate": False},
            "app": {"handlers": ["console"], "level": level, "propagate": False},
        },
        "root": {"handlers": ["console"], "level": level},
    }
    logging.config.dictConfig(config)
