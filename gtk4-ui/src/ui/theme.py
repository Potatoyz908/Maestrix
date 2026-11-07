"""Live, application-local interface palette inspired by the Maestrix icon."""
from gi.repository import Gtk, Gdk

BLUE = '#168bf1'
PURPLE = '#9354e8'
CYAN = '#71dcec'

DARK_CSS = """
window { background: #0c0f1b; color: #edf1ff; }
headerbar, notebook, notebook > stack, notebook > header,
.maestri-sidebar, .maestri-modal-window {
  background: #151c2e; color: #edf1ff; border-color: #303956;
}
.maestri-workspace-card, .maestri-floor-card, .maestri-agent-card,
.maestri-floating-pill, .maestri-floating-sidebar-btn {
  background: #1c263c; color: #edf1ff; border-color: #344161;
}
.maestri-card-title, .maestri-group-title, .maestri-floor-name,
.maestri-agent-name, .maestri-island-btn, .maestri-header-btn,
.maestri-sidebar-add-btn, .maestri-search-entry text,
.maestri-sidebar-menubar > item > label,
.maestri-header-menubar > item > label {
  color: #edf1ff;
}
.maestri-card-path, .maestri-agent-role, .maestri-sidebar-header,
.maestri-group-chevron { color: #a9b7d5; }
entry, searchentry, spinbutton, dropdown > button, textview, textview text {
  background: #101729; color: #edf1ff; border-color: #384866;
}
button { color: #edf1ff; }
button:not(.flat):not(.dot-close):not(.dot-min):not(.dot-max) {
  background-color: #24314b; background-image: none;
}
popover { background: transparent; border: none; box-shadow: none; }
popover > contents, .maestri-sidebar-menubar popover > contents,
.maestri-header-menubar popover > contents {
  background: #182239; color: #edf1ff; border: 1px solid #384866;
  box-shadow: 0 2px 8px rgba(0,0,0,0.18);
}
popover modelbutton label, .maestri-sidebar-menubar popover modelbutton label,
.maestri-header-menubar popover modelbutton label { color: #edf1ff; }
.maestri-workspace-card:hover { border-color: #71dcec; }
button.suggested-action, button:checked:not(.dot-close):not(.dot-min):not(.dot-max),
switch:checked, notebook > header tab:checked,
.maestri-menu-item-btn:hover:not(.danger), modelbutton:hover {
  background-image: linear-gradient(to right, #168bf1, #9354e8);
  background-color: #414bc5; color: white;
}
entry:focus, searchentry:focus-within { border-color: #71dcec; }
paned > separator { background-color: #303956; background-image: none; min-width: 1px; min-height: 1px; }
separator { background-color: #303956; }
notebook > header tab { color: #a9b7d5; }
notebook > header tab:checked, notebook > header tab:checked label { color: white; }
notebook > header tab:hover:not(:checked) label { color: #71dcec; }
switch { background-color: #24314b; background-image: none; border-color: #384866; }
switch slider { background-color: #edf1ff; }
scale trough, progressbar trough { background-color: #24314b; }
tooltip, tooltip.background { background-color: #182239; color: #edf1ff; border: 1px solid #384866; }
/* The two top islands: one uniform navy pill, icons with no button background, the active tool marked by the icon gradient. */
.maestri-top-toolbar, .maestri-context-actions-pill {
  background-color: rgba(21, 28, 46, 0.94); background-image: none; border: 1px solid #344161;
}
.maestri-top-toolbar button.maestri-island-btn.maestri-island-btn.maestri-island-btn, .maestri-context-actions-pill button.maestri-island-btn.maestri-island-btn.maestri-island-btn {
  background-color: transparent; background-image: none; border: none; box-shadow: none; color: #c4cff0;
}
.maestri-top-toolbar button.maestri-island-btn.maestri-island-btn.maestri-island-btn image, .maestri-context-actions-pill button.maestri-island-btn.maestri-island-btn.maestri-island-btn image {
  background: none; background-color: transparent; background-image: none;
}
.maestri-top-toolbar button.maestri-island-btn.maestri-island-btn.maestri-island-btn:hover, .maestri-context-actions-pill button.maestri-island-btn.maestri-island-btn.maestri-island-btn:hover {
  background-color: rgba(113, 220, 236, 0.10); background-image: none; color: #ffffff;
}
.maestri-top-toolbar button.maestri-island-btn.maestri-island-btn.maestri-island-btn.active, .maestri-context-actions-pill button.maestri-island-btn.maestri-island-btn.maestri-island-btn.active {
  background-image: linear-gradient(135deg, rgba(22, 139, 241, 0.30), rgba(147, 84, 232, 0.30));
  background-color: transparent; color: #71dcec; box-shadow: inset 0 0 0 1px rgba(113, 220, 236, 0.55);
}
.maestri-context-actions-pill button.maestri-island-btn.maestri-island-btn.maestri-island-btn.danger:hover { background-color: rgba(255, 69, 58, 0.14); color: #ff746f; }
.maestri-menu-item-btn.danger { color: #ff746f; }
.maestri-menu-item-btn.danger:hover { background: #a62937; color: white; }
"""


class InterfaceTheme:
    def __init__(self):
        self.provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self.provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 20)

    def apply(self, name):
        # Loading into the same provider replaces rules, so switching to Light restores the base theme.
        self.provider.load_from_string(DARK_CSS if name == 'dark' else '')
