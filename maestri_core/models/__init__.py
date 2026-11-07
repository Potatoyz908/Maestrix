"""
Models package for Maestrix GTK 4 Native.
"""

from .node import (
    CanvasNode,
    NodeFrame,
    TerminalContent,
    StickyNoteContent,
    PortalContent,
    FileTreeContent,
    current_iso8601,
)
from .connection import Connection, Point2D
from .workspace import (
    WorkspaceDocument,
    WorkspacePayload,
    WorkspaceEntry,
    SidebarGroup,
    SidebarLayout,
)

from typing import Any

# Aliases for backward/forward compatibility
Workspace = WorkspaceDocument
Terminal = TerminalContent
StickyNote = StickyNoteContent
Portal = PortalContent
FileTree = FileTreeContent
NodeContent = Any
NodeType = str

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
    "SidebarGroup",
    "SidebarLayout",
    "Workspace",
    "Terminal",
    "StickyNote",
    "Portal",
    "FileTree",
    "NodeContent",
    "NodeType",
    "current_iso8601",
]
