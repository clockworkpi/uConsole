"""Run unittest unchanged, retaining periodic thread traces if the suite stalls."""
import faulthandler
import math
import os
import sys
import unittest


def main(argv=None, *, dump_after=300):
    if type(dump_after) not in (int, float) or not math.isfinite(dump_after) or dump_after <= 0:
        raise ValueError('Traceback interval must be finite and positive')
    faulthandler.enable(all_threads=True)
    faulthandler.dump_traceback_later(dump_after, repeat=True)
    try:
        # Test selection, reporting and exit status remain unittest's own.
        unittest.main(module=None, argv=[sys.argv[0], *(sys.argv[1:] if argv is None else argv)])
    finally:
        faulthandler.cancel_dump_traceback_later()


if __name__ == '__main__':
    # Match `python -m unittest` import precedence, not the tools directory.
    sys.path[0] = os.getcwd()
    main()
