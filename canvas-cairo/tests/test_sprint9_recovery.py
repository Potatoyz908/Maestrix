"""Regression tests for recovered Sprint 9 actions; no display, server or user files.

Load individual window methods to exercise their model mutations without constructing
GTK widgets. Full signal/clipboard/WebKit integration belongs to the Monitor run.
"""
import ast
import os
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
import uuid
from unittest.mock import Mock

from maestri_core.models.node import CanvasNode, NodeFrame, TerminalContent, StickyNoteContent, PortalContent, FileTreeContent, agent_type_for_command
from maestri_canvas.geometry import Point
from maestri_core.element_defaults import apply_element_defaults
from maestri_canvas.note_model import unique_name

ROOT = Path(__file__).resolve().parents[2]


def method(path, cls, name, namespace):
    tree = ast.parse((ROOT / path).read_text())
    klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
    fn = next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[name]


class CreationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.create = method('gtk4-ui/src/ui/window.py', 'OpenMaestriWindow', '_create_node',
                             dict(globals(), WIDGET_KINDS={'terminal', 'stickyNote', 'portal', 'fileTree'}, bus=Mock()))
        self.win = NS(loaded=True, preferences=NS(_extra={}), document=NS(payload=NS(nodes=[], working_directory='/workspace')),
                      canvas_widget=NS(get_width=lambda: 1000, get_height=lambda: 800),
                      floors=NS(terminal_cwd=lambda: '/workspace'), _add_node_to_canvas=Mock())

    def test_supplied_content_survives_all_creation_paths(self):
        for kind, content in [('portal', PortalContent(current_url='https://example.org/path')),
                              ('stickyNote', StickyNoteContent(name='Dropped', storage_mode={'custom': {'path': '/tmp/custom.md'}})),
                              ('fileTree', FileTreeContent(root_path='/tmp/tree')),
                              ('terminal', TerminalContent(command='codex'))]:
            with self.subTest(kind=kind):
                self.create(self.win, kind, at=Point(10, 20), content=content)
                node = self.win.document.payload.nodes[-1]
                self.assertIs(node.content, content)
                if kind == 'terminal':
                    self.assertEqual(node.id, content.terminal_id)
                    self.assertEqual(node.id, content.id)
                    self.assertEqual(content.agent_type, 'codex')

    def test_default_note_names_and_tree_root(self):
        self.create(self.win, 'stickyNote', at=Point(0, 0))
        self.create(self.win, 'stickyNote', at=Point(0, 0))
        a, b = self.win.document.payload.nodes
        self.assertNotEqual(a.content.file_name, b.content.file_name)
        self.create(self.win, 'fileTree', at=Point(0, 0))
        self.assertEqual(self.win.document.payload.nodes[-1].content.root_path, '/workspace')


class NoteConflictRecoveryTests(unittest.TestCase):
    def test_conflict_pauses_timer_flush_and_followup_writes(self):
        from maestri_canvas.note_saver import NoteSaver
        writes = []
        callbacks = []
        saver = NoteSaver(lambda text, done: (writes.append(text), callbacks.append(done)),
                          set_timer=lambda fn, ms: fn, clear_timer=lambda timer: None)
        saver.schedule('first')
        saver.flush()
        saver.schedule('local edit')
        saver.pause()
        callbacks.pop()(None)  # a write already in flight must not start the pending one
        saver.flush()
        self.assertEqual(writes, ['first'])
        self.assertTrue(saver.dirty)
        saver.schedule('newest local edit')
        saver._fire()
        self.assertEqual(writes, ['first'])
        saver.resume()  # explicit Keep My Edits
        self.assertEqual(writes, ['first', 'newest local edit'])

    def test_load_disk_discards_pending_save(self):
        from maestri_canvas.note_saver import NoteSaver
        write = Mock()
        saver = NoteSaver(write, set_timer=lambda fn, ms: fn, clear_timer=lambda timer: None)
        saver.schedule('local')
        saver.pause()
        saver.cancel()
        saver.resume()
        write.assert_not_called()

    def test_locked_note_never_reaches_file_deletion(self):
        remove = method('canvas-cairo/maestri_canvas/widget.py', 'CanvasWidget', 'request_delete',
                        dict(globals(), StickyNoteView=object, norm=lambda n: n))
        win = NS(scene=NS(get=lambda nid: NS(locked=True)), frames={}, emit=Mock())
        remove(win, ['locked-note'])
        win.emit.assert_not_called()


class PresetRecoveryTests(unittest.TestCase):
    def test_custom_active_presets_are_consumed_by_dialog(self):
        tree = ast.parse((ROOT / 'gtk4-ui/src/ui/terminal_dialog.py').read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'agent_presets')
        ns = dict(PRESETS=[{'id': 'default'}])
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'terminal_dialog.py', 'exec'), ns)
        prefs = NS(_extra={'agentPresets': [dict(id='A', name='Custom', command='codex --resume', icon='brain'),
                                          dict(id='B', isActive=False)]})
        presets = ns['agent_presets'](NS(preferences=prefs))
        self.assertEqual([p['id'] for p in presets], ['A'])
        self.assertEqual(presets[0]['command'], 'codex --resume')
        self.assertEqual(presets[0]['sf_icon'], 'brain')
        prefs._extra['agentPresets'] = []
        self.assertEqual(ns['agent_presets'](NS(preferences=prefs)), [])


class TerminalActivityRecoveryTests(unittest.TestCase):
    def test_rings_count_only_observed_busy_terminals(self):
        tree = ast.parse((ROOT / 'gtk4-ui/src/ui/floating_bars.py').read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'terminal_activity_counts')
        ns = {}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'floating_bars.py', 'exec'), ns)
        nodes = [NS(node_type='terminal', id='A', content=TerminalContent(command='codex')),
                 NS(node_type='terminal', id='B', content=TerminalContent(command='codex')),
                 NS(node_type='terminal', id='C', content=TerminalContent(command='claude'))]
        frames = {'A': NS(terminal_features=NS(busy=True, exited=None)),
                  'B': NS(terminal_features=NS(busy=True, exited=0))}
        counts = ns['terminal_activity_counts'](nodes, frames)
        self.assertEqual(counts, {'claude': [0, 1], 'codex': [1, 2], 'gemini': [0, 0]})
        self.assertEqual(ns['terminal_activity_counts']([], {}), {'claude': [0, 0], 'codex': [0, 0], 'gemini': [0, 0]})

    def test_exit_event_uses_resolved_legacy_node_id(self):
        state = method('gtk4-ui/src/ui/live_events.py', 'LiveEvents', 'terminal_state',
                       dict(globals(), Dict=dict, Any=object, norm=lambda n: n))
        cw = NS(frames={'NODE': NS(terminal_features=Mock())}, set_terminal_exited=Mock())
        live = NS(node_id_for=lambda tid: 'NODE' if tid == 'LEGACY' else None, win=NS(canvas_widget=cw))
        self.assertTrue(state(live, dict(type='terminalExited', terminalId='LEGACY', exitCode=7)))
        cw.set_terminal_exited.assert_called_once_with('NODE', 7)
        self.assertFalse(state(live, dict(type='terminalExited', terminalId='unknown')))


class WorkspaceBridgeRecoveryTests(unittest.TestCase):
    def test_note_requests_pin_original_workspace(self):
        import urllib.parse
        url = method('canvas-cairo/maestri_canvas/note_storage.py', 'CoreNotesClient', '_url', dict(urllib=urllib))
        client = NS(base='http://localhost:9999', workspace_id='FIRST')
        self.assertEqual(url(client, 'Daily Note'), 'http://localhost:9999/api/notes/Daily%20Note?workspace=FIRST')

    def test_portal_resolves_persisted_content_id(self):
        view_for = method('canvas-cairo/maestri_canvas/portal_bridge.py', 'PortalBridge', 'view_for',
                          dict(Any=object, norm=lambda v: v.upper()))
        content = NS(meta=NS(portal_id='PORTAL-ID'), web=object())
        bridge = NS(cw=NS(frames={'NODE-ID': NS(content=content)}))
        self.assertIs(view_for(bridge, 'portal-id'), content)
        self.assertIs(view_for(bridge, 'node-id'), content)
        self.assertIsNone(view_for(bridge, 'unknown'))


@unittest.skipUnless(os.environ.get('DISPLAY') == ':99', 'GTK unit checks require isolated Xvfb :99')
class GtkRecoveryTests(unittest.TestCase):
    def test_settings_agents_and_terminal_dialog_construct(self):
        import sys
        sys.path.insert(0, str(ROOT / 'gtk4-ui/src'))
        from maestri_canvas.gi_env import ensure_typelib_paths
        ensure_typelib_paths()
        import gi
        gi.require_version('Gtk', '4.0')
        from gi.repository import Gtk
        from maestri_core.preferences import PreferencesStore
        from ui.settings_window import SettingsWindow
        from ui.terminal_dialog import NewTerminalDialog
        if not Gtk.init_check():
            self.skipTest("Xvfb :99 is not accepting GTK connections")
        parent = Gtk.Window()
        store = PreferencesStore(Path(os.environ['OPEN_MAESTRI_DATA_DIR']))
        parent.preferences = store.load()
        settings = SettingsWindow(parent, store=store)
        self.assertIn('agents', settings.pages)
        settings.agents_page.add_record('rolePresets')
        settings.agents_page.add_record('agentPresets')
        parent.preferences = store.load()
        self.assertEqual(len(parent.preferences._extra['rolePresets']), 1)
        self.assertEqual(len(parent.preferences._extra['agentPresets']), 6)
        dialog = NewTerminalDialog(parent, on_create=lambda data: None, default_cwd='/tmp')
        self.assertEqual(len(dialog.presets), 6)
        self.assertIn('maestro', dialog._common_data())
        dialog.close()
        settings.close()
        parent.destroy()


class RoutineRecoveryTests(unittest.TestCase):
    def test_remote_routine_never_uses_current_canvas_terminals(self):
        run = method('gtk4-ui/src/ui/routines_window.py', 'RoutinesController', 'run_now', dict(globals()))
        requests = []
        controller = NS(store=NS(run_now=lambda rid: requests.append(rid)), _remote_runs=set(),
                        scheduler=NS(routines={'OTHER-WORKSPACE': object()}, run_now=Mock()), _changed=Mock())
        pending = []
        controller._async = lambda work, done, finished: pending.append((work, done, finished))
        self.assertTrue(run(controller, 'OTHER-WORKSPACE'))
        self.assertFalse(run(controller, 'OTHER-WORKSPACE'))  # do not overlap
        work, done, finished = pending.pop()
        done(work())
        finished()
        self.assertEqual(requests, ['OTHER-WORKSPACE'])
        self.assertFalse(controller._remote_runs)
        controller.scheduler.run_now.assert_not_called()


class ElementDefaultsRecoveryTests(unittest.TestCase):
    def test_defaults_apply_only_when_requested(self):
        prefs = NS(_extra={'newElementDefaults': {'terminal': {'size': [640, 480], 'appearance': {'font_size': 17}}}})
        node = CanvasNode(frame=NodeFrame(width=420, height=300), content=TerminalContent(), node_type='terminal')
        apply_element_defaults(node, prefs, size=False)
        self.assertEqual(node.frame.width, 420)
        self.assertEqual(node.content.font_size, 17)
        apply_element_defaults(node, prefs)
        self.assertEqual((node.frame.width, node.frame.height), (640, 480))
        prefs._extra['newElementDefaults']['terminal']['size'] = [float('nan'), -1]
        apply_element_defaults(node, prefs)
        self.assertEqual((node.frame.width, node.frame.height), (640, 480))


class ClipboardRecoveryTests(unittest.TestCase):
    def test_pasted_note_gets_independent_storage_and_text(self):
        import json
        from typing import List, Optional
        from maestri_core.models.connection import Connection
        source = CanvasNode(node_type='stickyNote', content=StickyNoteContent(name='Note', file_name='Note.md'), frame=NodeFrame())
        paste = method('gtk4-ui/src/ui/window_actions.py', 'WindowActionsMixin', '_paste_payload',
                       dict(globals(), json=json, List=List, Optional=Optional, Connection=Connection, PASTE_OFFSET=24, bus=Mock()))
        win = NS(document=NS(payload=NS(nodes=[source])), canvas_widget=NS(controller=NS(dirty=False)),
                 _add_node_to_canvas=Mock(), _fill_note_when_loaded=Mock(), sync=None)
        result = paste(win, json.dumps({'nodes': [source.to_dict()], 'noteTexts': {source.id: 'Keep this content'}}), None)
        self.assertNotEqual(result[0].content.file_name, source.content.file_name)
        self.assertEqual(result[0].content.storage_mode, {'managed': {}})
        win._fill_note_when_loaded.assert_called_once_with(result[0].id, 'Keep this content')
