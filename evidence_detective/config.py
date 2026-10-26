import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path=None):
    path = Path(path or ROOT / ".env.local")
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                name, value = line.split("=", 1)
                os.environ.setdefault(name.strip(), value.strip().strip("\"'"))


def search_key():
    return os.getenv("YUANBAO_SEARCH_API_KEY") or os.getenv("TENCENTCLOUD_WSA_APIKEY", "")


def reasoning_config():
    """A model service is opt-in; ambient provider credentials grant no consent."""
    if os.getenv("REASONING_ENABLED", "").lower() not in ("1", "true", "yes"):
        return None
    values = {"api_key": os.getenv("REASONING_API_KEY", "").strip(),
              "base_url": os.getenv("REASONING_BASE_URL", "").strip().rstrip("/"),
              "model": os.getenv("REASONING_MODEL", "").strip()}
    if not all(values.values()):
        return None
    max_tokens = os.getenv("REASONING_MAX_TOKENS", "").strip()
    if max_tokens:
        if not max_tokens.isdigit() or not 256 <= int(max_tokens) <= 32000:
            return None
        values["max_tokens"] = int(max_tokens)
    effort = os.getenv('REASONING_EFFORT', '').strip()
    if effort:
        if effort not in {'none', 'low', 'high', 'max'}:
            return None
        values['reasoning_effort'] = effort
    from urllib.parse import urlsplit
    try:
        endpoint = urlsplit(values["base_url"])
        if endpoint.scheme != "https" or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
            return None
    except ValueError:
        return None
    return values


def model_key():
    configuration = reasoning_config()
    return configuration["api_key"] if configuration else ""
