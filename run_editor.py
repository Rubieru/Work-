"""Startskript (auch Einstiegspunkt für PyInstaller)."""
import sys

from luftbild_editor.app import main

if __name__ == "__main__":
    sys.exit(main())
