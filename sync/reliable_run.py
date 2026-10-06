#!/usr/bin/env python3
"""Run one of osm-carto4mc's download scripts with a safety net.

Both ``scripts/get-fonts.py`` and ``scripts/get-external-data.py`` issue HTTP
requests **without a timeout**, so a single stalled connection hangs them
forever (observed in practice: connections to raw.githubusercontent.com are
blackholed on some networks, leaving the process sleeping on a CLOSE_WAIT
socket).  ``get-fonts.py`` is worse still: it aborts the entire run on the first
failure, throwing away the progress of every font it already downloaded.

This wrapper runs the requested script in-process after installing a tiny
``requests`` shim that supplies a default timeout and retries each request with
backoff.  Used as::

    reliable_run.py /path/to/script.py [script arguments...]
"""

import os
import runpy
import sys
import time

import requests

DEFAULT_TIMEOUT = float(os.environ.get("DOWNLOAD_REQUEST_TIMEOUT", "60"))
RETRIES = max(1, int(os.environ.get("DOWNLOAD_REQUEST_RETRIES", "5")))
BACKOFF = float(os.environ.get("DOWNLOAD_REQUEST_BACKOFF", "2"))

_original_request = requests.Session.request


def _retrying_request(self, method, url, **kwargs):
    # get-fonts.py / get-external-data.py never pass a timeout; a stalled socket
    # without one blocks forever instead of raising.
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    last_error = None
    for attempt in range(1, RETRIES + 1):
        try:
            return _original_request(self, method, url, **kwargs)
        except requests.RequestException as exc:
            last_error = exc
            if attempt == RETRIES:
                break
            delay = BACKOFF * attempt
            print(
                f"[reliable-run] {method} {url} failed "
                f"({exc.__class__.__name__}: {exc}); retry "
                f"{attempt}/{RETRIES - 1} in {delay:.0f}s",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(delay)
    raise last_error


# Patching Session.request also covers the module level requests.get(), because
# requests.api.get() delegates to a Session.
requests.Session.request = _retrying_request


def main(argv):
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    script = argv[1]
    sys.argv = [script] + list(argv[2:])
    runpy.run_path(script, run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
