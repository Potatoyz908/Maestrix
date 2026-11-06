"""Sticky note on real GTK 4 (needs a display) + end to end against the real core (throwaway data dir, see core_support.py)."""
import json
import os
import time
import types
import urllib.request
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

if not Gtk.init_check() or Gdk.Display.get_default() is None:
    pytest.skip("no display", allow_module_level=True)

from maestri_canvas.geometry import Point, Rect  # noqa: E402
from maestri_canvas.interaction import NodeContent, NodeHeader  # noqa: E402
from maestri_canvas.note_model import NoteMeta  # noqa: E402
from maestri_canvas.note_storage import CoreNoteStorage, CoreNotesClient, LocalNoteStorage, MemoryNoteStorage  # noqa: E402
from maestri_canvas.scene import NodeItem  # noqa: E402
from maestri_canvas.sticky_note import StickyNoteView  # noqa: E402
from maestri_canvas.viewport import Viewport  # noqa: E402
from maestri_canvas.widget import CanvasWidget  # noqa: E402

NID = "BBBBBBBB-0000-0000-0000-000000000002"


def pump_until(cond, timeout=6.0, what="condition"):
    ctx = GLib.MainContext.default()
    end = time.time() + timeout
    while time.time() < end:
        ctx.iteration(False)
        if cond():
            return
        time.sleep(0.004)
    raise AssertionError(f"timed out waiting for {what}")


def pump(seconds=0.2):
    ctx = GLib.MainContext.default()
    end = time.time() + seconds
    while time.time() < end:
        ctx.iteration(False); time.sleep(0.004)


def in_window(widget, w=420, h=420):
    win = Gtk.Window(default_width=w, default_height=h)
    win.set_child(widget); win.present(); pump(0.4)
    return win


def view_of(text="", meta=None, storage=None, **kw):
    kw.setdefault("debounce_ms", 60)
    meta = meta or NoteMeta(file_name="Note 1.md")
    v = StickyNoteView(meta, storage or MemoryNoteStorage(text), **kw)
    return v, in_window(v)


class Events:
    def __init__(self, v):
        self.meta, self.titles, self.deleted, self.dropped = [], [], [], []
        v.connect("meta-changed", lambda _v, d: self.meta.append(d))
        v.connect("title-changed", lambda _v, t: self.titles.append(t))
        v.connect("delete-requested", lambda _v: self.deleted.append(1))
        v.connect("file-dropped", lambda _v, p: self.dropped.append(p))


# ---- loading, modes, persistence ------------------------------------------------------------------------------
def test_loads_text_then_enables_the_editor():
    v, win = view_of("hello")
    assert v.text() == "hello" and v.raw.get_sensitive()
    win.destroy()


def test_editor_is_locked_until_the_text_has_loaded():
    class Slow(MemoryNoteStorage):
        def read(self, cb): self.cb = cb          # completes later
    st = Slow("late")
    v, win = view_of(storage=st)
    assert not v.raw.get_sensitive()              # never type over something not yet read
    st.cb("late", None); pump(0.05)
    assert v.raw.get_sensitive() and v.text() == "late"
    win.destroy()


def test_toggle_follows_is_previewing_and_emits_once_per_change():
    v, win = view_of("# T", meta=NoteMeta(file_name="n.md", is_previewing=False))
    ev = Events(v)
    assert v.stack.get_visible_child_name() == "raw" and v.raw_btn.get_active()
    v.fmt_btn.set_active(True)
    assert v.meta.is_previewing and v.stack.get_visible_child_name() == "formatted" and ev.meta[-1]["isPreviewing"] is True
    n = len(ev.meta)
    v.set_previewing(True)                         # same mode again: nothing to report
    assert len(ev.meta) == n
    v.raw_btn.set_active(True)
    assert not v.meta.is_previewing and ev.meta[-1]["isPreviewing"] is False
    win.destroy()


def test_initial_mode_comes_from_the_metadata():
    v, win = view_of("x", meta=NoteMeta(file_name="n.md", is_previewing=True))
    assert v.stack.get_visible_child_name() == "formatted" and v.fmt_btn.get_active()
    win.destroy()


def test_edits_are_debounced_into_a_single_write_of_the_latest_text():
    st = MemoryNoteStorage("")
    v, win = view_of(storage=st, debounce_ms=120)
    for ch in "abc":
        v.buf.insert_at_cursor(ch)
    assert st.writes == []                          # nothing yet: still inside the debounce window
    pump_until(lambda: st.writes, what="debounced write")
    pump(0.3)
    assert st.writes == ["abc"]                     # one write, with the final text
    assert v.status.get_text() == "Saved"
    win.destroy()


def test_blur_flushes_immediately():
    st = MemoryNoteStorage("")
    v, win = view_of(storage=st, debounce_ms=5000)
    v.buf.insert_at_cursor("typed")
    v.flush()
    assert st.writes == ["typed"]
    win.destroy()


def test_a_failed_write_shows_the_error_and_keeps_the_text_for_retry():
    class Flaky(MemoryNoteStorage):
        fails = 1
        def write(self, text, cb):
            if self.fails: self.fails -= 1; cb(OSError("disk full"))
            else: super().write(text, cb)
    st = Flaky("")
    v, win = view_of(storage=st)
    v.buf.insert_at_cursor("keep me")
    v.flush()
    assert v.status.get_text() == "Save failed" and v.status.has_css_class("error") and st.text == ""
    v.flush()
    assert st.text == "keep me" and v.status.get_text() == "Saved"
    win.destroy()


def test_local_write_is_atomic_temp_plus_rename(tmp_path):
    path = tmp_path / "n.md"
    path.write_text("v0")
    st = LocalNoteStorage(str(path))
    done = []
    st.write("v1", lambda e: done.append(e)); pump_until(lambda: done)
    ino1 = os.stat(path).st_ino
    st.write("v2", lambda e: done.append(e)); pump_until(lambda: len(done) == 2)
    assert done == [None, None] and path.read_text() == "v2"
    assert os.stat(path).st_ino != ino1                       # a NEW inode renamed over the old one: never a half-written file
    assert sorted(os.listdir(tmp_path)) == ["n.md"]           # and no temp file is left behind


def test_local_storage_read_of_a_missing_file_is_empty_not_an_error(tmp_path):
    got = []
    LocalNoteStorage(str(tmp_path / "nope.md")).read(lambda t, e: got.append((t, e)))
    pump_until(lambda: got)
    assert got == [("", None)]


# ---- colours / size / metadata ---------------------------------------------------------------------------------
def canvas_with_note(note=None, storage_text=None, **kw):
    c = CanvasWidget(viewport=Viewport(Point(0, 0), 1.0))
    c.add_node(NodeItem(NID, Rect(100, 80, 340, 300), 1, "stickyNote", "Note 1"), note=note or NoteMeta(file_name="Note 1.md").to_dict(), **kw)
    win = in_window(c, 700, 520)
    return c, c.frames[NID].content, win


def test_colour_name_is_resolved_for_the_card_but_persisted_verbatim():
    c, v, win = canvas_with_note({"color": "yellow", "fileName": "N.md", "fontSize": 14, "hasCustomName": False, "isPreviewing": False,
                                  "storageMode": {"managed": {}}})
    assert c.frames[NID].has_css_class("note-c-fefde8") and c.frames[NID].has_css_class("maestri-note")
    assert v.to_content()["color"] == "yellow"                 # the NAME survives, not its hex
    win.destroy()


def test_swatch_click_changes_card_class_and_emits_the_preset_string():
    c, v, win = canvas_with_note()
    got = []
    c.connect("note-meta-changed", lambda _c, nid, d: got.append((nid, d)))
    blue = next(b for val, b in v._swatches if val == "#D6EBFF")
    blue.emit("clicked")
    f = c.frames[NID]
    assert f.has_css_class("note-c-d6ebff") and not f.has_css_class("note-c-fefde8")
    assert got and got[-1][0] == NID and got[-1][1]["color"] == "#D6EBFF" and blue.has_css_class("active")
    n = len(got)
    blue.emit("clicked")                                       # same colour: no event
    assert len(got) == n
    win.destroy()


def test_unknown_metadata_keys_survive_a_roundtrip_through_the_widget():
    meta = NoteMeta.from_dict({"color": "#abc", "fileName": "N.md", "fontSize": 14, "hasCustomName": False, "isPreviewing": False,
                               "storageMode": {"managed": {}}, "futureKey": {"x": 1}})
    v, win = view_of("", meta=meta)
    v.set_font_size(18)
    assert v.to_content()["futureKey"] == {"x": 1} and v.to_content()["fontSize"] == 18 and v.to_content()["color"] == "#abc"
    win.destroy()


def test_font_size_is_clamped_and_reported_via_the_card_class():
    c, v, win = canvas_with_note()
    f = c.frames[NID]
    assert f.has_css_class("note-fs-14")
    v.set_font_size(999); assert v.meta.font_size == 32 and f.has_css_class("note-fs-32") and not f.has_css_class("note-fs-14")
    v.set_font_size(1); assert v.meta.font_size == 10
    win.destroy()


def test_custom_colour_via_the_dialog_button_stores_a_hex():
    v, win = view_of("")
    rgba = Gdk.RGBA(); rgba.parse("#123456")
    v.color_btn.set_rgba(rgba)
    assert v.meta.color == "#123456"
    win.destroy()


def test_bind_applies_external_metadata_without_echoing_it_back():
    v, win = view_of("")
    ev = Events(v)
    v.bind({"color": "pink", "fileName": "Note 1.md", "fontSize": 20, "hasCustomName": True, "isPreviewing": True, "storageMode": {"managed": {}}})
    assert v.meta.color == "pink" and v.meta.font_size == 20 and v.stack.get_visible_child_name() == "formatted" and ev.meta == []
    win.destroy()


# ---- markdown rendering ------------------------------------------------------------------------------------------
def fmt_text(v):
    b = v.fmt.get_buffer()
    return b.get_text(b.get_start_iter(), b.get_end_iter(), False)


def tag_names_at(v, offset):
    return {t.get_property("name") for t in v.fmt.get_buffer().get_iter_at_offset(offset).get_tags()}


def test_formatted_view_renders_blocks_inline_styles_and_tables():
    v, win = view_of("# Title\n\nplain **bold** `code`\n\n- item\n\n| A | B |\n|--|--|\n| 1 | 2 |\n", meta=NoteMeta(file_name="n.md", is_previewing=True))
    t = fmt_text(v)
    assert t.startswith("Title\n") and "• item" in t and "│ A" in t and "**" not in t and "`" not in t
    assert "h1" in tag_names_at(v, 0)
    assert "bold" in tag_names_at(v, t.index("bold") + 1) and "code" in tag_names_at(v, t.index("code") + 1)
    assert "table" in tag_names_at(v, t.index("│"))
    win.destroy()


def test_rerendering_does_not_duplicate_link_tags():
    v, win = view_of("[a](https://a.io) [b](https://b.io)", meta=NoteMeta(file_name="n.md", is_previewing=True))
    for _ in range(3):
        v.buf.insert_at_cursor("x")                  # every edit re-renders while previewing
    assert len(v._links) == 2 and "xxx" in fmt_text(v)
    win.destroy()


def test_link_click_opens_http_and_ignores_text_and_dangerous_schemes():
    v, win = view_of("see [site](https://example.com/p) and [bad](javascript:alert(1))", meta=NoteMeta(file_name="n.md", is_previewing=True))
    opened = []
    v._open_uri = opened.append
    t = fmt_text(v)
    def click_on(word):
        rect = v.fmt.get_iter_location(v.fmt.get_buffer().get_iter_at_offset(t.index(word) + 1))
        x, y = v.fmt.buffer_to_window_coords(Gtk.TextWindowType.TEXT, rect.x + 2, rect.y + rect.height // 2)
        v._on_fmt_click(None, 1, x, y)
    click_on("site"); click_on("bad"); click_on("see")
    assert opened == ["https://example.com/p"]
    win.destroy()


def test_images_render_from_disk_and_missing_or_remote_ones_fall_back_to_text(tmp_path):
    tex = Gdk.MemoryTexture.new(40, 20, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(bytes([200, 30, 30, 255]) * 800), 160)
    tex.save_to_png(str(tmp_path / "pic one.png"))
    v, win = view_of("![ok](<pic one.png>) ![gone](missing.png) ![net](https://example.com/x.png)",
                     meta=NoteMeta(file_name="n.md", is_previewing=True), base_dir=str(tmp_path))
    t = fmt_text(v)
    assert len(v._pictures) == 1 and "[gone]" in t and "[net]" in t       # the remote image is never fetched while rendering
    assert "image-missing" in tag_names_at(v, t.index("[gone]") + 1)
    win.destroy()


# ---- external changes ----------------------------------------------------------------------------------------------
def test_external_write_updates_a_clean_buffer_and_keeps_the_cursor(tmp_path):
    path = tmp_path / "n.md"
    path.write_text("AAA\nBBB\nCCC")
    meta = NoteMeta(file_name="n.md"); meta.set_custom_path(str(path))
    v, win = view_of(meta=meta, storage=LocalNoteStorage(str(path)))
    pump_until(lambda: v.text() == "AAA\nBBB\nCCC", what="initial load")
    v.buf.place_cursor(v.buf.get_iter_at_offset(7))               # end of BBB
    path.write_text("AAA\nBBB\nCCC\nDDD")                          # an agent appended a line
    pump_until(lambda: v.text().endswith("DDD"), what="file monitor reflecting the write")
    assert v.buf.get_iter_at_mark(v.buf.get_insert()).get_offset() == 7 and v.status.get_text() == "Updated from disk"
    path.write_text("ZZZ\nAAA\nBBB\nCCC\nDDD")                     # prepended: the cursor must follow its text, not stay at offset 7
    pump_until(lambda: v.text().startswith("ZZZ"))
    assert v.buf.get_iter_at_mark(v.buf.get_insert()).get_offset() == 11
    win.destroy()


def test_external_write_never_overwrites_unsaved_edits(tmp_path):
    path = tmp_path / "n.md"
    path.write_text("original")
    meta = NoteMeta(file_name="n.md"); meta.set_custom_path(str(path))
    v, win = view_of(meta=meta, storage=LocalNoteStorage(str(path)), debounce_ms=60000)    # the user's edit stays unsaved
    pump_until(lambda: v.text() == "original")
    v.buf.insert_at_cursor(" + my edit")
    path.write_text("agent version")
    pump_until(lambda: "disk" in v.status.get_text().lower(), what="conflict notice")
    assert v.text() == "original + my edit" or v.text().endswith("my edit")
    assert "agent version" not in v.text()
    win.destroy()


def test_our_own_write_does_not_trigger_a_reload(tmp_path):
    path = tmp_path / "n.md"
    path.write_text("")
    meta = NoteMeta(file_name="n.md"); meta.set_custom_path(str(path))
    v, win = view_of(meta=meta, storage=LocalNoteStorage(str(path)))
    pump(0.2)
    v.buf.insert_at_cursor("mine"); v.flush()
    pump_until(lambda: path.read_text() == "mine"); pump(0.4)
    assert v.text() == "mine" and v.status.get_text() != "Updated from disk"
    win.destroy()


# ---- rename / move / delete / adopt ---------------------------------------------------------------------------------
class FakeClient:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail
    def rename(self, old, new, cb):
        self.calls.append(("rename", old, new)); cb(OSError("nope") if self.fail else None)
    def delete(self, name, cb):
        self.calls.append(("delete", name)); cb(None)
    def put(self, name, text, cb):
        self.calls.append(("put", name, text)); cb(None)
    def get(self, name, cb):
        cb("", None)


def test_rename_flushes_pending_text_first_then_renames_and_marks_custom_name():
    st, cl = MemoryNoteStorage(""), FakeClient()
    v, win = view_of(storage=st, client=cl, debounce_ms=60000)
    ev = Events(v)
    v.buf.insert_at_cursor("unsaved")
    v.rename("Plan")
    assert st.writes == ["unsaved"] and cl.calls == [("rename", "Note 1", "Plan")]     # the text lands in the OLD note before it is moved
    assert v.meta.file_name == "Plan.md" and v.meta.has_custom_name and v.meta.name == "Plan"   # file name keeps the .md; the core is given "Plan"
    assert ev.meta[-1]["fileName"] == "Plan.md" and ev.meta[-1]["hasCustomName"] is True and ev.titles[-1] == "Plan"
    assert isinstance(v.storage, CoreNoteStorage) and v.storage.name == "Plan"          # later saves go to the NEW name, without ".md"
    win.destroy()


@pytest.mark.parametrize("bad", ["", " ", "a/b", "..", ".hidden", "x\\y"])
def test_rename_rejects_unsafe_names_without_calling_the_core(bad):
    cl = FakeClient()
    v, win = view_of("", client=cl)
    v.rename(bad)
    assert cl.calls == [] and v.meta.file_name == "Note 1.md" and not v.meta.has_custom_name
    assert v.status.get_text() == "Invalid name"
    win.destroy()


def test_rename_rejects_a_name_used_by_another_note_and_reports_core_failures():
    cl = FakeClient()
    v, win = view_of("", client=cl, name_taken=lambda n: n.lower() == "other")
    v.rename("Other")
    assert cl.calls == [] and v.status.get_text() == "Name already used"
    bad = FakeClient(fail=True)
    v2, win2 = view_of("", client=bad)
    v2.rename("Fresh")
    assert v2.meta.file_name == "Note 1.md" and not v2.meta.has_custom_name and "Rename failed" in v2.status.get_text()
    win.destroy(); win2.destroy()


def test_rename_of_a_custom_path_note_renames_the_file_on_disk(tmp_path):
    path = tmp_path / "old.md"; path.write_text("body")
    meta = NoteMeta(file_name="old.md"); meta.set_custom_path(str(path))
    v, win = view_of(meta=meta, storage=LocalNoteStorage(str(path)))
    pump_until(lambda: v.text() == "body")
    done = []
    v.rename("fresh", lambda e: done.append(e)); pump_until(lambda: done)
    assert done == [None] and (tmp_path / "fresh.md").read_text() == "body" and not path.exists()
    assert v.meta.custom_path == str(tmp_path / "fresh.md") and v.meta.file_name == "fresh.md"
    win.destroy()


def test_rename_refuses_to_overwrite_an_existing_file(tmp_path):
    (tmp_path / "old.md").write_text("a"); (tmp_path / "taken.md").write_text("keep")
    meta = NoteMeta(file_name="old.md"); meta.set_custom_path(str(tmp_path / "old.md"))
    v, win = view_of(meta=meta, storage=LocalNoteStorage(str(tmp_path / "old.md")))
    pump_until(lambda: v.text() == "a")
    done = []
    v.rename("taken", lambda e: done.append(e)); pump_until(lambda: done)
    assert done[0] is not None and (tmp_path / "taken.md").read_text() == "keep" and (tmp_path / "old.md").exists()
    win.destroy()


def test_move_to_folder_writes_atomically_deletes_the_old_copy_and_switches_to_custom(tmp_path):
    dest = tmp_path / "project"; dest.mkdir()
    st, cl = MemoryNoteStorage("hello"), FakeClient()
    v, win = view_of(storage=st, client=cl)
    ev = Events(v)
    done = []
    v.move_to(str(dest), lambda e: done.append(e)); pump_until(lambda: done)
    assert done == [None] and (dest / "Note 1.md").read_text() == "hello"
    assert st.deleted                                                    # the managed copy is removed only after the new file is safe
    assert v.meta.is_custom and v.meta.custom_path == str(dest / "Note 1.md")
    assert ev.meta[-1]["storageMode"] == {"custom": {"_0": str(dest / "Note 1.md")}}
    win.destroy()


def test_move_refuses_when_the_target_exists_and_changes_nothing(tmp_path):
    (tmp_path / "Note 1.md").write_text("someone else's")
    st = MemoryNoteStorage("mine")
    v, win = view_of(storage=st, client=FakeClient())
    done = []
    v.move_to(str(tmp_path), lambda e: done.append(e))
    assert done and done[0] is not None and (tmp_path / "Note 1.md").read_text() == "someone else's" and not st.deleted and not v.meta.is_custom
    win.destroy()


def test_delete_file_removes_it_asks_for_node_removal_and_never_writes_again():
    st = MemoryNoteStorage("bye")
    v, win = view_of(storage=st, debounce_ms=30)
    ev = Events(v)
    v.buf.insert_at_cursor("!")                 # a pending debounced save...
    v.delete_file()
    pump(0.3)
    assert st.deleted and ev.deleted == [1] and st.writes == []      # ...must not resurrect the file
    win.destroy()


def test_a_failed_delete_keeps_the_note_alive():
    class NoDelete(MemoryNoteStorage):
        def delete(self, cb): cb(PermissionError("read-only"))
    v, win = view_of("x", storage=NoDelete("x"))
    ev = Events(v)
    v.delete_file()
    assert ev.deleted == [] and "Delete failed" in v.status.get_text()
    v.buf.insert_at_cursor("y"); v.flush()
    assert v.status.get_text() == "Saved"                             # still editable and saving


def test_a_failed_delete_does_not_lose_the_edit_it_cancelled():
    class NoDelete(MemoryNoteStorage):
        def delete(self, cb): cb(PermissionError("read-only"))
    st = NoDelete("")
    v, win = view_of(storage=st, debounce_ms=60000)
    v.buf.insert_at_cursor("precious")
    v.delete_file()
    v.flush()
    assert st.writes == ["precious"]                                  # the cancelled text was re-queued and is saved on the next flush
    win.destroy()


def test_delete_waits_for_a_write_already_in_flight():
    order = []
    class Slow(MemoryNoteStorage):
        def write(self, text, cb): order.append("write"); self.pending = cb      # completes later
        def delete(self, cb): order.append("delete"); super().delete(cb)
    st = Slow("")
    v, win = view_of(storage=st, debounce_ms=60000)
    v.buf.insert_at_cursor("x"); v.flush()                            # write starts and stays in flight
    v.delete_file()
    assert order == ["write"]                                         # delete must not race ahead of it (a late PUT would recreate the note)
    st.pending(None); pump(0.1)
    assert order == ["write", "delete"] and st.deleted
    win.destroy()
    win.destroy()


def test_drop_of_a_note_file_is_offered_to_the_app_and_other_files_are_ignored(tmp_path):
    v, win = view_of("")
    ev = Events(v)
    assert v._on_drop(None, Gio.File.new_for_path(str(tmp_path / "x.md")), 0, 0) is True and ev.dropped == [str(tmp_path / "x.md")]
    assert v._on_drop(None, Gio.File.new_for_path(str(tmp_path / "x.exe")), 0, 0) is False and len(ev.dropped) == 1
    win.destroy()


def test_adopt_file_uses_the_file_as_storage_but_refuses_over_unsaved_edits(tmp_path):
    f = tmp_path / "doc.markdown"; f.write_text("from file")
    v, win = view_of("old", debounce_ms=60000)
    v.buf.insert_at_cursor("!")
    done = []
    v.adopt_file(str(f), lambda e: done.append(e))
    assert len(done) == 1 and isinstance(done[0], RuntimeError) and v.text() == "old!" and not v.meta.is_custom   # dirty: refused, nothing replaced
    v.buf.set_text("old"); v.saver._pending = None; v._saved_text = "old"
    v.adopt_file(str(f), lambda e: done.append(e)); pump_until(lambda: len(done) == 2)
    assert done[1] is None and v.text() == "from file" and v.meta.custom_path == str(f) and v.meta.file_name == "doc.markdown"
    win.destroy()


# ---- image paste ----------------------------------------------------------------------------------------------------
def test_pasted_image_is_saved_next_to_the_note_and_referenced_with_a_safe_markdown_path(tmp_path):
    v, win = view_of("", base_dir=str(tmp_path))
    tex = Gdk.MemoryTexture.new(8, 8, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(bytes([10, 120, 200, 255]) * 64), 32)
    name = v.paste_texture(tex)
    assert name and (tmp_path / name).is_file() and name.endswith(".png")
    assert f"![](<{name}>)" in v.text() and " " in name                 # "Note 1-....png" has a space, so the destination is wrapped in <>
    v.set_previewing(True)
    assert len(v._pictures) == 1                                         # and it renders back from that relative path
    win.destroy()


def test_image_paste_without_a_folder_is_refused_with_a_message():
    v, win = view_of("")
    assert v.paste_texture(Gdk.MemoryTexture.new(2, 2, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(bytes(16)), 8)) is None
    assert "needs a folder" in v.status.get_text() and v.text() == ""
    win.destroy()


# ---- inside the canvas -----------------------------------------------------------------------------------------------
def test_header_widgets_carry_the_right_canvas_roles_even_when_zoomed():
    c, v, win = canvas_with_note()
    for z in (1.0, 1.5):
        c.viewport.zoom_at(Point(0, 0), z / c.viewport.zoom); c.controller.dirty = True; pump(0.3)
        def centre(w):
            ok, b = w.compute_bounds(c); assert ok
            return b.get_x() + b.get_width() / 2, b.get_y() + b.get_height() / 2
        assert c.target_at(*centre(v.fmt_btn)) == NodeContent(NID)       # real controls keep their clicks
        assert c.target_at(*centre(v.menu_btn)) == NodeContent(NID)
        assert c.target_at(*centre(v.title_label)) == NodeHeader(NID)    # the title area still drags the node
        assert c.target_at(*centre(v.raw)) == NodeContent(NID) or c.target_at(*centre(v.stack)) == NodeContent(NID)
    win.destroy()


def test_double_click_on_the_header_starts_renaming_and_editing_retags_the_label():
    c, v, win = canvas_with_note()
    t = [1000]
    g = types.SimpleNamespace(get_current_event_time=lambda: t[0])
    c._header_double_click(g, NodeHeader(NID), 100, 100)
    t[0] = 1200
    c._header_double_click(g, NodeHeader(NID), 102, 101)                 # second press: 200 ms, 2 px
    pump_until(lambda: v.title_label.get_editing(), what="rename mode")
    ok, b = v.title_label.compute_bounds(c)
    assert c.target_at(b.get_x() + b.get_width() / 2, b.get_y() + b.get_height() / 2) == NodeContent(NID)   # editing: pointer goes to the entry
    v.title_label.stop_editing(False)
    pump(0.1)
    ok, b = v.title_label.compute_bounds(c)
    assert c.target_at(b.get_x() + b.get_width() / 2, b.get_y() + b.get_height() / 2) == NodeHeader(NID)
    win.destroy()


def test_slow_or_distant_second_press_is_not_a_double_click():
    c, v, win = canvas_with_note()
    t = [1000]
    g = types.SimpleNamespace(get_current_event_time=lambda: t[0])
    c._header_double_click(g, NodeHeader(NID), 100, 100)
    t[0] = 1600; c._header_double_click(g, NodeHeader(NID), 100, 100)    # too slow
    t[0] = 1700; c._header_double_click(g, NodeHeader(NID), 160, 100)    # too far
    pump(0.15)
    assert not v.title_label.get_editing()
    win.destroy()


def test_typing_in_a_note_never_triggers_canvas_shortcuts():
    c, v, win = canvas_with_note()
    c.scene.selection = {NID}
    v.raw.grab_focus(); pump(0.2)
    assert c._focus_in_node()
    deleted = []
    c.connect("delete-requested", lambda _c, ids: deleted.append(ids))
    assert not c._key_pressed(None, Gdk.KEY_Delete, 0, Gdk.ModifierType(0))
    assert not c._key_pressed(None, Gdk.KEY_BackSpace, 0, Gdk.ModifierType(0))
    assert not c._key_pressed(None, Gdk.KEY_w, 0, Gdk.ModifierType.CONTROL_MASK)       # readline/word-delete chord: the editor keeps it
    assert deleted == [] and NID in c.scene.nodes
    win.destroy()


def test_removing_the_node_flushes_the_last_edit(tmp_path):
    path = tmp_path / "n.md"; path.write_text("")
    note = NoteMeta(file_name="n.md"); note.set_custom_path(str(path))
    c, v, win = canvas_with_note(note.to_dict())
    pump(0.2)
    v.buf.insert_at_cursor("last words")
    c.remove_node(NID)
    pump_until(lambda: path.read_text() == "last words", what="final flush on removal")
    win.destroy()


def test_two_notes_cannot_share_a_name_and_delete_in_the_node_header_reaches_the_canvas():
    c = CanvasWidget(viewport=Viewport(Point(0, 0), 1.0))
    c.add_node(NodeItem("AAAAAAAA-0000-0000-0000-000000000001", Rect(0, 0, 340, 300), 1, "stickyNote", "Note 1"), note=NoteMeta(file_name="Alpha.md").to_dict())
    c.add_node(NodeItem(NID, Rect(400, 0, 340, 300), 2, "stickyNote", "Note 2"), note=NoteMeta(file_name="Beta.md").to_dict())
    win = in_window(c, 900, 500)
    a, b = c.frames["AAAAAAAA-0000-0000-0000-000000000001"].content, c.frames[NID].content
    b.rename("alpha")                                          # case-insensitive clash with the other note
    assert b.meta.file_name == "Beta.md" and b.status.get_text() == "Name already used"
    got = []
    c.connect("delete-requested", lambda _c, ids: got.append(ids))
    a.delete_file(); pump(0.1)
    assert got == [("AAAAAAAA-0000-0000-0000-000000000001",)]
    win.destroy()


# ---- end to end against the real core ---------------------------------------------------------------------------------
def _ensure_workspace(core):
    req = urllib.request.Request(f"http://127.0.0.1:{core['port']}/api/workspace", headers={"Authorization": f"Bearer {core['token']}"})
    urllib.request.urlopen(req, timeout=5).read()    # the core creates and activates a default workspace on first read


def _notes_dirs(core):
    return list((core["data_dir"] / "workspaces").glob("*/notes"))


def _call(fn, *args):
    out = []
    fn(*args, lambda *r: out.append(r))
    pump_until(lambda: out, what=f"{fn.__name__} callback")
    return out[0]


def test_e2e_put_get_delete_rename_roundtrip_with_special_names(core):
    _ensure_workspace(core)
    cl = CoreNotesClient(core["port"], core["token"])
    name = "Reunião 2026 ✓"
    assert _call(cl.put, name, "# olá\ncorpo") == (None,)
    assert _call(cl.get, name) == ("# olá\ncorpo", None)
    assert any((d / f"{name}.md").is_file() for d in _notes_dirs(core))                   # the core appended ".md" exactly once
    assert _call(cl.rename, name, "Renomeada") == (None,)
    assert _call(cl.get, "Renomeada") == ("# olá\ncorpo", None) and _call(cl.get, name) == ("", None)   # old one is gone (404 -> empty)
    assert _call(cl.delete, "Renomeada") == (None,) and _call(cl.get, "Renomeada") == ("", None)
    assert not any((d / "Renomeada.md").exists() for d in _notes_dirs(core))


def test_e2e_missing_note_reads_as_empty_and_bad_names_never_leave_the_process(core):
    cl = CoreNotesClient(core["port"], core["token"])
    assert _call(cl.get, "never-created") == ("", None)
    for bad in ("../escape", "a/b", ".hidden", ""):
        err = _call(cl.put, bad, "x")[0]
        assert isinstance(err, ValueError), bad                                         # rejected locally, before any request


def test_e2e_wrong_token_is_rejected(core):
    cl = CoreNotesClient(core["port"], "not-the-token")
    err = _call(cl.put, "nope", "x")[0]
    assert err is not None and "401" in str(err)
    assert _call(cl.get, "nope")[1] is not None


def test_e2e_widget_saves_to_the_core_without_md_md_and_picks_up_an_agent_write(core):
    _ensure_workspace(core)
    cl = CoreNotesClient(core["port"], core["token"])
    meta = NoteMeta(file_name="Ideas.md")                                                # fileName WITH extension, as persisted
    v, win = view_of(storage=CoreNoteStorage(cl, meta.name), meta=meta, client=cl, debounce_ms=40)
    pump_until(lambda: v.raw.get_sensitive())
    v.buf.insert_at_cursor("# Plan\n- one")
    pump_until(lambda: v.status.get_text() == "Saved", what="save through the core")
    files = [p for d in _notes_dirs(core) for p in d.iterdir()]
    assert any(p.name == "Ideas.md" for p in files) and not any(p.name == "Ideas.md.md" for p in files)
    assert next(p for p in files if p.name == "Ideas.md").read_text() == "# Plan\n- one"
    # an agent edits the file on disk (what `maestri note write` does): managed notes are watched through their file path
    note_file = next(p for p in files if p.name == "Ideas.md")
    v._watch_path = str(note_file); v._start_watch()
    note_file.write_text("# Plan\n- one\n- two (agent)")
    pump_until(lambda: "agent" in v.text(), what="agent write reflected")
    win.destroy()
