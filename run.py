#!/usr/bin/env python3
"""Entry point for the Polymarket CopyBot."""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "backend.app:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
