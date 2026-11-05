"""
Unit tests for maestri_core data models.
Validates schemaVersion: 2 compatibility:
- frame: [[x, y], [w, h]]
- node content: { "content": { "<type>": { "_0": <payload> } } }
- connections: 6 typed connection kinds
- date format: ISO8601 without fractional seconds
"""

import json
import pytest

from maestri_core.models.node import (
    CanvasNode,
    NodeFrame,
    TerminalContent,
    StickyNoteContent,
    PortalContent,
    FileTreeContent,
    current_iso8601,
)
from maestri_core.models.connection import Connection, Point2D
from maestri_core.models.workspace import (
    WorkspaceDocument,
    WorkspacePayload,
    WorkspaceEntry,
)


class TestModels:
    def test_node_frame_serialization(self):
        frame = NodeFrame(x=150.0, y=250.0, width=500.0, height=400.0)
        serialized = frame.to_list()
        assert serialized == [[150.0, 250.0], [500.0, 400.0]]

        deserialized = NodeFrame.from_list(serialized)
        assert deserialized.x == 150.0
        assert deserialized.y == 250.0
        assert deserialized.width == 500.0
        assert deserialized.height == 400.0

    def test_terminal_node_round_trip(self):
        content = TerminalContent(
            name="Terminal 1",
            working_directory="/home/user/project",
            shell_path="/bin/bash",
            icon="terminal",
            color="#007AFF",
        )
        node = CanvasNode(
            id="NODE-1234",
            frame=NodeFrame(x=100.0, y=100.0, width=400.0, height=300.0),
            node_type="terminal",
            content=content,
        )

        d = node.to_dict()
        assert d["id"] == "NODE-1234"
        assert d["frame"] == [[100.0, 100.0], [400.0, 300.0]]
        assert "content" in d
        assert "terminal" in d["content"]
        assert "_0" in d["content"]["terminal"]
        assert d["content"]["terminal"]["_0"]["workingDirectory"] == "/home/user/project"

        restored = CanvasNode.from_dict(d)
        assert restored.id == "NODE-1234"
        assert restored.frame.x == 100.0
        assert isinstance(restored.content, TerminalContent)
        assert restored.content.working_directory == "/home/user/project"

    def test_sticky_note_node_round_trip(self):
        content = StickyNoteContent(
            name="Meeting Notes",
            file_name="meeting-notes.md",
            color="#FFCC00",
        )
        node = CanvasNode(
            id="NODE-NOTE-1",
            frame=NodeFrame(x=600.0, y=200.0, width=320.0, height=240.0),
            node_type="stickyNote",
            content=content,
        )
        d = node.to_dict()
        assert "content" in d
        assert "stickyNote" in d["content"]
        assert d["content"]["stickyNote"]["_0"]["fileName"] == "meeting-notes.md"

        restored = CanvasNode.from_dict(d)
        assert isinstance(restored.content, StickyNoteContent)
        assert restored.content.file_name == "meeting-notes.md"
        assert restored.content.color == "#FFCC00"

    def test_connection_kinds(self):
        # 1. Terminal to Terminal
        c1 = Connection(a_id="T1", b_id="T2", kind="terminal")
        d1 = c1.to_dict()
        assert d1["terminalIdA"] == "T1"
        assert d1["terminalIdB"] == "T2"

        # 2. Terminal to Note
        c2 = Connection(a_id="T1", b_id="N1", kind="terminalToNote")
        d2 = c2.to_dict()
        assert d2["terminalId"] == "T1"
        assert d2["noteNodeId"] == "N1"

        # 3. Portal to Portal
        c3 = Connection(a_id="P1", b_id="P2", kind="portalToPortal")
        d3 = c3.to_dict()
        assert d3["portalIdA"] == "P1"
        assert d3["portalIdB"] == "P2"

        # 4. Note to Note
        c4 = Connection(a_id="N1", b_id="N2", kind="noteToNote")
        d4 = c4.to_dict()
        assert d4["noteNodeIdA"] == "N1"
        assert d4["noteNodeIdB"] == "N2"

    def test_workspace_document_schema_version_2(self):
        payload = WorkspacePayload(
            id="WS-TEST-1",
            name="Main Workspace",
            canvas_origin=[9800.0, 8500.0],
            canvas_zoom=1.0,
            nodes=[],
            connections=[],
        )
        doc = WorkspaceDocument(payload=payload)
        d = doc.to_dict()

        assert d["schemaVersion"] == 2
        assert d["type"] == "workspace"
        assert d["payload"]["canvasOrigin"] == [9800.0, 8500.0]
        assert d["payload"]["canvasZoom"] == 1.0

        # Verify json round-trip
        json_str = json.dumps(d)
        data = json.loads(json_str)
        doc_restored = WorkspaceDocument.from_dict(data)

        assert doc_restored.schema_version == 2
        assert doc_restored.payload.name == "Main Workspace"
        assert doc_restored.payload.canvas_origin == [9800.0, 8500.0]

    def test_iso8601_format(self):
        ts = current_iso8601()
        assert ts.endswith("Z")
        assert "." not in ts, "Timestamps should not have fractional seconds per schema spec"
        assert len(ts) == 20  # YYYY-MM-DDTHH:MM:SSZ
