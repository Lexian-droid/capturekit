import sys

# These modules import Windows-only bindings at import time; on other
# platforms they are not collected at all (reported as not run, never passed).
collect_ignore_glob = [] if sys.platform == "win32" else ["test_*.py"]
