import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017")
os.environ.setdefault("AI_API_KEY", "test")
