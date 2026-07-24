"""
docx_tag_engine.py — Reusable {{tag}} fill engine for Word docs exported from
Google Docs (Paper MSA template, but the mechanics are generic).
 
Handles:
  - Finding every {{...}} tag occurrence by TEXT, robust to a tag's braces/name
    being split across multiple <w:r> runs (common after Google Docs export
    when part of a tag has different formatting, e.g. a color used to mark
    Start/End tags during template authoring).
  - Simple field substitution ("{{Account.Name}}" -> "Harley's District").
  - Paired Start/End "section" tags: keep (just remove the marker tags) or
    delete (remove the marker tags AND everything between them, at whichever
    granularity is safe: paragraph range, or whole table row).
  - Collapsing adjacent deleted sections into a single "N/A" paragraph.
  - Anchor-text-based removal for content that ISN'T tag-wrapped (known
    template gaps), from a given occurrence of a heading through a fixed end
    marker or to end-of-document-body.
  - Comment insertion (shells out to the existing docx skill's comment.py),
    anchored precisely to the enclosing run(s) of a tag occurrence or of
    arbitrary literal anchor text.
  - Removing all yellow (and any leftover template-authoring-color) highlight.
 
This module only edits word/document.xml. Callers are responsible for
unzip/rezip and for running merge_runs.py first (recommended — coalesces
fragmented runs so plain text is more often contained in a single run,
though this engine works even when it isn't).
"""
 
import re
import subprocess
from pathlib import Path
 
TAG_RE = re.compile(r"\{\{[^}]*\}\}")
RUN_RE = re.compile(r"<w:r(?:\s[^>]*)?>.*?</w:r>", re.S)
T_RE = re.compile(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", re.S)
 
 
class TagEngineError(Exception):
    pass
 
 
def read_document_xml(unpacked_dir: Path) -> str:
    return (unpacked_dir / "word" / "document.xml").read_text(encoding="utf-8")
 
 
def write_document_xml(unpacked_dir: Path, text: str) -> None:
    (unpacked_dir / "word" / "document.xml").write_text(text, encoding="utf-8")
 
 
def normalize_bookmarks(doc: str) -> str:
    """Renumber duplicate bookmark IDs and rename duplicate bookmark names
    so the output passes OOXML ID-uniqueness validation.
 
    Why this exists: Google Docs preserves the internal heading anchor ID
    when a heading is copy-pasted within a document, so the docx export can
    contain two bookmarks with identical w:name AND w:id (seen in the real
    MSA template: two "General Service Information" headings both exporting
    as name="_ovwbz6r56ohy" id="8"). Template editors will copy-paste
    headings again, so normalize on every run rather than special-casing.
 
    Starts/ends are paired in document order per ID. The first occurrence
    keeps its original id/name, so any hyperlink referencing the anchor
    still resolves to the first bookmark."""
    seen_ids, seen_names = {}, {}
    used_ids = set(int(i) for i in re.findall(
        r'<w:bookmarkStart[^>]*w:id="(\d+)"', doc))
    next_id = max(used_ids, default=0) + 1
    remap_queue = {}  # old_id -> FIFO of new ids for 2nd+ occurrences
 
    def fix_start(m):
        nonlocal next_id
        tag = m.group(0)
        bid = re.search(r'w:id="(\d+)"', tag).group(1)
        name_m = re.search(r'w:name="([^"]+)"', tag)
        name = name_m.group(1) if name_m else None
        if bid in seen_ids:
            new = str(next_id)
            next_id += 1
            remap_queue.setdefault(bid, []).append(new)
            tag = tag.replace(f'w:id="{bid}"', f'w:id="{new}"')
        else:
            seen_ids[bid] = True
        if name:
            if name in seen_names:
                seen_names[name] += 1
                tag = tag.replace(f'w:name="{name}"',
                                  f'w:name="{name}_{seen_names[name]}"')
            else:
                seen_names[name] = 0
        return tag
 
    doc = re.sub(r'<w:bookmarkStart[^>]*/?>', fix_start, doc)
 
    seen_end_ids = set()
 
    def fix_end(m):
        tag = m.group(0)
        bid = re.search(r'w:id="(\d+)"', tag).group(1)
        if bid in seen_end_ids and remap_queue.get(bid):
            new = remap_queue[bid].pop(0)
            tag = tag.replace(f'w:id="{bid}"', f'w:id="{new}"')
        else:
            seen_end_ids.add(bid)
        return tag
 
    doc = re.sub(r'<w:bookmarkEnd[^>]*/?>', fix_end, doc)
    return doc
 
 
def run_merge_runs(unpacked_dir: Path, docx_skill_scripts: Path) -> None:
    """Coalesce adjacent same-formatting runs so text is more findable.
    Safe to call even if it changes nothing structurally important."""
    subprocess.run(
        ["python3", str(docx_skill_scripts / "merge_runs.py"), str(unpacked_dir)],
        check=True, capture_output=True, text=True,
    )
 
 
# ---------------------------------------------------------------------------
# Tag discovery: map every visible character in the document to its exact
# doc.xml offset, then find {{...}} matches against that concatenated text.
# This is what makes tag-finding robust to a tag being split across runs.
# ---------------------------------------------------------------------------
 
def _build_text_index(doc: str):
    runs = [(m.start(), m.end(), m.group()) for m in RUN_RE.finditer(doc)]
    segs = []  # (concat_start, concat_end, abs_t_start)
    pos = 0
    parts = []
    for (s, e, text) in runs:
        tm = T_RE.search(text)
        if tm:
            tlen = tm.end(1) - tm.start(1)
            segs.append((pos, pos + tlen, s + tm.start(1)))
            parts.append(tm.group(1))
            pos += tlen
    full_text = "".join(parts)
    return full_text, segs
 
 
def _to_doc_pos(segs, ft_pos, prefer_end=False):
    if not prefer_end:
        for (cs, ce, abs_t) in segs:
            if cs <= ft_pos < ce:
                return abs_t + (ft_pos - cs)
        for (cs, ce, abs_t) in segs:
            if ft_pos == cs:
                return abs_t
    else:
        target = ft_pos - 1
        for (cs, ce, abs_t) in segs:
            if cs <= target < ce:
                return abs_t + (target - cs) + 1
    raise TagEngineError(f"could not map text offset {ft_pos} back to document.xml")
 
 
def find_tag_occurrences(doc: str):
    """Return dict: tag_literal (e.g. '{{Account.Name}}') -> list of
    (start, end) doc.xml char spans, in document order, 0-indexed list
    (occurrence N is index N-1)."""
    full_text, segs = _build_text_index(doc)
    result = {}
    for m in TAG_RE.finditer(full_text):
        ds = _to_doc_pos(segs, m.start(), prefer_end=False)
        de = _to_doc_pos(segs, m.end(), prefer_end=True)
        result.setdefault(m.group(), []).append((ds, de))
    return result
 
 
def find_text_occurrences(doc: str, literal_text: str):
    """Same idea as find_tag_occurrences but for arbitrary plain text
    (used for anchoring to untagged content, e.g. a heading)."""
    full_text, segs = _build_text_index(doc)
    result = []
    start = 0
    while True:
        i = full_text.find(literal_text, start)
        if i == -1:
            break
        ds = _to_doc_pos(segs, i, prefer_end=False)
        de = _to_doc_pos(segs, i + len(literal_text), prefer_end=True)
        result.append((ds, de))
        start = i + 1
    return result
 
 
# ---------------------------------------------------------------------------
# Structural boundary helpers
# ---------------------------------------------------------------------------
 
def paragraph_bounds(doc: str, start: int, end: int):
    ps = max(doc.rfind("<w:p ", 0, start), doc.rfind("<w:p>", 0, start))
    if ps == -1:
        raise TagEngineError("no enclosing <w:p> found before position %d" % start)
    pe = doc.find("</w:p>", end)
    if pe == -1:
        raise TagEngineError("no closing </w:p> found after position %d" % end)
    return ps, pe + len("</w:p>")
 
 
def row_bounds(doc: str, start: int, end: int):
    ts = max(doc.rfind("<w:tr ", 0, start), doc.rfind("<w:tr>", 0, start))
    if ts == -1:
        raise TagEngineError("no enclosing <w:tr> found before position %d" % start)
    te = doc.find("</w:tr>", end)
    if te == -1:
        raise TagEngineError("no closing </w:tr> found after position %d" % end)
    return ts, te + len("</w:tr>")
 
 
def enclosing_run_bounds(doc: str, start: int, end: int):
    """Widen (start,end) out to the full enclosing run(s), so a comment
    marker can be inserted as a sibling of <w:r>, never inside a <w:t>."""
    rs1 = doc.rfind("<w:r>", 0, start)
    rs2 = doc.rfind("<w:r ", 0, start)
    run_start = max(rs1, rs2)
    run_end = doc.find("</w:r>", end - 1)
    if run_end == -1:
        raise TagEngineError("no closing </w:r> found after position %d" % end)
    return run_start, run_end + len("</w:r>")
 
 
def contained_in(span, ranges):
    a, b = span
    return any(rs <= a and b <= re_ for (rs, re_) in ranges)
 
 
def paragraph_has_other_content(doc: str, ps: int, pe: int, span) -> bool:
    """True if the paragraph range [ps,pe) has any non-whitespace visible
    text (including un-stripped {{tag}} literals) outside of `span`.
 
    Shared by callers deciding whether a deletion/strip can safely take
    the whole enclosing paragraph with it, or must be narrowed to just
    the given span because the paragraph also holds other content (e.g.
    a sibling section's alternative value, or inline text sharing the
    same line as a tag)."""
    outside = doc[ps:span[0]] + doc[span[1]:pe]
    visible = re.sub(r"<[^>]+>", "", outside)
    return bool(visible.strip())
 
 
def strip_marker_or_line(doc: str, span):
    """Plan a delete range for a single marker tag occurrence (e.g. one
    half of a kept section's Start/End pair, where only the marker tag
    itself -- not the content it wraps -- is meant to disappear).
 
    If the tag is the only visible thing on its paragraph, widen the
    range to the whole paragraph so removing the tag doesn't leave a
    blank line behind. If the paragraph has other visible content (plain
    text, or another tag sharing the line), return the tag's own span
    so that other content is left untouched.
 
    Returns (start, end) suitable for use as an apply_ops delete range."""
    ps, pe = paragraph_bounds(doc, span[0], span[1])
    if paragraph_has_other_content(doc, ps, pe, span):
        return span
    return (ps, pe)
 
 
def na_paragraph(text="N/A"):
    return (
        '<w:p><w:pPr><w:spacing w:line="240" w:lineRule="auto"/></w:pPr>'
        '<w:r><w:rPr><w:rFonts w:ascii="Calibri" w:cs="Calibri" w:eastAsia="Calibri" '
        f'w:hAnsi="Calibri"/><w:rtl w:val="0"/></w:rPr><w:t>{text}</w:t></w:r></w:p>'
    )
 
 
# ---------------------------------------------------------------------------
# Applying a batch of edits safely (descending order so earlier offsets
# stay valid; raises if any two edits overlap, which would indicate a bug
# in the caller's op-planning rather than something to silently paper over).
# ---------------------------------------------------------------------------
 
def apply_ops(doc: str, ops):
    """ops: list of (start, end, replacement_text, op_id). op_id can be
    anything hashable (or None if you don't need to track it) and is only
    used to report back where each replacement ended up post-edit.
 
    Returns (new_doc, {op_id: (final_start, final_end)}).
 
    Final positions are computed analytically (via cumulative length deltas
    of every earlier-starting op), not by re-scanning text, so this is safe
    even when a replacement's text is a substring of, or identical to,
    other content elsewhere in the document.
    """
    # Drop redundant nested deletions: a pure delete (empty replacement,
    # no op_id) strictly contained inside another empty-replacement delete
    # is a semantic no-op -- the outer deletion removes that content anyway.
    # Arises e.g. on a GROW deal with od_included_free=false: Phase 1 plans
    # tag-strip ops for kept-section tags ({{GROW_Only_Start}} etc.) that
    # sit inside the OD_Only_Or_Free block, then Phase 1b deletes that
    # whole block, which would otherwise trip the overlap check below.
    delete_ranges = [(s, e) for (s, e, repl, _oid) in ops if repl == "" and e > s]
    pruned = []
    for op in ops:
        s, e, repl, oid = op
        if repl == "" and oid is None and any(
            (S < s and e <= E) or (S <= s and e < E)
            for (S, E) in delete_ranges
        ):
            continue  # strictly contained in a bigger delete
        pruned.append(op)
    ops = pruned
 
    ops_sorted = sorted(ops, key=lambda o: o[0])
    for i in range(len(ops_sorted) - 1):
        if ops_sorted[i][1] > ops_sorted[i + 1][0]:
            raise TagEngineError(
                f"overlapping ops: {ops_sorted[i]} and {ops_sorted[i+1]}"
            )
 
    final_spans = {}
    cumulative_delta = 0
    for (s, e, repl, op_id) in ops_sorted:
        final_start = s + cumulative_delta
        final_end = final_start + len(repl)
        if op_id is not None:
            final_spans[op_id] = (final_start, final_end)
        cumulative_delta += len(repl) - (e - s)
 
    for (s, e, repl, _op_id) in sorted(ops, key=lambda o: -o[0]):
        doc = doc[:s] + repl + doc[e:]
    return doc, final_spans
 
 
# ---------------------------------------------------------------------------
# Highlight cleanup
# ---------------------------------------------------------------------------
 
def strip_highlight(doc: str, colors=("yellow", "green")) -> str:
    for c in colors:
        doc = re.sub(rf'<w:highlight w:val="{c}"/>', "", doc)
    return doc
 
 
# ---------------------------------------------------------------------------
# Comments — shells out to the docx public skill's comment.py so the
# comments.xml / commentsExtended.xml / etc. plumbing stays in one place.
# ---------------------------------------------------------------------------
 
class CommentAdder:
    def __init__(self, unpacked_dir: Path, docx_skill_scripts: Path,
                 author="Claude (MSA Skill)", initials="AI"):
        self.unpacked_dir = unpacked_dir
        self.comment_script = docx_skill_scripts / "comment.py"
        self.author = author
        self.initials = initials
        self._pending_markers = []  # (doc_start, doc_end, comment_id)
 
    def add(self, doc: str, span, text: str) -> str:
        """Registers a comment (writes comments.xml etc. immediately) and
        records a marker to be inserted into `doc` by flush_markers()."""
        result = subprocess.run(
            ["python3", str(self.comment_script), str(self.unpacked_dir), text,
             "--author", self.author, "--initials", self.initials],
            check=True, capture_output=True, text=True,
        )
        m = re.search(r'id=(\d+)', result.stdout)
        if not m:
            raise TagEngineError(f"comment.py did not report an id: {result.stdout}")
        comment_id = int(m.group(1))
        run_s, run_e = enclosing_run_bounds(doc, span[0], span[1])
        self._pending_markers.append((run_s, run_e, comment_id))
        return doc
 
    def flush_markers(self, doc: str) -> str:
        from collections import defaultdict
        by_pos = defaultdict(str)
        for (run_s, run_e, cid) in sorted(self._pending_markers, key=lambda x: x[0]):
            by_pos[run_s] += f'<w:commentRangeStart w:id="{cid}"/>'
            by_pos[run_e] += (
                f'<w:commentRangeEnd w:id="{cid}"/><w:r><w:rPr>'
                f'<w:rStyle w:val="CommentReference"/></w:rPr>'
                f'<w:commentReference w:id="{cid}"/></w:r>'
            )
        for pos in sorted(by_pos.keys(), reverse=True):
            doc = doc[:pos] + by_pos[pos] + doc[pos:]
        self._pending_markers = []
        return doc
 