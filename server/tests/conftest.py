"""Pytest bootstrap: load the dev .env before app modules import."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

SERVER_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(SERVER_ROOT / ".env")
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

# Keep registry tests deterministic regardless of the developer's env.
os.environ.pop("MODEL_OVERRIDES", None)
