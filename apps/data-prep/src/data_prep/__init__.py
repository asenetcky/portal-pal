"""Flatten raw portal catalog snapshots into tidy tables, diffs, and RAG chunks."""

from data_prep.changes import change_summary, diff_snapshots
from data_prep.chunks import build_chunks, changed_chunks, content_hash
from data_prep.flatten import PortalTables, flatten_file, flatten_record

__all__ = [
    "PortalTables",
    "build_chunks",
    "change_summary",
    "changed_chunks",
    "content_hash",
    "diff_snapshots",
    "flatten_file",
    "flatten_record",
]
