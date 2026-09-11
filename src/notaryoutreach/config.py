"""Load Phase 1 settings from the environment and a local .env file."""

import argparse
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values


class ConfigurationError(ValueError):
    """Required application settings are missing or invalid."""


@dataclass(frozen=True)
class Settings:
    supabase_url: str
    supabase_key: str = field(repr=False)


def load_config(env_file: str | Path = ".env") -> Settings:
    """Read settings without changing the process environment.

    Relative paths are resolved from the current working directory. Existing
    environment variables take precedence over values in the file.
    """
    values = {**dotenv_values(env_file), **os.environ}
    required = ("SUPABASE_URL", "SUPABASE_KEY")
    settings = {name: (values.get(name) or "").strip() for name in required}
    missing = [name for name, value in settings.items() if not value]
    if missing:
        raise ConfigurationError(
            "Missing required settings: " + ", ".join(missing)
            + ". Set them in the environment or your .env file."
        )

    url = settings["SUPABASE_URL"]
    try:
        parsed = urlsplit(url)
        valid_url = (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
            and not any(character.isspace() for character in url)
        )
        # Accessing port also validates its format and range.
        parsed.port
    except ValueError:
        valid_url = False
    if not valid_url:
        raise ConfigurationError(
            "SUPABASE_URL must be an HTTP(S) project URL, "
            "without credentials, an API path, a query, or a fragment."
        )

    return Settings(
        supabase_url=url.rstrip("/"),
        supabase_key=settings["SUPABASE_KEY"],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Supabase configuration.")
    parser.add_argument(
        "--env-file", type=Path, default=Path(".env"),
        help="Environment file (default: .env in the current directory).",
    )
    args = parser.parse_args()
    try:
        load_config(args.env_file)
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}")
        return 1
    print("Configuration valid. Supabase connection has not been tested.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
