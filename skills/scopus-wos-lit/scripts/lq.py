# -*- coding: utf-8 -*-
"""Entry point: python lq.py <command> ...

Keeps the package importable without installation by putting this directory on
sys.path.
"""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from litquery.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
