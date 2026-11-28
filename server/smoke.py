"""Verify a configured OpenAI key using a small synthetic cloud-analyst request."""

import os
from urllib.error import HTTPError, URLError

from server.cloud_server import generate


def main():
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        print("OpenAI check skipped: OPENAI_API_KEY is not configured.")
        return 2
    data = {
        "language": "en",
        "question": "What is the last observed closing price?",
        "facts": {"series": "synthetic smoke test", "last_close": 100},
    }
    try:
        generate(key, os.environ.get("PIVLUMA_OPENAI_MODEL", "gpt-5-mini"), data)
    except HTTPError as error:
        print(f"OpenAI returned HTTP {error.code}.")
        return 1
    except (URLError, OSError, ValueError, KeyError, TypeError):
        print("OpenAI connectivity or response check failed.")
        return 1
    print("OpenAI returned a cloud-analyst response.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
