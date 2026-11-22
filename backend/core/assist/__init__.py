"""Patent Assist core: pure helpers for the Google Patents assistant.

ASSIST_MODEL is the one shared model constant every assist module imports,
so switching models is a one-line change.
"""

from backend.groq_client import MODEL_CHAT

ASSIST_MODEL = MODEL_CHAT
