import os
import shutil
import struct
import tempfile
import unittest

from csp_story.story import Change, apply_changes, collect, from_document, to_document
from csp_story.textattrs import (build_tlv, make_remap, parse_tlv, rewrite_attributes,
                                 u16len)

SAMPLE = os.path.join(os.path.dirname(__file__), "..", "Comic")


def runs(*rs):
    """Range record: count + (start, len, size=2, payload)."""
    return struct.pack("<I", len(rs)) + b"".join(
        struct.pack("<III", s, n, 2) + p for s, n, p in rs)


class TextAttrsTests(unittest.TestCase):
    def test_u16len_counts_crlf_and_surrogates(self):
        self.assertEqual(u16len("ab\ncd"), 6)
        self.assertEqual(u16len("a😀"), 3)

    def test_remap(self):
        r = make_remap("hello world", "hello big world")
        self.assertEqual(r(0), 0)
        self.assertEqual(r(6), 10)         # "w" of "world" shifts past the insertion
        self.assertEqual(r(11), 15)        # end

    def test_single_run_resized(self):
        blob = build_tlv([(0x0C, runs((0, 5, b"\x02\x00"))), (0x27, struct.pack("<II", 5, 0))])
        out = dict(parse_tlv(rewrite_attributes(blob, "hello", "hello!!")))
        self.assertEqual(struct.unpack_from("<III", out[0x0C]), (1, 0, 7))

    def test_caret_clamped(self):
        blob = build_tlv([(0x0C, runs((0, 5, b"AA"))), (0x27, struct.pack("<II", 4, 1))])
        out = dict(parse_tlv(rewrite_attributes(blob, "hello", "hi")))
        self.assertEqual(struct.unpack("<II", out[0x27]), (2, 0))

    def test_multi_run_follows_edit(self):
        # "big" is styled differently: "a big dog" -> runs [0,2) [2,5) [5,9)
        blob = build_tlv([(0x0C, runs((0, 2, b"AA"), (2, 3, b"BB"), (5, 4, b"AA")))])
        out = dict(parse_tlv(rewrite_attributes(blob, "a big dog", "a bigger dogs")))[0x0C]
        count, = struct.unpack_from("<I", out)
        got = [struct.unpack_from("<II", out, 4 + i * 14) for i in range(count)]
        self.assertEqual(got[0], (0, 2))
        self.assertEqual(got[-1][0] + got[-1][1], u16len("a bigger dogs"))


class DocumentTests(unittest.TestCase):
    def test_parse_tolerates_crlf_and_page_comments(self):
        doc = "# hdr\r\n\r\n[[2:6:0]]\r\nHi\r\nthere\r\n\r\n# ---- Page 3 ----\r\n\r\n[[3:1:0]]\r\nX\r\n"
        self.assertEqual(from_document(doc), {"2:6:0": "Hi\nthere", "3:1:0": "X"})

    def test_hidden_marker_roundtrips(self):
        from csp_story.story import TextObject
        objs = [TextObject(1, "p.clip", 5, 0, "", "secret", hidden=True),
                TextObject(1, "p.clip", 6, 0, "", "shown")]
        doc = to_document(objs)
        self.assertIn("[[1:5:0 hidden]]\nsecret", doc)
        self.assertIn("[[1:6:0]]\nshown", doc)
        self.assertEqual(from_document(doc), {"1:5:0": "secret", "1:6:0": "shown"})


@unittest.skipUnless(os.path.exists(os.path.join(SAMPLE, "Comic.cmc")), "sample comic not present")
class RoundTripTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        shutil.copytree(SAMPLE, os.path.join(self.dir, "c"))
        self.cmc = os.path.join(self.dir, "c", "Comic.cmc")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_roundtrip(self):
        objs = collect(self.cmc)
        self.assertEqual(from_document(to_document(objs)), {o.key: o.text for o in objs})
        edits = {o.key: o.text.upper() + " é" for o in objs}
        log, pages = apply_changes([Change(o, edits[o.key]) for o in objs], backup=False)
        self.assertEqual(pages, {2})
        self.assertFalse([l for l in log if l.startswith("SKIP")], log)
        self.assertEqual({o.key: o.text for o in collect(self.cmc)}, edits)

    def test_skips_if_edited_since_export(self):
        objs = collect(self.cmc)
        stale = objs[0]
        stale.text = "something else"
        log, _ = apply_changes([Change(stale, "x")], backup=False)
        self.assertIn("edited in Clip Studio", log[0])


if __name__ == "__main__":
    unittest.main()
