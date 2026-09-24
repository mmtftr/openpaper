"""Ingest worker entry point: `python -m app.ingest.worker` (design §4).

One process for the whole deployment (same image as the server). The
scheduling lives in `engine.py`; this file wires logging and signals.
SIGTERM / SIGINT stop claiming new work, give running stages a short grace
period and requeue whatever didn't finish.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from app.ingest.engine import Engine


async def main() -> None:
    engine = Engine()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, engine.stop)
    await engine.run()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(main())
