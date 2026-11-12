"""Select the host's native widget toolkit: Qt on Plasma, GTK on GNOME."""
import argparse
import os
import sys

from .core import InstallError, portable_root


def main():
    args = argparse.ArgumentParser(description="Savage Wind portable launcher")
    args.add_argument("--root", help="Explicit install folder for development/tests")
    args.add_argument("--selftest", action="store_true", help="Validate Python imports without creating files")
    args.add_argument("--config", action="store_true", help="Abrir o instalador em vez de iniciar direto")
    args.add_argument("--mods", action="store_true", help="Open SWMM")
    args.add_argument("--mods-play", action="store_true", help="Launch game from SWMM")
    opt = args.parse_args()
    try:
        root = portable_root(explicit=opt.root)
    except InstallError as error:
        print(error, file=sys.stderr)
        return 2
    if opt.mods_play:
        from .core import Pipeline
        import threading
        try:
            Pipeline(root, lambda *_: None, lambda _: False,
                     threading.Event(), mode="play").run()
        except InstallError as error:
            print(error, file=sys.stderr)
            return 1
        return 0
    if opt.mods:
        from .swmm import launch
        try:
            launch(root, launcher=os.environ.get("APPIMAGE"))
        except (InstallError, OSError) as error:
            print(error, file=sys.stderr)
            return 1
        return 0
    desktop = (os.getenv("XDG_CURRENT_DESKTOP", "") + ":" + os.getenv("DESKTOP_SESSION", "")).lower()
    kde = "kde" in desktop or "plasma" in desktop
    try:
        if kde:
            from . import qt_ui
            return qt_ui.run(root, selftest=opt.selftest, autoplay=not opt.config)
        from . import gtk_ui
        return gtk_ui.run(root, selftest=opt.selftest, autoplay=not opt.config)
    except ImportError as error:
        library = "PySide6 ou PyQt6" if kde else "PyGObject e GTK 3"
        print(f"A interface nativa requer {library}. Detalhes: {error}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
