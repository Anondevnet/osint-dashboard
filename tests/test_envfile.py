"""Local .env loading. Uses a temporary file and a fake key."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from envfile import create_key_file, load_env, main  # noqa: E402

FAKE_KEY = "TESTSHODANKEY1234"


class EnvFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self._previous = os.environ.get("SHODAN_API_KEY")
        self._extra = os.environ.get("NOT_A_REAL_SETTING")
        os.environ.pop("SHODAN_API_KEY", None)
        os.environ.pop("NOT_A_REAL_SETTING", None)

    def tearDown(self) -> None:
        if self._previous is None:
            os.environ.pop("SHODAN_API_KEY", None)
        else:
            os.environ["SHODAN_API_KEY"] = self._previous
        if self._extra is None:
            os.environ.pop("NOT_A_REAL_SETTING", None)
        else:
            os.environ["NOT_A_REAL_SETTING"] = self._extra
        self.tmp.cleanup()

    def test_missing_file_loads_nothing(self) -> None:
        result = load_env(self.directory / "missing.env")
        self.assertFalse(result["present"])
        self.assertEqual(result["applied"], [])
        self.assertIsNone(os.environ.get("SHODAN_API_KEY"))

    def test_process_environment_wins_and_unknown_names_are_ignored(self) -> None:
        path = self.directory / "local.env"
        path.write_text(
            "\n# comment\nSHODAN_API_KEY=\"%s\"\nNOT_A_REAL_SETTING=hidden\nexport DETROIT_SYNC_INTERVAL_SECONDS=300\n"
            % FAKE_KEY
        )
        os.environ["SHODAN_API_KEY"] = "OTHERSHODANKEY999"
        result = load_env(path)
        self.assertEqual(result["applied"], ["DETROIT_SYNC_INTERVAL_SECONDS"])
        self.assertEqual(os.environ["SHODAN_API_KEY"], "OTHERSHODANKEY999")
        self.assertNotIn("NOT_A_REAL_SETTING", os.environ)
        self.assertEqual(os.environ["DETROIT_SYNC_INTERVAL_SECONDS"], "300")
        os.environ.pop("DETROIT_SYNC_INTERVAL_SECONDS", None)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_create_refuses_to_overwrite_and_hides_the_key(self) -> None:
        path = self.directory / "local.env"
        path.write_text("SHODAN_API_KEY=KEEPTHISKEY000000\n")
        before = path.read_bytes()
        with self.assertRaises(FileExistsError):
            create_key_file(path, FAKE_KEY)
        self.assertEqual(path.read_bytes(), before)
        missing = self.directory / "fresh.env"
        refused = create_key_file(missing, "bad key")
        self.assertFalse(refused["configured"])
        self.assertEqual(refused["key_length"], 0)
        self.assertNotIn("key", refused)
        self.assertFalse(missing.exists())
        created = create_key_file(missing, FAKE_KEY)
        self.assertEqual(created, {"configured": True, "key_length": len(FAKE_KEY)})
        self.assertNotIn(FAKE_KEY, str(created))
        self.assertEqual(stat.S_IMODE(missing.stat().st_mode), 0o600)
        self.assertEqual(missing.read_text(), "SHODAN_API_KEY=%s\n" % FAKE_KEY)

    def test_cli_reports_length_without_the_key_and_script_refuses_overwrite(self) -> None:
        path = self.directory / "cli.env"
        completed = subprocess.run(
            [sys.executable, "-m", "envfile", "create", str(path)],
            input=FAKE_KEY + "\n",
            capture_output=True,
            text=True,
            cwd=ROOT,
            check=False,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertIn("configured: true", completed.stdout)
        self.assertIn("key_length: %s" % len(FAKE_KEY), completed.stdout)
        self.assertNotIn(FAKE_KEY, completed.stdout)
        self.assertNotIn(FAKE_KEY, completed.stderr)
        again = main(["envfile", "create", str(path)])
        self.assertEqual(again, 2)
        script = ROOT / "scripts" / "configure-shodan-key.sh"
        text = script.read_text()
        self.assertIn("read -r -s KEY", text)
        self.assertNotIn('echo "$KEY"', text)
        self.assertNotIn("echo $KEY", text)
        environment = os.environ.copy()
        environment.pop("SHODAN_API_KEY", None)
        environment["SHODAN_ENV_FILE"] = str(path)
        refused = subprocess.run(
            ["bash", str(script)],
            capture_output=True,
            text=True,
            cwd=ROOT,
            env=environment,
            check=False,
        )
        self.assertEqual(refused.returncode, 2)
        self.assertNotIn(FAKE_KEY, refused.stdout)
        self.assertNotIn(FAKE_KEY, refused.stderr)
        self.assertIn("Refusing to overwrite", refused.stderr)

    def test_server_loads_the_env_file_before_serving(self) -> None:
        source = (ROOT / "server.py").read_text()
        load_at = source.index("load_env(ROOT / \".env\")")
        serve_at = source.index("httpd.serve_forever()")
        self.assertLess(load_at, serve_at)
        gitignore = (ROOT / ".gitignore").read_text()
        self.assertIn(".env", gitignore)


if __name__ == "__main__":
    unittest.main()
