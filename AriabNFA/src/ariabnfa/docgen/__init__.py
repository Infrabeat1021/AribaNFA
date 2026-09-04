"""Word document generation. Knows nothing about Ariba."""

from .builder import NFABuilder, build_nfa, output_filename

__all__ = ["NFABuilder", "build_nfa", "output_filename"]
