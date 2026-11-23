"""Tests ohne echte API-Aufrufe: Generator, Wortauswahl und Claude-Prüfung mit nachgebautem Client.

Aufruf im Projektordner:  .venv\\Scripts\\python -m unittest discover tests
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import types
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from sr import claude_check as cc  # noqa: E402
from sr import generator  # noqa: E402
from sr.puzzle import validate  # noqa: E402
from sr.service import AVOID_TAG, word_pool  # noqa: E402
from sr.words import EXAMPLE_CSV, ensure_db, load_csv  # noqa: E402

ENTRIES = load_csv(os.path.join(HERE, EXAMPLE_CSV))


class FakeStream:
    """Antwortet wie Claude im vorgegebenen JSON-Format: jeder siebte Begriff bekommt ★1 und einen Vorschlag."""

    calls = []

    def __init__(self, kw):
        self.kw = kw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        kw = self.kw
        FakeStream.calls.append(kw)
        schema = kw["output_config"]["format"]["schema"]
        user = kw["messages"][0]["content"]
        if "ergebnisse" in schema["properties"]:
            rows = [json.loads(line) for line in user.splitlines()[1:]]
            out = {"ergebnisse": [{
                "id": r["id"], "bewertung": 1 if r["id"] % 7 == 0 else 4,
                "probleme": ["zu_lang"] if r["id"] % 7 == 0 else [], "hinweis": "",
                "frage_vorschlag": "Neue Frage" if r["id"] % 7 == 0 else "",
                "tags_dazu": ["testtag"] if r["id"] % 7 == 0 else [], "tags_weg": []} for r in rows]}
        else:
            ids = [json.loads(line)["id"] for line in user.split("je Zeile):\n", 1)[1].splitlines()]
            out = {"note": 7, "fazit": "ok", "themenbezug": "gut", "staerken": [], "verbesserungen": [],
                   "woerter": [{"id": ids[0], "problem": "themenfremd", "hinweis": "", "frage_vorschlag": ""}]}
        usage = types.SimpleNamespace(input_tokens=1000, output_tokens=500, cache_creation_input_tokens=0,
                                      cache_read_input_tokens=0)
        return types.SimpleNamespace(stop_reason="end_turn", usage=usage,
                                     content=[types.SimpleNamespace(type="text", text=json.dumps(out))])


class FakeClient:
    beta = types.SimpleNamespace(messages=types.SimpleNamespace(stream=lambda **kw: FakeStream(kw)))


class GeneratorTest(unittest.TestCase):
    def test_raetsel_ist_regelkonform(self):
        p, _ = generator.generate(word_pool(ENTRIES, set()), 9, 9, 4)
        self.assertIsNotNone(p)
        self.assertEqual(validate(p), [])
        self.assertEqual(p.gray_cells(), 0)


class WortauswahlTest(unittest.TestCase):
    def test_meiden_tag_und_skip(self):
        es = [e for e in ENTRIES[:50]]
        es[0].tags = es[0].tags + [AVOID_TAG]
        try:
            pool = word_pool(es, set())
            self.assertNotIn(es[0], pool)
            pool2 = word_pool(es, set(), skip=lambda e: e.id == es[1].id)
            self.assertNotIn(es[1], pool2)
        finally:
            es[0].tags.remove(AVOID_TAG)

    def test_beispiel_datenbank_wird_angelegt(self):
        d = tempfile.mkdtemp()
        try:
            shutil.copy(os.path.join(HERE, EXAMPLE_CSV), d)
            path = os.path.join(d, "woerter.csv")
            ensure_db(path)
            self.assertEqual(len(load_csv(path)), len(ENTRIES))
        finally:
            shutil.rmtree(d, ignore_errors=True)


class ClaudePruefungTest(unittest.TestCase):
    def setUp(self):
        self._orig = cc.make_client
        cc.make_client = lambda: FakeClient()
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        cc.make_client = self._orig
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_schemas_ohne_nicht_unterstuetzte_regeln(self):
        for s in (cc._db_schema(), cc._puzzle_schema()):
            txt = json.dumps(s)
            for word in ("minimum", "maximum", "minLength", "maxLength"):
                self.assertNotIn(word, txt)

    def test_datenbank_pruefung_nur_aenderungen(self):
        es = [load_csv(os.path.join(HERE, EXAMPLE_CSV))[k] for k in range(130)]
        store = cc.ReviewStore(os.path.join(self.dir, "pruefung.json"))
        self.assertEqual(len(store.pending(es)), 130)

        def on_batch(res, fps, cost, err):
            self.assertIsNone(err)
            for r in res:
                store.put(fps[r["id"]], r)
            store.total_cost += cost

        cc.review_db(es, es, on_batch, threading.Event())
        self.assertEqual(store.pending(es), [])
        self.assertGreater(store.total_cost, 0)
        store.save()

        reloaded = cc.ReviewStore(os.path.join(self.dir, "pruefung.json"))
        self.assertEqual(reloaded.pending(es), [])
        es[0].frage += " (geändert)"
        self.assertEqual([e.id for e in reloaded.pending(es)], [es[0].id])

        bad = [e for e in es if reloaded.is_bad(e)]
        self.assertTrue(bad)
        n_all = len(word_pool(es, set()))
        self.assertEqual(len(word_pool(es, set(), skip=reloaded.is_bad)), n_all - len(bad))

    def test_raetsel_gutachten(self):
        p, _ = generator.generate(word_pool(ENTRIES, set()), 9, 9, 4)
        facts = cc.puzzle_facts(p, {"tiere"})
        data, cost = cc.review_puzzle(p, {"tiere"}, "Test", facts, hints="\ntiere = Tiere")
        self.assertEqual(data["note"], 7)
        self.assertIn("Bedeutung einzelner Tags", FakeStream.calls[-1]["messages"][0]["content"])
        self.assertIn("Themenwörter", cc.facts_text(facts))


if __name__ == "__main__":
    unittest.main()
