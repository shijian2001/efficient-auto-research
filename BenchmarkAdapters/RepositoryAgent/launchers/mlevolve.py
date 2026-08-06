from ..profiles import PROFILES
from ..runner import main

PROFILE = PROFILES["mlevolve"]


if __name__ == "__main__":
    raise SystemExit(main(default_agent=PROFILE.key))
