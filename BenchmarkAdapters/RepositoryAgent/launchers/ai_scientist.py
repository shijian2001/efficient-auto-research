from ..profiles import PROFILES
from ..runner import main

PROFILE = PROFILES["ai-scientist"]


if __name__ == "__main__":
    raise SystemExit(main(default_agent=PROFILE.key))
