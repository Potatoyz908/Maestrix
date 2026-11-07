"""Editable role and agent libraries, using the macOS Preferences Codable fields."""
from __future__ import annotations

import uuid
from gi.repository import Gtk
from maestri_core.models.node import agent_type_for_command
from .terminal_dialog import PRESETS


def default_agents():
    return [dict(id=str(uuid.uuid5(uuid.NAMESPACE_URL, 'open-maestri:preset:' + p['id'])).upper(),
                 name=p['name'], command=p['command'], icon=p['sf_icon'], color=p['color'],
                 agentType=p['agent_type'], isActive=True, isBuiltIn=True) for p in PRESETS]


class AgentsPage(Gtk.ScrolledWindow):
    def __init__(self, prefs, on_changed):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.prefs, self.on_changed = prefs, on_changed
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                           margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
        self.set_child(self.box)
        self.lists = {}
        for key, title in [('rolePresets', 'Roles'), ('agentPresets', 'Agent Presets')]:
            if not isinstance(prefs._extra.get(key), list):
                prefs._extra[key] = default_agents() if key == 'agentPresets' else []
            heading = Gtk.Box(spacing=8)
            heading.append(Gtk.Label(label=title, xalign=0, hexpand=True, css_classes=['heading']))
            add = Gtk.Button(icon_name='maestri-plus-symbolic', tooltip_text='Add Role' if key == 'rolePresets' else 'Add Agent Preset')
            add.connect('clicked', lambda _b, k=key: self.add_record(k))
            heading.append(add)
            self.box.append(heading)
            rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            self.lists[key] = rows
            self.box.append(rows)
            for record in prefs._extra[key]:
                if isinstance(record, dict):
                    self._row(key, record)

    def add_record(self, key):
        record = dict(id=str(uuid.uuid4()).upper(), name='New Role' if key == 'rolePresets' else 'New Agent',
                      icon='terminal', color='#007AFF')
        record.update(dict(prompt='') if key == 'rolePresets' else
                      dict(command='bash', agentType='generic_shell', isActive=True, isBuiltIn=False))
        self.prefs._extra[key].append(record)
        self._row(key, record)
        self.on_changed()

    def _row(self, key, record):
        grid = Gtk.Grid(column_spacing=8, row_spacing=6)
        fields = [('name', 'Name'), ('prompt', 'Prompt')] if key == 'rolePresets' else [('name', 'Name'), ('command', 'Command')]
        fields += [('icon', 'Icon'), ('color', 'Color')]
        for row, (field, title) in enumerate(fields):
            grid.attach(Gtk.Label(label=title, xalign=0), 0, row, 1, 1)
            if field == 'prompt':
                editor = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, height_request=80, hexpand=True)
                buf = editor.get_buffer()
                buf.set_text(str(record.get(field, '')))
                buf.connect('changed', lambda b, r=record: self._change(r, 'prompt', b.get_text(b.get_start_iter(), b.get_end_iter(), False)))
            else:
                editor = Gtk.Entry(text=str(record.get(field, '')), hexpand=True)
                editor.connect('changed', lambda e, f=field, r=record: self._change(r, f, e.get_text()))
            grid.attach(editor, 1, row, 1, 1)
        if key == 'agentPresets':
            active = Gtk.CheckButton(label='Show in Quick Start', active=bool(record.get('isActive', True)))
            active.connect('toggled', lambda b: self._change(record, 'isActive', b.get_active()))
            grid.attach(active, 1, len(fields), 1, 1)
        delete = Gtk.Button(icon_name='maestri-trash-symbolic', tooltip_text='Delete', valign=Gtk.Align.START)
        delete.connect('clicked', lambda _b: self._delete(key, record, grid))
        grid.attach(delete, 2, 0, 1, 1)
        self.lists[key].append(grid)

    def _change(self, record, field, value):
        record[field] = value
        if field == 'command':
            record['agentType'] = agent_type_for_command(value)
        self.on_changed()

    def _delete(self, key, record, grid):
        self.prefs._extra[key].remove(record)
        self.lists[key].remove(grid)
        self.on_changed()
