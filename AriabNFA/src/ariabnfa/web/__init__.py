"""Browser interface, served locally.

An alternative front end to the same pipeline the desktop window uses: `ariba`
fetches, `mapping` extracts, `docgen` writes the Word file. Nothing in those
packages knows which interface called it, which is what made this port a matter
of replacing the front end rather than rewriting the tool.

It binds to loopback by default. Serving it to other machines would turn a
workstation into an unmanaged server holding Ariba credentials, with no TLS, so
that is a deliberate decision rather than a default.
"""

from .server import create_app, run

__all__ = ["create_app", "run"]
