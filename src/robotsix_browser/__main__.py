"""Console entrypoint: serve the robotsix-browser HTTP API with uvicorn."""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("ROBOTSIX_BROWSER_HOST", "0.0.0.0")
    port = int(os.environ.get("ROBOTSIX_BROWSER_PORT", "8000"))
    uvicorn.run("robotsix_browser.app:create_app", host=host, port=port, factory=True)


if __name__ == "__main__":
    main()
