"""Runs the Osaeru web app: `python -m osaeru.web`."""

import os
import uvicorn

from osaeru import logging_config


def main() -> None:
    """
    Entry point for the application.

    This function configures logging and starts the Uvicorn server to run the web application.
    The server host, port, and reload settings can be customized using environment variables:
    - OSAERU_HOST: Specifies the host address (default is "127.0.0.1").
    - PORT: Specifies the port number (default is 8000).
    - OSAERU_RELOAD: Enables or disables auto-reload (set to "1" to enable).

    Note:
    The application is configured to run on localhost by default, as it does not include its own authentication mechanism.
    """
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
