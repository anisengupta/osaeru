"""Runs the Osaeru web app: `python -m osaeru.web`."""

import os

import uvicorn

from osaeru import logging_config


def main() -> None:
    logging_config.configure()
    uvicorn.run(
        "osaeru.web.main:app",
        # Localhost only by default: the app has no login of its own.
        host=os.environ.get("OSAERU_HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        reload=os.environ.get("OSAERU_RELOAD") == "1",
    )


if __name__ == "__main__":
    main()
