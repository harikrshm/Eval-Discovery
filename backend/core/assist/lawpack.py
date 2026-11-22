"""Law pack for Patent Assist: 12 short hand-written provision summaries.

Each file in lawpack/ is one provision: front matter (id, title, source,
status) between "---" lines, then a plain-language summary. The summaries are
DRAFTS written from general knowledge of 35 U.S.C. and the MPEP, not statute
text; the author must review them before they are shown to anyone.

INTENT_PROVISIONS is the fixed intent -> provision-ids map (at most 4 per
intent); excerpts_for turns it into the (id, text) pairs `assemble` takes as
`law_excerpts`. load_system_prompt returns the assistant's initial system
prompt (system_prompt.md).
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from backend.core.assist.intent import Intent

_DIR = Path(__file__).parent
_LAW_DIR = _DIR / "lawpack"
_PROMPT_FILE = _DIR / "system_prompt.md"

MAX_PROVISION_CHARS = 1500

# The fixed sentence the system prompt requires at the end of every legal-opinion answer.
NOT_LEGAL_ADVICE = "This is not legal advice, and I can't judge validity or infringement."
MAX_EXCERPTS_PER_INTENT = 4

_REQUIRED = ("id", "title", "source", "status")
_ID_RE = re.compile(r"[a-z0-9-]{1,64}")  # must match what citations.py treats as an id


@dataclass(frozen=True)
class Provision:
    id: str
    title: str
    source: str
    status: str
    text: str


def _parse(path: Path) -> Provision:
    raw = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    parts = raw.split("---\n", 2)
    if len(parts) != 3 or parts[0].strip():
        raise ValueError(f"{path.name}: missing front matter")
    fields: dict[str, str] = {}
    for line in parts[1].splitlines():
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise ValueError(f"{path.name}: bad front matter line {line!r}")
        fields[key.strip()] = value.strip()
    for key in _REQUIRED:
        if not fields.get(key):
            raise ValueError(f"{path.name}: front matter is missing {key!r}" + (" (id)" if key == "id" else ""))
    if not _ID_RE.fullmatch(fields["id"]):
        raise ValueError(f"{path.name}: id {fields['id']!r} must match [a-z0-9-]+ (max 64)")
    text = parts[2].strip()
    if not text:
        raise ValueError(f"{path.name}: empty summary")
    return Provision(fields["id"], fields["title"], fields["source"], fields["status"], text)


def _load_dir(directory: Path) -> dict[str, Provision]:
    pack: dict[str, Provision] = {}
    for path in sorted(directory.glob("*.md")):
        p = _parse(path)
        if p.id in pack:
            raise ValueError(f"{path.name}: duplicate provision id {p.id!r}")
        pack[p.id] = p
    return pack


@lru_cache(maxsize=1)
def _cached_pack() -> dict[str, Provision]:
    return _load_dir(_LAW_DIR)


def load_lawpack() -> dict[str, Provision]:
    """All provisions by id (read once; callers get their own dict)."""
    return dict(_cached_pack())


# Fixed map: every id must exist (checked at import) and no intent gets more than 4.
INTENT_PROVISIONS: dict[Intent, list[str]] = {
    Intent.claim_explain: ["mpep-2111", "mpep-2111-03"],
    Intent.claim_scope: ["mpep-2111", "mpep-2111-03", "usc-112f", "usc-112b"],
    Intent.status_family: ["usc-154"],
    Intent.citations_prior_art: ["usc-102", "usc-103", "mpep-2141"],
    Intent.classification: [],
    Intent.law_concept: ["usc-101", "usc-102", "usc-103", "usc-112a"],
    Intent.navigation_help: [],
    Intent.legal_opinion_request: ["usc-102", "usc-103", "usc-271", "mpep-2111"],
    Intent.other: [],
}


def _check_map() -> None:
    pack = _cached_pack()
    for intent in Intent:
        ids = INTENT_PROVISIONS[intent]
        if len(ids) > MAX_EXCERPTS_PER_INTENT:
            raise ValueError(f"{intent.value}: more than {MAX_EXCERPTS_PER_INTENT} provisions")
        missing = [i for i in ids if i not in pack]
        if missing:
            raise ValueError(f"{intent.value}: unknown provision ids {missing}")


_check_map()


def excerpts_for(intent: Intent) -> list[tuple[str, str]]:
    """(id, "title (source): text") pairs for `assemble(law_excerpts=...)`."""
    pack = _cached_pack()
    return [(i, f"{pack[i].title} ({pack[i].source}): {pack[i].text}") for i in INTENT_PROVISIONS[intent]]


@lru_cache(maxsize=1)
def load_system_prompt() -> str:
    return _PROMPT_FILE.read_text(encoding="utf-8").strip()
