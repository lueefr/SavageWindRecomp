"""PyInstaller entry point for the bundled Qt frontend."""
import argparse
import sys

from wrapper.core import InstallError, portable_root
from wrapper.qt_ui import run


def main():
    parser = argparse.ArgumentParser(description="Savage Wind portable GUI")
    parser.add_argument("--root")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    try:
        root = portable_root(explicit=args.root)
    except InstallError as error:
        print(error, file=sys.stderr)
        return 2
    return run(root, selftest=args.selftest)


if __name__ == "__main__":
    sys.exit(main())
