"""Header economy button: a leaf (grey = off, green = on) whose dropdown holds the agent economy mode and the agent language,
each with what it does. Same preferences as Settings > General (agentEconomyMode / agentCompactLanguage); the core reads them
on every ask, so a change applies at once to running agents."""
from __future__ import annotations

import dataclasses
from typing import Callable

from gi.repository import Gtk

from maestri_core.preferences import (AGENT_LANGUAGE_HELP, AGENT_LANGUAGE_LABELS, AGENT_LANGUAGES, ECONOMY_HELP, Preferences,
                                      agent_language, compact_for)

from .iconography import icon_name

CSS = """
menubutton.maestri-eco-btn > button { min-height: 28px; padding: 0 6px; border-radius: 8px; color: #8a93ab;
  background: transparent; background-image: none; box-shadow: none; border: none; }
menubutton.maestri-eco-btn.on > button { color: #34c759; background-color: rgba(52, 199, 89, 0.14); }
menubutton.maestri-eco-btn.maestri-eco-btn.maestri-eco-btn > button:checked { background-image: none; background-color: rgba(52, 199, 89, 0.2); }
.maestri-eco-pop { padding: 12px; }
.maestri-eco-help { font-size: 11px; opacity: 0.75; }
"""


class EcoButton(Gtk.MenuButton):
    def __init__(self, prefs: Preferences, on_change: Callable[[Preferences], None]) -> None:
        super().__init__(valign=Gtk.Align.CENTER, css_classes=["maestri-eco-btn"])
        self._on_change = on_change
        self._prefs = prefs
        self._syncing = False
        box = Gtk.Box(spacing=4)
        box.append(Gtk.Image.new_from_icon_name(icon_name("leaf")))
        box.append(Gtk.Image.new_from_icon_name(icon_name("chevron-down")))
        self.set_child(box)

        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["maestri-eco-pop"], width_request=280)
        row = Gtk.Box(spacing=12)
        row.append(Gtk.Label(label="Economy mode", xalign=0, hexpand=True, css_classes=["heading"]))
        self.switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.switch.connect("notify::active", lambda s, _p: self._changed())
        row.append(self.switch)
        v.append(row)
        v.append(self._help(ECONOMY_HELP))

        self.lang_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        lrow = Gtk.Box(spacing=12)
        lrow.append(Gtk.Label(label="Agent language", xalign=0, hexpand=True))
        self.lang = Gtk.DropDown.new_from_strings([AGENT_LANGUAGE_LABELS[k] for k in AGENT_LANGUAGES])
        self.lang.connect("notify::selected", lambda *_: self._changed())
        lrow.append(self.lang)
        self.lang_box.append(Gtk.Separator())
        self.lang_box.append(lrow)
        self.lang_help = self._help("")
        self.lang_box.append(self.lang_help)
        v.append(self.lang_box)
        self.set_popover(Gtk.Popover(child=v))

        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        display = self.get_display()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 30)   # above the dark theme (USER + 20)
        self.sync(prefs)

    @staticmethod
    def _help(text: str) -> Gtk.Label:
        return Gtk.Label(label=text, xalign=0, wrap=True, max_width_chars=38, css_classes=["dim-label", "maestri-eco-help"])

    def sync(self, prefs: Preferences) -> None:
        """Show `prefs` (initial state, or a change made in Settings)."""
        self._prefs = prefs
        self._syncing = True
        self.switch.set_active(prefs.agent_economy_mode)
        self.lang.set_selected(AGENT_LANGUAGES.index(agent_language(prefs.agent_compact_language)))
        self._syncing = False
        self._paint()

    def _paint(self) -> None:
        on = self._prefs.agent_economy_mode
        lang = agent_language(self._prefs.agent_compact_language)
        self.lang_box.set_visible(on)
        self.lang_help.set_label(AGENT_LANGUAGE_HELP[lang])
        if on:
            self.add_css_class("on")
            self.set_tooltip_text(f"Economy mode on · {AGENT_LANGUAGE_LABELS[lang]}")
        else:
            self.remove_css_class("on")
            self.set_tooltip_text("Economy mode off")

    def _changed(self) -> None:
        if self._syncing:
            return
        lang = AGENT_LANGUAGES[min(self.lang.get_selected(), len(AGENT_LANGUAGES) - 1)]
        self._prefs = dataclasses.replace(self._prefs, agent_economy_mode=self.switch.get_active(),
                                          agent_compact_language=compact_for(lang))
        self._paint()
        self._on_change(self._prefs)
