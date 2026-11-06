"""Pure sticky-note logic: colours, model round trip, markdown runs, saver."""
import pytest

from maestri_canvas import note_colors as C
from maestri_canvas.markdown_spans import Run, parse_markdown, runs_text
from maestri_canvas.note_model import NoteMeta, note_name, title_from_text, unique_name, valid_note_name
from maestri_canvas.note_saver import NoteSaver


# ---- colours ----------------------------------------------------------------------------------------------
def test_colour_names_hex_shorthand_and_fallback():
    assert C.resolve_hex("yellow") == "#FCF7B8" and C.resolve_hex("BLUE") == C.NAMED["blue"]
    assert C.resolve_hex("#abc") == "#AABBCC" and C.resolve_hex("12ab34") == "#12AB34" and C.resolve_hex("#11223344") == "#112233"
    for bad in (None, "", "not-a-colour", "#12", "#GGGGGG"):
        assert C.resolve_hex(bad) == C.DEFAULT_COLOR


def test_text_colour_follows_luminance_and_shade_darkens():
    assert C.text_color("#FEFDE8") == "#1F1F1F" and C.text_color("#102030") == "#F2F2F2"
    assert C.luminance(C.shade("#FEFDE8", 0.5)) < C.luminance("#FEFDE8")
    assert C.css_class("yellow") == C.css_class("#fcf7b8") == "note-c-fcf7b8"       # the same colour spelled two ways shares a rule
    assert "background: #EDEDE9" in C.css_rule("yellow") and C.clamp_font_size(5) == 10 and C.clamp_font_size(99) == 32


# ---- model ------------------------------------------------------------------------------------------------
SAMPLE = {"color": "yellow", "fileName": "Ideas.md", "fontSize": 16, "hasCustomName": True, "isPreviewing": True,
          "storageMode": {"custom": {"_0": "/tmp/x/Ideas.md"}}, "futureKey": {"a": [1, 2]}}


def test_meta_roundtrip_is_lossless_and_keeps_the_colour_string():
    m = NoteMeta.from_dict(SAMPLE)
    assert m.to_dict() == SAMPLE                                                     # incl. unknown key and the NAME "yellow" (not its hex)
    assert m.name == "Ideas" and m.is_custom and m.custom_path == "/tmp/x/Ideas.md"


def test_meta_omits_a_missing_file_name_and_defaults_to_managed():
    m = NoteMeta.from_dict({"color": "#fff", "fontSize": 14, "hasCustomName": False, "isPreviewing": False})
    assert "fileName" not in m.to_dict() and m.to_dict()["storageMode"] == {"managed": {}} and not m.is_custom
    m.set_custom_path("/a/b.md"); assert m.to_dict()["storageMode"] == {"custom": {"_0": "/a/b.md"}}
    m.set_custom_path(None); assert m.to_dict()["storageMode"] == {"managed": {}}


def test_name_never_carries_md_so_the_core_does_not_create_md_md():
    assert note_name("x.md") == "x" and note_name("x.MD") == "x" and note_name("x.md.md") == "x.md" and note_name("plain") == "plain"


def test_name_validation_and_titles():
    for bad in ("", " ", "a/b", "a\\b", "..", "a..b", ".hidden", "x\x00", " lead"):
        assert not valid_note_name(bad), bad
    assert valid_note_name("Reunião 2026") and valid_note_name("a.b")
    assert title_from_text("\n\n## Plan\nmore") == "Plan" and title_from_text("- [ ] todo") == "[ ] todo" and title_from_text("", "Note 3") == "Note 3"
    assert len(title_from_text("x" * 100)) == 40
    assert unique_name("Note", {"note"}) == "Note 2" and unique_name("Note", {"Note", "Note 2"}) == "Note 3" and unique_name("A", {"b"}) == "A"


# ---- markdown --------------------------------------------------------------------------------------------
def tagged(runs, tag):
    return "".join(r.text for r in runs if tag in r.tags)


def test_blocks_headings_lists_quote_hr_fence():
    runs = parse_markdown("# T\n## S\n#### deep\n- a\n  - nested\n1. one\n- [x] done\n- [ ] todo\n> quote\n---\n```\n**raw** `x`\n```\n")
    text = runs_text(runs)
    assert tagged(runs, "h1") == "T\n" and tagged(runs, "h2") == "S\n" and tagged(runs, "h3") == "deep\n"   # h4 maps to h3
    assert "• a\n" in text and "◦ nested\n" in text and "1. one\n" in text and "☑ done\n" in text and "☐ todo\n" in text
    assert tagged(runs, "indent1") == "◦ nested\n" and tagged(runs, "quote") == "quote\n" and tagged(runs, "hr") == "─" * 24 + "\n"
    assert tagged(runs, "code-block") == "**raw** `x`\n"                                                      # nothing inside a fence is interpreted
    assert text.endswith("\n")


def test_inline_styles_nest_and_code_protects_markers():
    runs = parse_markdown("a **b *c* d** `**x**` ~~s~~ e")
    assert tagged(runs, "bold") == "b c d" and tagged(runs, "italic") == "c" and tagged(runs, "strike") == "s"
    assert [r.text for r in runs if "code" in r.tags] == ["**x**"]
    assert runs_text(runs) == "a b c d **x** s e\n"


def test_snake_case_and_math_stars_are_not_emphasis_and_escapes_work():
    assert not any(r.tags for r in parse_markdown("my_var_name and 2*3*4"))
    assert runs_text(parse_markdown(r"\*not italic\* \# no")) == "*not italic* # no\n"
    assert tagged(parse_markdown("_it_ and __bd__"), "italic") == "it" and tagged(parse_markdown("__bd__"), "bold") == "bd"


def test_links_only_http_https_mailto_and_bare_urls():
    r = parse_markdown("[ok](https://a.b/c) [mail](mailto:x@y.z) [bad](javascript:alert(1)) see https://x.io/p?q=1, done.")
    links = [(x.text, x.link) for x in r if x.link]
    assert ("ok", "https://a.b/c") in links and ("mail", "mailto:x@y.z") in links and ("see", None) not in links
    assert not any(x.link and x.link.startswith("javascript") for x in r) and "bad" in runs_text(r)       # the label survives, the target does not
    assert ("https://x.io/p?q=1", "https://x.io/p?q=1") in links and tagged(r, "link").count(",") == 0   # trailing punctuation is not part of the URL


def test_images_become_image_runs_and_cells_align():
    img = [r for r in parse_markdown("before ![shot](</tmp/a b.png>) ![x](rel/p.png) ![alt](spaced name.png)") if r.image]
    assert [r.image for r in img] == ["/tmp/a b.png", "rel/p.png"]          # <...> allows spaces (CommonMark); a bare space ends the src, so the 3rd is text
    t = parse_markdown("| Name | Qty |\n|---|--:|\n| apple | 3 |\n| **fig** | 12 |\n")
    lines = runs_text(t).splitlines()
    assert len(lines) == 4 and len({len(l) for l in lines}) == 1                                          # every row has the same width
    assert lines[0].startswith("│ Name") and "┼" in lines[1] and "fig" in lines[3] and "**" not in lines[3]
    assert all("table" in r.tags for r in t) and "bold" in t[0].tags


def test_blank_lines_collapse_and_empty_input():
    assert runs_text(parse_markdown("a\n\n\n\nb")) == "a\n\nb\n" and parse_markdown("") == [] and parse_markdown("\n\n") == []


def test_cjk_table_alignment_uses_display_width():
    lines = runs_text(parse_markdown("| 名 | q |\n|--|--|\n| 苹果 | 1 |\n")).splitlines()
    import unicodedata
    width = lambda t: sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in t)
    assert len(lines) == 3 and len({width(l) for l in lines}) == 1             # equal DISPLAY width (wide chars take 2 columns), not equal len()


# ---- saver ------------------------------------------------------------------------------------------------
class Harness:
    def __init__(self, fail=0):
        self.timers: list = []; self.writes: list = []; self.states: list = []; self.pending_done = []; self.fail = fail; self.manual = False
        def write(text, done):
            self.writes.append(text)
            if self.manual:
                self.pending_done.append(done)
            else:
                err = Exception("boom") if self.fail > 0 else None
                self.fail -= 1
                done(err)
        self.saver = NoteSaver(write, 500, lambda s, e: self.states.append(s),
                               lambda fn, ms: (self.timers.append(fn), len(self.timers) - 1)[1],
                               lambda h: self.timers.__setitem__(h, lambda: None))


def test_saver_debounces_and_writes_only_the_latest():
    h = Harness()
    for t in ("a", "ab", "abc"): h.saver.schedule(t)
    assert h.writes == [] and h.saver.dirty
    h.timers[-1]()
    assert h.writes == ["abc"] and h.states[-1] == "saved" and not h.saver.dirty


def test_saver_flush_is_immediate_and_runs_waiters():
    h = Harness(); done = []
    h.saver.schedule("x"); h.saver.flush(lambda: done.append(1))
    assert h.writes == ["x"] and done == [1]


def test_saver_serializes_and_follows_up_with_the_newest_text():
    h = Harness(); h.manual = True
    h.saver.schedule("one"); h.timers[-1]()
    h.saver.schedule("two"); h.timers[-1]()
    assert h.writes == ["one"]                                    # "two" waits: never two writes in flight
    h.pending_done.pop(0)(None)
    assert h.writes == ["one", "two"]
    h.pending_done.pop(0)(None)
    assert h.states[-1] == "saved"


def test_saver_error_keeps_the_text_and_retries_on_next_flush():
    h = Harness(fail=1); done = []
    h.saver.schedule("keep"); h.saver.flush(lambda: done.append(1))
    assert h.states[-1] == "error" and h.saver.last_error is not None and done == [1] and h.saver.dirty
    h.saver.flush()
    assert h.writes == ["keep", "keep"] and h.states[-1] == "saved"


def test_saver_cancel_drops_pending_text_and_timer_but_not_what_is_in_flight():
    h = Harness()
    h.saver.schedule("never"); h.saver.cancel()
    h.timers[-1]()                                                   # the (cleared) timer firing late must do nothing
    assert h.writes == [] and not h.saver.dirty
    h2 = Harness(); h2.manual = True; done = []
    h2.saver.schedule("in-flight"); h2.timers[-1]()
    h2.saver.schedule("later"); h2.saver.cancel()                    # "later" is dropped, "in-flight" cannot be recalled
    h2.saver.flush(lambda: done.append(1))
    assert h2.writes == ["in-flight"] and done == []                 # waiters wait for the in-flight write
    h2.pending_done.pop(0)(None)
    assert h2.writes == ["in-flight"] and done == [1]
