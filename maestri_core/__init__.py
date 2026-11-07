"""
Maestri Core Python package for Maestrix GTK 4 Native.
"""

__version__ = "0.1.0"

from .models.node import CanvasNode, NodeFrame, TerminalContent, StickyNoteContent, PortalContent, FileTreeContent
from .models.connection import Connection, Point2D
from .models.workspace import WorkspaceDocument, WorkspacePayload, WorkspaceEntry, SidebarLayout, SidebarGroup
from .signals.bus import bus, SignalBus
from .client.api import MaestriClient

__all__ = [
    "CanvasNode",
    "NodeFrame",
    "TerminalContent",
    "StickyNoteContent",
    "PortalContent",
    "FileTreeContent",
    "Connection",
    "Point2D",
    "WorkspaceDocument",
    "WorkspacePayload",
    "WorkspaceEntry",
    "SidebarLayout",
    "SidebarGroup",
    "bus",
    "SignalBus",
    "MaestriClient",
]
