import sys, os
_SRC = os.path.join(os.path.dirname(__file__), '..', 'src')
_ROOT = os.path.join(os.path.dirname(__file__), '..')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
