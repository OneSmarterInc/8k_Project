"""
Filing text built from existing FilingChunk rows (guide 5.4: reuse
FilingChunk rather than re-parsing).

Used by the labelling screen now, and by the Interpreter in Phase 4, so
the text a human labels and the text the model classifies come from the
same function.

Two corrections to the guide's sketch, both found in the code:

1. Exhibit type lives on FilingDocument.document_type, not on
   FilingChunk.section_title. Filtering section_title for "EX-" matches
   nothing.
2. chunk_index restarts per document, and FilingChunker overlaps
   consecutive chunks of a long section by 400 characters. Chunks are
   ordered per document and the overlap is trimmed using char_start /
   char_end, so text is not duplicated.
"""

import hashlib
from dataclasses import dataclass, field

from watcher.models import FilingChunk


# The narrative body plus press-release exhibits. EX-10 (contracts),
# EX-4 (instruments), EX-5 (legal opinions) and the like are left out:
# they are long and say what the paper is, not what the company did.
DEFAULT_EXHIBIT_PREFIXES = ("EX-99",)

# Labellers can read more than a model can hold.
LABELLER_MAX_CHARS = 60000


@dataclass
class FilingText:
    text: str
    truncated: bool
    sha256: str
    documents: list = field(default_factory=list)
    chunk_count: int = 0

    @property
    def is_empty(self):
        return not self.text.strip()


def _document_key(chunk):
    """Primary document first, then exhibits in capture order."""
    document = chunk.document

    if document is None or document.is_primary:
        return (0, 0)

    return (1, document.id)


def _is_wanted(chunk, exhibit_prefixes):
    document = chunk.document

    # Legacy chunks created before FilingDocument existed belong to the
    # primary document.
    if document is None or document.is_primary:
        return True

    document_type = (document.document_type or "").strip().upper()

    return document_type.startswith(tuple(
        prefix.upper() for prefix in exhibit_prefixes
    ))


def _document_label(chunk):
    document = chunk.document

    if document is None or document.is_primary:
        return "PRIMARY DOCUMENT"

    return (document.document_type or "EXHIBIT").strip().upper()


def build_filing_text(
    filing,
    *,
    max_chars=LABELLER_MAX_CHARS,
    exhibit_prefixes=DEFAULT_EXHIBIT_PREFIXES,
):
    chunks = [
        chunk
        for chunk in (
            FilingChunk.objects
            .filter(filing=filing)
            .select_related("document")
        )
        if _is_wanted(chunk, exhibit_prefixes)
    ]

    chunks.sort(key=lambda c: (_document_key(c), c.chunk_index))

    parts = []
    documents = []
    current_key = None
    previous_end = None

    for chunk in chunks:
        key = _document_key(chunk)

        if key != current_key:
            current_key = key
            previous_end = None
            label = _document_label(chunk)
            documents.append(label)
            parts.append(f"\n\n===== {label} =====\n\n")

        text = chunk.text or ""

        # Trim the chunker's overlap with the previous chunk.
        if (
            previous_end is not None
            and chunk.char_start is not None
            and chunk.char_start < previous_end
        ):
            overlap = previous_end - chunk.char_start
            text = text[overlap:].lstrip()

        if chunk.char_end is not None:
            previous_end = max(previous_end or 0, chunk.char_end)

        if text:
            parts.append(text.strip() + "\n\n")

    full_text = "".join(parts).strip()
    truncated = len(full_text) > max_chars
    text = full_text[:max_chars] if truncated else full_text

    return FilingText(
        text=text,
        truncated=truncated,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        documents=documents,
        chunk_count=len(chunks),
    )
