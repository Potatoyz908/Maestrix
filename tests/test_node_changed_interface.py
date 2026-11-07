"""Server metadata updates use real GTK headers without starting a server or PTY."""
from types import SimpleNamespace as NS
from unittest.mock import Mock
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
from maestri_core.client.api import EventsWebSocketClient
from maestri_core.models.node import CanvasNode, TerminalContent
from maestri_core.signals.bus import bus
from ui.window import OpenMaestriWindow


def test_node_changed_updates_header_without_echo_and_ignores_other_workspace():
    Gtk.init()
    node = CanvasNode(content=TerminalContent(name='Before'))
    changed = CanvasNode.from_dict(node.to_dict())
    changed.content.name = 'Reviewer'
    changed.content.icon = 'sparkle'
    changed.content.color = '#007AFF'
    changed.content.assigned_role_id = 'ROLE-1'
    title = Gtk.Label(label='Before')
    header = Gtk.Box()
    header.append(title)
    frame = NS(title=title)
    item = NS(title='Before')
    canvas = NS(frames={node.id: frame}, scene=NS(get=lambda _: item), controller=NS(dirty=False), queue_draw=Mock())
    win = NS(_cleaned_up=False, loaded=True, document=NS(payload=NS(id='WORKSPACE', nodes=[node])),
             _find_node=lambda nid: node if nid == node.id else None, canvas_widget=canvas)
    event = dict(type='nodeChanged', workspaceId='OTHER', nodeId=node.id, node=changed.to_dict())
    apply = OpenMaestriWindow._on_server_node_changed
    listener = bus.connect('node-changed', lambda b, e: apply(win, b, e))
    echoes = Mock()
    echo_id = bus.connect('node-updated', echoes)
    stream = EventsWebSocketClient(host='localhost', port=1, auth_token='', signal_bus=bus, use_glib_idle=False)
    try:
        stream._dispatch_event(event)
        assert node.title == 'Before'
        canvas.queue_draw.assert_not_called()
        event['workspaceId'] = 'workspace'
        stream._dispatch_event(event)
        assert win.document.payload.nodes[0] is node
        assert node.content.assigned_role_id == 'ROLE-1'
        assert node.title == item.title == title.get_label() == 'Reviewer'
        assert frame._server_header_icon.get_icon_name() == 'maestri-sparkle-symbolic'
        assert b'rgb(0,122,255)' in frame._server_header_css.to_string().replace(' ', '').encode()
        stream._dispatch_event(event)
        assert header.get_first_child().get_next_sibling() is title
        echoes.assert_not_called()
    finally:
        bus.disconnect(listener)
        bus.disconnect(echo_id)
