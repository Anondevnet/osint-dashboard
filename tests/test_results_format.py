import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ResultsFormatTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_plain_language_formatting(self):
        run = subprocess.run(
            ["node", str(ROOT / "tests" / "results_format_harness.js")],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(run.returncode, 0, run.stderr or run.stdout)
        self.assertIn("results-format-ok", run.stdout)

    def test_results_script_is_served_before_app(self):
        page = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<script src="/static/results.js"></script>', page)
        self.assertLess(page.index("/static/results.js"), page.index("/static/app.js"))
        self.assertLess(page.index("/static/detect.js"), page.index("/static/app.js"))

    def test_results_script_has_no_inline_styles_or_remote_code(self):
        for name in ("results.js", "app.js"):
            source = (ROOT / "static" / name).read_text(encoding="utf-8")
            self.assertNotIn("innerHTML", source, name)
            self.assertNotIn("eval(", source, name)
        source = (ROOT / "static" / "results.js").read_text(encoding="utf-8")
        self.assertNotIn("fetch(", source)
        self.assertNotIn("document.", source)


if __name__ == "__main__":
    unittest.main()
