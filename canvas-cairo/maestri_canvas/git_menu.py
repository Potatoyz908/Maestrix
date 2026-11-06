"""Branch indicator + Git operations menu for the FileTree toolbar (spec: docs/reference/maestri-reference-index.md §7).

    [branch-icon main ↑1 dirty-dot]  -> Commit… / Pull / Push / Fetch | Checkout ▸ / New Branch… / Merge ▸ | Stash / Stash Pop | Show Changes / Refresh

`GitBranchButton(root)` is self-contained: it reads `GitOps.status()` (60 s TTL cache shared by every tree on the canvas,
invalidated by each action), hides itself outside a repository, and runs every action through `GitOps` (Gio.Subprocess,
argv only, never blocks the main loop). Commit and New Branch ask for their text in a small popover anchored to the button.

Signals: `status-changed(GitStatus)`, `action-finished(GitResult)`, `diff-requested()`. `feedback` is a Gtk.Revealer with
the last result line (check icon + "pull: Already up to date.") for the host to place under its toolbar.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, GObject, Gtk, Pango

from maestri_core.gitops import GitOps, GitResult, GitStatus

# (label, action) sections of the menu. Checkout/Merge submenus are filled with the branch list when the menu opens.
SIMPLE_ACTIONS = ("pull", "push", "fetch", "stash", "stash-pop")
FEEDBACK_SECONDS = 8


class GitBranchButton(Gtk.Box):
    __gtype_name__ = "MaestriGitBranchButton"
    __gsignals__ = {
        "status-changed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "action-finished": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "diff-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, root: str) -> None:
        super().__init__(spacing=2)
        self.add_css_class("maestri-git-branch")
        self.git = GitOps(root)
        self.status: GitStatus = GitStatus(False)
        self.branches: list[str] = []
        self.busy_action: str | None = None
        self._generation = 0          # drops results that belong to a previous root
        self._feedback_timer = 0

        # ---- menu model + actions ----
        self.checkout_menu = Gio.Menu()
        self.merge_menu = Gio.Menu()
        self.menu = self._build_menu()
        self.actions = Gio.SimpleActionGroup()
        for name in SIMPLE_ACTIONS:
            self._add_action(name, lambda _a, _p, n=name: self.run_action(n))
        self._add_action("commit", lambda *_: self.prompt("commit"))
        self._add_action("new-branch", lambda *_: self.prompt("new-branch"))
        self._add_action("show-diff", lambda *_: self.emit("diff-requested"))
        self._add_action("refresh", lambda *_: self.refresh(force=True))
        # stateful: state = current branch, so the Checkout submenu marks it with GTK's own radio indicator (no "✓" text)
        self._add_action("checkout", lambda _a, p: self.run_action("checkout", p.get_string()), GLib.VariantType.new("s"),
                         state=GLib.Variant.new_string(""))
        self._add_action("merge", lambda _a, p: self.run_action("merge", p.get_string()), GLib.VariantType.new("s"))
        self.insert_action_group("git", self.actions)

        # ---- widgets ----
        self.label = Gtk.Label(ellipsize=Pango.EllipsizeMode.END, max_width_chars=22)
        self.spinner = Gtk.Spinner(visible=False)
        content = Gtk.Box(spacing=4)
        content.append(Gtk.Image(icon_name="maestri-git-branch-symbolic"))   # outline branch icon (gtk4-ui/src/ui/icons)
        content.append(self.label)
        self.dirty_dot = Gtk.Box(valign=Gtk.Align.CENTER, visible=False, tooltip_text="Uncommitted changes")
        self.dirty_dot.add_css_class("maestri-git-dirty-dot")   # CSS-drawn, replaces the "●" glyph
        content.append(self.dirty_dot)
        content.append(self.spinner)
        self.menu_button = Gtk.MenuButton(menu_model=self.menu, tooltip_text="Git")
        self.menu_button.set_child(content)
        self.menu_button.add_css_class("flat")
        self.menu_button.get_popover().connect("show", lambda _p: self._load_branches())
        self.append(self.menu_button)

        # text input (commit message / new branch name)
        self.input_popover = Gtk.Popover()
        self.input_popover.set_parent(self.menu_button)
        box = Gtk.Box(spacing=6, margin_start=6, margin_end=6, margin_top=6, margin_bottom=6)
        self.input_entry = Gtk.Entry(width_chars=28, activates_default=True)
        self.input_ok = Gtk.Button(label="OK")
        self.input_ok.add_css_class("suggested-action")
        self.input_ok.connect("clicked", lambda _b: self._submit_input())
        self.input_entry.connect("activate", lambda _e: self._submit_input())
        self.input_entry.connect("changed", lambda _e: self._validate_input())
        box.append(self.input_entry)
        box.append(self.input_ok)
        self.input_popover.set_child(box)
        self._input_action = ""

        self.feedback_label = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, selectable=True,
                                        margin_start=8, margin_end=8, margin_top=2, margin_bottom=2)
        self.feedback = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, reveal_child=False)
        self.feedback_icon = Gtk.Image(margin_start=8, visible=False)   # check / warning outline instead of ✓ / ✗
        feedback_box = Gtk.Box()
        feedback_box.append(self.feedback_icon)
        feedback_box.append(self.feedback_label)
        self.feedback.set_child(feedback_box)

        self.set_visible(False)       # until status says this is a repository
        self.connect("destroy", lambda _w: self.input_popover.unparent())
        self.refresh()

    # ---- public API -------------------------------------------------------------------------------
    @property
    def root(self) -> str:
        return self.git.root

    def set_root(self, root: str) -> None:
        if root == self.git.root:
            return
        self._generation += 1
        self.git = GitOps(root, cache=self.git.cache)
        self.branches = []
        self.refresh()

    def refresh(self, force: bool = False) -> None:
        gen = self._generation
        self.git.status(lambda st: self._on_status(gen, st), force=force)

    def run_action(self, action: str, arg: str | None = None) -> None:
        if self.busy_action is not None:
            return
        self._set_busy(action)
        gen = self._generation
        self.git.run(action, lambda r: self._on_action_done(gen, r), arg)

    def prompt(self, action: str) -> None:
        """Ask for the commit message / new branch name, then run `action` with it."""
        self._input_action = action
        self.input_entry.set_text("")
        self.input_entry.set_placeholder_text("Commit message" if action == "commit" else "New branch name")
        self.input_ok.set_label("Commit" if action == "commit" else "Create")
        self._validate_input()
        self.input_popover.popup()
        self.input_entry.grab_focus()

    def menu_labels(self) -> list[str]:
        """Flattened labels of the menu (submenus included, prefixed with their parent): what the user sees."""
        out: list[str] = []

        def walk(model: Gio.MenuModel, prefix: str) -> None:
            for i in range(model.get_n_items()):
                label = model.get_item_attribute_value(i, "label", GLib.VariantType.new("s"))
                section = model.get_item_link(i, "section")
                sub = model.get_item_link(i, "submenu")
                if section is not None:
                    walk(section, prefix)
                elif label is not None:
                    out.append(prefix + label.get_string())
                    if sub is not None:
                        walk(sub, prefix + label.get_string() + "/")
        walk(self.menu, "")
        return out

    # ---- internals --------------------------------------------------------------------------------
    def _add_action(self, name: str, cb, param: GLib.VariantType | None = None, state: GLib.Variant | None = None) -> None:
        a = Gio.SimpleAction.new(name, param) if state is None else Gio.SimpleAction.new_stateful(name, param, state)
        a.connect("activate", cb)
        self.actions.add_action(a)

    def _build_menu(self) -> Gio.Menu:
        menu = Gio.Menu()
        sync = Gio.Menu()
        sync.append("Commit…", "git.commit")
        sync.append("Pull", "git.pull")
        sync.append("Push", "git.push")
        sync.append("Fetch", "git.fetch")
        menu.append_section(None, sync)
        branches = Gio.Menu()
        branches.append_submenu("Checkout", self.checkout_menu)
        branches.append("New Branch…", "git.new-branch")
        branches.append_submenu("Merge", self.merge_menu)
        menu.append_section(None, branches)
        stash = Gio.Menu()
        stash.append("Stash", "git.stash")
        stash.append("Stash Pop", "git.stash-pop")
        menu.append_section(None, stash)
        view = Gio.Menu()
        view.append("Show Changes", "git.show-diff")
        view.append("Refresh Status", "git.refresh")
        menu.append_section(None, view)
        return menu

    def _load_branches(self) -> None:
        gen = self._generation
        self.git.list_branches(lambda names: self._on_branches(gen, names))

    def _on_branches(self, gen: int, names: list[str]) -> None:
        if gen != self._generation:
            return
        self.branches = names
        current = self.status.branch
        self.actions.lookup_action("checkout").set_state(GLib.Variant.new_string(current or ""))
        self.checkout_menu.remove_all()
        self.merge_menu.remove_all()
        for name in names:
            item = Gio.MenuItem.new(name, None)
            item.set_action_and_target_value("git.checkout", GLib.Variant.new_string(name))
            self.checkout_menu.append_item(item)
            if name != current:
                m = Gio.MenuItem.new(name, None)
                m.set_action_and_target_value("git.merge", GLib.Variant.new_string(name))
                self.merge_menu.append_item(m)

    def _on_status(self, gen: int, st: GitStatus) -> None:
        if gen != self._generation:
            return
        self.status = st
        self.set_visible(st.is_repo)
        if st.is_repo:
            name = st.branch or "detached"
            extra = (f" ↑{st.ahead}" if st.ahead else "") + (f" ↓{st.behind}" if st.behind else "")
            self.label.set_label(name + extra)
            self.dirty_dot.set_visible(st.dirty)
            self.actions.lookup_action("checkout").set_state(GLib.Variant.new_string(st.branch or ""))
            tip = [f"Branch: {name}"]
            if st.upstream:
                tip.append(f"Upstream: {st.upstream} (ahead {st.ahead}, behind {st.behind})")
            tip.append(f"{len(st.changes)} changed path(s)" if st.dirty else "Working tree clean")
            self.menu_button.set_tooltip_text("\n".join(tip))
        self.emit("status-changed", st)

    def _set_busy(self, action: str | None) -> None:
        self.busy_action = action
        self.spinner.set_visible(action is not None)
        self.spinner.set_spinning(action is not None)
        self.menu_button.set_sensitive(action is None)
        if action is not None:
            self._show_feedback(f"git {action}…", None)

    def _on_action_done(self, gen: int, r: GitResult) -> None:
        self._set_busy(None)
        if gen != self._generation:
            return
        lines = [ln for ln in r.output.splitlines() if ln.strip()]
        detail = lines[-1] if lines else ("done" if r.ok else "failed")
        self._show_feedback(f"{r.action}: {detail}", r.ok)
        self.refresh(force=True)
        self.emit("action-finished", r)

    def _show_feedback(self, text: str, ok: bool | None) -> None:
        lbl = self.feedback_label
        lbl.set_label(text)
        self.feedback_icon.set_visible(ok is not None)
        if ok is not None:
            self.feedback_icon.set_from_icon_name("maestri-check-symbolic" if ok else "maestri-warning-symbolic")
        for c in ("success", "error", "dim-label"):
            lbl.remove_css_class(c)
        lbl.add_css_class("dim-label" if ok is None else "success" if ok else "error")
        self.feedback.set_reveal_child(True)
        if self._feedback_timer:
            GLib.source_remove(self._feedback_timer)
            self._feedback_timer = 0
        if ok:   # errors stay until the next action so they can be read (and copied: the label is selectable)
            self._feedback_timer = GLib.timeout_add_seconds(FEEDBACK_SECONDS, self._hide_feedback)

    def _hide_feedback(self) -> bool:
        self._feedback_timer = 0
        self.feedback.set_reveal_child(False)
        return GLib.SOURCE_REMOVE

    def _validate_input(self) -> None:
        text = self.input_entry.get_text()
        if self._input_action == "new-branch":
            try:
                GitOps.argv_for("new-branch", text)
                ok = True
            except ValueError:
                ok = False
        else:
            ok = bool(text.strip())
        self.input_ok.set_sensitive(ok)

    def _submit_input(self) -> None:
        if not self.input_ok.get_sensitive():
            return
        text = self.input_entry.get_text()
        action = self._input_action
        self.input_popover.popdown()
        self.run_action(action, text.strip() if action == "new-branch" else text)
