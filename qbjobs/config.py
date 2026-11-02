"""Settings, read from a .env file in the repo folder (see .env.example) or the environment."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")

# "sandbox" until you deliberately switch. Production means your real books.
QBO_ENV = os.environ.get("QBO_ENV", "sandbox").strip().lower()
CLIENT_ID = os.environ.get("QBO_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("QBO_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get("QBO_REDIRECT_URI", "http://localhost:8000/callback")

DB_PATH = Path(os.environ.get("JOBS_DB", ROOT / "jobs.db"))
TOKENS_PATH = Path(os.environ.get("QBO_TOKENS", ROOT / ".qbo-tokens.json"))

if QBO_ENV not in ("sandbox", "production"):
    raise SystemExit(f"QBO_ENV must be 'sandbox' or 'production', not {QBO_ENV!r}")
