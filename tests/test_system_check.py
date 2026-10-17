from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import system_check


class BundledExecutableDiscoveryTests(unittest.TestCase):
    def test_frozen_app_prefers_resources_bin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory)
            resources_bin = bundle / "Resources" / "bin"
            resources_bin.mkdir(parents=True)
            expected: dict[str, Path] = {}
            for name in ("ddjvu", "djvused", "djvudump", "jbig2"):
                executable = resources_bin / name
                executable.write_text("#!/bin/sh\n", encoding="utf-8")
                executable.chmod(0o755)
                expected[name] = executable.resolve()

            with (
                patch.object(system_check.sys, "frozen", True, create=True),
                patch.object(system_check.sys, "_MEIPASS", str(bundle), create=True),
                patch.object(system_check.sys, "executable", str(bundle / "Contents/MacOS/app")),
                patch("core.system_check.shutil.which", return_value=None),
            ):
                tools = system_check.find_tools()

        self.assertEqual(tools.ddjvu, expected["ddjvu"])
        self.assertEqual(tools.djvused, expected["djvused"])
        self.assertEqual(tools.djvudump, expected["djvudump"])
        self.assertEqual(tools.jbig2, expected["jbig2"])


if __name__ == "__main__":
    unittest.main()
