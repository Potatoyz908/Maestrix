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
.maestri-card-badge { background-color: rgba(255, 255, 255, 0.06); }
.maestri-card-badge, .maestri-card-badge label, .maestri-card-badge image { color: #8a93ab; }
row:selected .maestri-card-badge { background-color: rgba(255, 255, 255, 0.14); }
row:selected .maestri-card-badge, row:selected .maestri-card-badge label, row:selected .maestri-card-badge image { color: #ffffff; }
.maestri-card-badge.unread, row:selected .maestri-card-badge.unread { background-color: rgba(10, 132, 255, 0.45); }
.maestri-workspace-card:hover { border-color: #71dcec; }
/* The open workspace: accent bar on the left + tinted card + cyan border. */
row:selected .maestri-workspace-card { background: #213556; border-color: #71dcec; box-shadow: inset 4px 0 0 #71dcec; }
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
/* The two top islands: one uniform navy pill, icons with no button background, the active tool marked by its icon colour. */
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
  background-color: transparent; background-image: none; box-shadow: none; color: #71dcec;   /* the icon colour is the marker */
}
.maestri-context-actions-pill button.maestri-island-btn.maestri-island-btn.maestri-island-btn.danger:hover { background-color: rgba(255, 69, 58, 0.14); color: #ff746f; }
/* Draw tool = Gtk.MenuButton: same island look on its inner button. */
.maestri-top-toolbar menubutton.maestri-island-btn.maestri-island-btn > button {
  background-color: transparent; background-image: none; border: none; box-shadow: none; color: #c4cff0;
}
.maestri-top-toolbar menubutton.maestri-island-btn.maestri-island-btn > button:hover, .maestri-top-toolbar menubutton.maestri-island-btn.maestri-island-btn > button:checked {
  background-color: rgba(113, 220, 236, 0.10); background-image: none; color: #ffffff;
}
.maestri-top-toolbar menubutton.maestri-island-btn.maestri-island-btn.active > button { background-color: transparent; color: #71dcec; }
/* Sheets / dialogs (New Workspace, Edit Workspace, New Terminal...): they hard-code light surfaces in window.py. */
.maestri-modal-window, .maestri-modal-dialog, .maestri-sheet-root { background-color: #151c2e; color: #edf1ff; }
.maestri-sheet-header { background-color: #151c2e; background-image: none; border-bottom: 1px solid #303956; }
.maestri-sheet-title, .maestri-dialog-title { color: #edf1ff; }
.maestri-form-section-title, .maestri-section-label, .maestri-form-label { color: #8fa0c8; }
.maestri-text-input, .maestri-form-entry { background-color: #101729; color: #edf1ff; border: 1px solid #384866; }
.maestri-text-input:focus, .maestri-form-entry:focus { border-color: #71dcec; box-shadow: none; }
.maestri-dir-row, .maestri-sync-row { background-color: #101729; background-image: none; border: 1px solid #384866; }
.maestri-dir-path { color: #a9b7d5; }
.maestri-dialog-btn { background-color: #24314b; background-image: none; color: #edf1ff; border: 1px solid #384866; }
.maestri-dialog-btn:hover { background-color: #2d3d5e; }
.maestri-dialog-btn-primary { background-image: linear-gradient(to right, #168bf1, #9354e8); background-color: #414bc5; color: white; border: none; }
.maestri-dialog-btn-danger { background-color: #a62937; background-image: none; color: white; border: none; }
/* Workspace colour dots: the generic dark button rule above would paint them all navy, so restate each colour here (extra repeated class = specificity above the :not() chain). */
button.maestri-color-dot.maestri-color-dot.maestri-color-dot { background-image: none; border: 2.5px solid transparent; box-shadow: none; }
button.maestri-color-dot.maestri-color-dot.maestri-color-dot.color-blue { background-color: #007AFF; }
button.maestri-color-dot.maestri-color-dot.maestri-color-dot.color-red { background-color: #FF3B30; }
button.maestri-color-dot.maestri-color-dot.maestri-color-dot.color-green { background-color: #34C759; }
button.maestri-color-dot.maestri-color-dot.maestri-color-dot.color-orange { background-color: #FF9500; }
button.maestri-color-dot.maestri-color-dot.maestri-color-dot.color-purple { background-color: #AF52DE; }
button.maestri-color-dot.maestri-color-dot.maestri-color-dot.color-pink { background-color: #FF2D55; }
button.maestri-color-dot.maestri-color-dot.maestri-color-dot.color-cyan { background-color: #32ADE6; }
button.maestri-color-dot.maestri-color-dot.maestri-color-dot.color-yellow { background-color: #FFCC00; }
button.maestri-color-dot.maestri-color-dot.maestri-color-dot.color-rainbow { background-color: transparent;
  background-image: linear-gradient(135deg, #ff3b30, #ff9500, #ffcc00, #34c759, #007aff, #af52de); }
button.maestri-color-dot.maestri-color-dot.maestri-color-dot.active { border-color: #edf1ff; box-shadow: 0 0 0 2px #151c2e; }
.maestri-icon-btn { color: #c4cff0; }
.maestri-icon-btn:hover { background-color: rgba(113, 220, 236, 0.10); }
.maestri-icon-btn.active { background-color: rgba(22, 139, 241, 0.18); color: #71dcec; box-shadow: inset 0 0 0 1.5px #71dcec; }
.maestri-preset-btn { background-color: #1c263c; color: #edf1ff; border: 1px solid #344161; }
.maestri-preset-btn:hover { background-color: #24314b; }
.maestri-preset-btn.active { background-color: rgba(22, 139, 241, 0.18); border-color: #71dcec; }
.maestri-preset-name { color: #edf1ff; }
.maestri-segmented-control { background-color: #101729; }
.maestri-segment-btn { color: #a9b7d5; background-color: transparent; background-image: none; }
.maestri-segment-btn.active { background-color: #2d3d5e; color: white; }
/* File Tree node */
.maestri-file-tree, .maestri-file-tree scrolledwindow, .maestri-file-tree-list, .maestri-file-grid,
.maestri-file-tree listview, .maestri-file-tree gridview, .maestri-file-tree-list > row, .maestri-file-grid > child {
  background-color: #101729; color: #edf1ff;
}
.maestri-file-tree .toolbar { background-color: #151c2e; border-bottom: 1px solid #303956; }
.maestri-file-tree .dim-label, .maestri-file-tree .caption { color: #8fa0c8; }
.maestri-file-tree-list > row:hover, .maestri-file-grid > child:hover { background-color: rgba(113, 220, 236, 0.08); }
.maestri-file-tree-list > row:selected, .maestri-file-grid > child:selected { background-color: rgba(22, 139, 241, 0.28); color: white; }
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
