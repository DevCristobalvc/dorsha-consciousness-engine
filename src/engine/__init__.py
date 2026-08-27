"""Dorsha Consciousness Engine — recall, judgment and loop supervision for AI agents.

The engine is a thin, file-based protocol + supervision service that works
beside any agent (Claude Code, Codex, Gemini CLI, Hermes, ...). See ``idea.md``
for the vision and ``TODO.md`` for the work contract.
"""

from engine.config import Settings

__version__ = "0.1.0"
__all__ = ["Settings", "__version__"]
