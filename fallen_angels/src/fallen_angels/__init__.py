"""fallen_angels: operational stack for fallen-angel long/short credit trades.

The package is parameterized entirely by per-issuer YAML configs under
``config/`` (e.g. ``config/cnc.yaml``). Submodules are imported lazily by the
caller rather than re-exported here, so importing the package stays cheap and
does not require Bloomberg to be present.
"""

from __future__ import annotations

__version__ = "0.1.0"
