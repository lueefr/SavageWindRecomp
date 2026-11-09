"""Select the host's native widget toolkit: Qt on Plasma, GTK on GNOME."""
import argparse
import os
import sys

from .core import InstallError, portable_root


def main():
    args = argparse.ArgumentParser(description="Savage Wind portable launcher")
    args.add_argument("--root", help="Explicit install folder for development/tests")
    args.add_argument("--selftest", action="store_true", help="Validate Python imports without creating files")
    opt = args.parse_args()
    try:
        root = portable_root(explicit=opt.root)
    except InstallError as error:
        print(error, file=sys.stderr)
        return 2
    desktop = (os.getenv("XDG_CURRENT_DESKTOP", "") + ":" + os.getenv("DESKTOP_SESSION", "")).lower()
    kde = "kde" in desktop or "plasma" in desktop
    try:
        if kde:
            from . import qt_ui
            return qt_ui.run(root, selftest=opt.selftest)
        from . import gtk_ui
        return gtk_ui.run(root, selftest=opt.selftest)
    except ImportError as error:
        library = "PySide6 ou PyQt6" if kde else "PyGObject e GTK 3"
        print(f"A interface nativa requer {library}. Detalhes: {error}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
