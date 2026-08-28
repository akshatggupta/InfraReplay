"""Abstract interfaces every plugin implements. No concrete plugin here."""

from infrareplay.contracts.capture import CapturePlugin
from infrareplay.contracts.comparator import ComparatorPlugin
from infrareplay.contracts.redaction import RedactionPlugin
from infrareplay.contracts.storage import StoragePlugin

__all__ = ["CapturePlugin", "ComparatorPlugin", "RedactionPlugin", "StoragePlugin"]
