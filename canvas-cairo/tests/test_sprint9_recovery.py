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
        self.win = NS(loaded=True, document=NS(payload=NS(nodes=[], working_directory='/workspace')),
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
