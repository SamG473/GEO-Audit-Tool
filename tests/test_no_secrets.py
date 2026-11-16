"""The app as a fresh deployment sees it: no OPENAI_API_KEY, no AUDIT_PASSWORD,
no .env file and no geo.db. It must start, and the whole demo must work."""
import os
import subprocess
import sys
import unittest

from helpers import PASSWORD, REPO, TempDirTest, unlock_live
from streamlit.testing.v1 import AppTest

import dotenv
import openai

import modes


def html(at):
    return "\n".join(m.value for m in at.markdown)


class NoSecretsTest(TempDirTest):
    def setUp(self):
        super().setUp()                                   # empty temp dir: no geo.db, no latest_run.json
        self._key = os.environ.pop("OPENAI_API_KEY", None)
        os.environ["AUDIT_PASSWORD"] = ""
        # the repo's own .env must not quietly supply a key on a developer machine
        self._load_dotenv, dotenv.load_dotenv = dotenv.load_dotenv, lambda *a, **k: False

    def tearDown(self):
        dotenv.load_dotenv = self._load_dotenv
        if self._key is not None:
            os.environ["OPENAI_API_KEY"] = self._key
        super().tearDown()

    def test_the_environment_really_has_no_key(self):
        with self.assertRaises(openai.OpenAIError):
            openai.OpenAI()

    def test_modules_import_without_a_key(self):
        env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "AUDIT_PASSWORD")}
        env["PYTHONPATH"] = REPO
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        code = ("import dotenv; dotenv.load_dotenv = lambda *a, **k: False\n"
                "import detection, querygen, extraction, modes\n"
                "assert detection._client is None\n"
                "print(detection.detect('Northgate Barbers', 'Try Northgate.', 'barber', 'Cardiff'))")
        done = subprocess.run([sys.executable, "-c", code], env=env, cwd=os.getcwd(),
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr[-800:])
        self.assertEqual(done.stdout.strip(), "True")

    def test_app_starts_and_the_whole_demo_works(self):
        at = AppTest.from_file(os.path.join(REPO, "app.py"), default_timeout=60).run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertIn(modes.DEMO_LABEL, html(at))
        self.assertEqual([b.key for b in at.button], ["btn_write"])       # no live-audit panel either

        at.button(key="btn_write").click().run()                          # review screen
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertIn("Review your test questions", html(at))

        at.button(key="btn_run").click().run()                            # loading page, then results
        self.assertFalse(at.exception, [e.value for e in at.exception])
        page = html(at)
        self.assertIn("Northgate Barbers appears in 6 of 10 AI search answers", page)
        self.assertIn("Northgate Barbers is 2nd of the 8 businesses the AI mentioned", page)
        self.assertEqual([t.label for t in at.tabs], ["Your visibility", "You vs competitors", "What's next"])
        self.assertEqual(os.listdir("."), [])                             # nothing written: still no geo.db

    def test_results_page_opens_directly(self):
        at = AppTest.from_file(os.path.join(REPO, "pages", "results.py"), default_timeout=30).run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertIn("Northgate Barbers", html(at))

    def test_live_audit_without_a_key_fails_with_a_clear_message(self):
        os.environ["AUDIT_PASSWORD"] = PASSWORD                           # password set, key forgotten
        at = unlock_live(AppTest.from_file(os.path.join(REPO, "app.py"), default_timeout=30).run())
        at.text_input(key="in_business").set_value("Lazarou Barbers")
        at.text_input(key="in_town").set_value("Cardiff")
        at.button(key="btn_write").click().run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertIn("No OpenAI API key is set", at.error[0].value)


if __name__ == "__main__":
    unittest.main()
