import sys
from proctor.live import main as run
from proctor.core.config import ConfigError


def main():
    try:
        return run()
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
