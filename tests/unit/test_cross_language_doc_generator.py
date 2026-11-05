"""
Generates a complete schemaVersion: 2 Workspace Document containing:
- 1 terminal node
- 1 stickyNote node
- 1 portal node
- 1 fileTree node
- 1 of each connection kind (terminal, note, portal, portal-to-portal, note-to-note)

Used for cross-language validation with the Swift OpenMaestriCore decoder.
"""

import json
from pathlib import Path
from maestri_core.models.workspace import WorkspaceDocument, WorkspacePayload
from maestri_core.models.node import CanvasNode, NodeFrame, TerminalContent, StickyNoteContent, PortalContent, FileTreeContent
from maestri_core.models.connection import Connection


FIXED_TIMESTAMP = "2026-11-05T20:27:31Z"


def generate_full_test_document() -> WorkspaceDocument:
    term_node = CanvasNode(
        id="11111111-1111-1111-1111-111111111111",
        frame=NodeFrame(x=100.0, y=100.0, width=400.0, height=300.0),
        node_type="terminal",
        created_at=FIXED_TIMESTAMP,
        last_modified_at=FIXED_TIMESTAMP,
        content=TerminalContent(
            name="Main Terminal",
            agent_type="generic_shell",
            command="bash",
            working_directory="/tmp",
            terminal_id="11111111-1111-1111-1111-111111111111",
            scrollback_line_count=42,
        ),
    )

    note_node = CanvasNode(
        id="22222222-2222-2222-2222-222222222222",
        frame=NodeFrame(x=600.0, y=100.0, width=300.0, height=200.0),
        node_type="stickyNote",
        created_at=FIXED_TIMESTAMP,
        last_modified_at=FIXED_TIMESTAMP,
        content=StickyNoteContent(
            name="QuickNote",
            file_name="QuickNote.md",
            color="yellow",
            font_size=14,
        ),
    )

    portal_node = CanvasNode(
        id="33333333-3333-3333-3333-333333333333",
        frame=NodeFrame(x=100.0, y=500.0, width=500.0, height=400.0),
        node_type="portal",
        created_at=FIXED_TIMESTAMP,
        last_modified_at=FIXED_TIMESTAMP,
        content=PortalContent(
            name="Web Preview",
            current_url="https://github.com",
            portal_id="33333333-3333-3333-3333-333333333333",
        ),
    )

    portal_node_2 = CanvasNode(
        id="44444444-4444-4444-4444-444444444444",
        frame=NodeFrame(x=700.0, y=500.0, width=500.0, height=400.0),
        node_type="portal",
        created_at=FIXED_TIMESTAMP,
        last_modified_at=FIXED_TIMESTAMP,
        content=PortalContent(
            name="Docs Preview",
            current_url="https://docs.swift.org",
            portal_id="44444444-4444-4444-4444-444444444444",
        ),
    )

    file_tree_node = CanvasNode(
        id="55555555-5555-5555-5555-555555555555",
        frame=NodeFrame(x=1000.0, y=100.0, width=250.0, height=500.0),
        node_type="fileTree",
        created_at=FIXED_TIMESTAMP,
        last_modified_at=FIXED_TIMESTAMP,
        content=FileTreeContent(
            name="Project Files",
            root_path="/tmp",
            view_mode="list",
        ),
    )

    # 1 of each connection kind
    c_term = Connection(
        id="AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA",
        a_id="11111111-1111-1111-1111-111111111111",
        b_id="11111111-1111-1111-1111-111111111111",
        kind="terminal",
        rope_points=[[100.0, 100.0], [200.0, 200.0]],
        created_at=FIXED_TIMESTAMP,
    )
    c_note = Connection(
        id="BBBBBBBB-BBBB-BBBB-BBBB-BBBBBBBBBBBB",
        a_id="11111111-1111-1111-1111-111111111111",
        b_id="22222222-2222-2222-2222-222222222222",
        kind="terminalToNote",
        created_at=FIXED_TIMESTAMP,
    )
    c_portal = Connection(
        id="CCCCCCCC-CCCC-CCCC-CCCC-CCCCCCCCCCCC",
        a_id="11111111-1111-1111-1111-111111111111",
        b_id="33333333-3333-3333-3333-333333333333",
        kind="terminalToPortal",
        created_at=FIXED_TIMESTAMP,
    )
    c_p2p = Connection(
        id="DDDDDDDD-DDDD-DDDD-DDDD-DDDDDDDDDDDD",
        a_id="33333333-3333-3333-3333-333333333333",
        b_id="44444444-4444-4444-4444-444444444444",
        kind="portalToPortal",
        created_at=FIXED_TIMESTAMP,
    )
    c_n2n = Connection(
        id="EEEEEEEE-EEEE-EEEE-EEEE-EEEEEEEEEEEE",
        a_id="22222222-2222-2222-2222-222222222222",
        b_id="22222222-2222-2222-2222-222222222222",
        kind="noteToNote",
        created_at=FIXED_TIMESTAMP,
    )

    payload = WorkspacePayload(
        id="99999999-9999-9999-9999-999999999999",
        name="Cross-Language Parity Workspace",
        working_directory="/tmp",
        canvas_origin=[9800.0, 8500.0],
        canvas_zoom=1.0,
        nodes=[term_node, note_node, portal_node, portal_node_2, file_tree_node],
        connections=[c_term],
        note_connections=[c_note],
        portal_connections=[c_portal],
        portal_to_portal_connections=[c_p2p],
        note_to_note_connections=[c_n2n],
        created_at=FIXED_TIMESTAMP,
        last_modified_at=FIXED_TIMESTAMP,
    )

    return WorkspaceDocument(payload=payload)


def test_generate_and_save_fixture():
    doc = generate_full_test_document()
    doc_dict = doc.to_dict()

    fixture_dir = Path("Tests/OpenMaestriTests/Fixtures")
    fixture_dir.mkdir(parents=True, exist_ok=True)
    out_file = fixture_dir / "PythonGeneratedWorkspace.json"

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(doc_dict, f, indent=2)

    assert out_file.exists()
    assert doc_dict["schemaVersion"] == 2
    assert "color" not in doc_dict["payload"]
    assert "type" not in doc_dict["payload"]["nodes"][0]
    assert doc_dict["payload"]["nodes"][0]["content"]["terminal"]["_0"]["scrollbackLineCount"] == 42
    assert doc_dict["payload"]["connections"][0]["createdAt"] != ""
