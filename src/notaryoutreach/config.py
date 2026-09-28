"""Local database paths and selectable model providers."""

import argparse
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

from providers.settings import ProviderSettings, provider_settings


class ConfigurationError(ValueError):
    """Application settings are missing or invalid."""


@dataclass(frozen=True)
class Settings:
    groq_api_key: str | None = field(default=None, repr=False)
    database_path: Path = Path("data/outreach.db")
    checkpoint_path: Path = Path("runs/checkpoints.sqlite3")
    llm: ProviderSettings | None = None
    search: ProviderSettings | None = None


def load_config(env_file: str | Path = ".env", *, require_api_key: bool = False) -> Settings:
    """Environment overrides .env; reading local results needs no API key."""
    values = {**dotenv_values(env_file), **os.environ}
    key = (values.get("GROQ_API_KEY") or "").strip() or None
    try:
        llm = provider_settings(values, require_key=require_api_key)
        search = provider_settings(values, search=True, require_key=require_api_key)
    except ValueError as exc:
        raise ConfigurationError(str(exc)) from exc
    paths = {}
    for name, default in (("DATABASE_PATH", "data/outreach.db"),
                          ("CHECKPOINT_PATH", "runs/checkpoints.sqlite3")):
        raw = (values.get(name) or default).strip()
        if not raw or raw == ":memory:" or raw.startswith("file:"):
            raise ConfigurationError(f"{name} must be a persistent local file path.")
        paths[name] = Path(raw).expanduser()
    if paths["DATABASE_PATH"].resolve() == paths["CHECKPOINT_PATH"].resolve():
        raise ConfigurationError("Application data and checkpoints need distinct file paths.")
    return Settings(groq_api_key=key, database_path=paths["DATABASE_PATH"],
                    checkpoint_path=paths["CHECKPOINT_PATH"], llm=llm, search=search)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate local outreach configuration.")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--require-api-key", action="store_true", help="Also check credentials for the selected providers.")
    args = parser.parse_args()
    try:
        load_config(args.env_file, require_api_key=args.require_api_key)
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}")
        return 1
    print("Configuration valid. No external connection was made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
