from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from create_dmg import create_dmg


class DmgBuildTests(unittest.TestCase):
    def test_stages_app_and_applications_link_and_verifies_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_bundle = root / "dist" / "djvu2pdf.app"
            app_bundle.mkdir(parents=True)
            (app_bundle / "Contents").write_text("app", encoding="utf-8")

            def run(command: list[str], **_kwargs: object) -> None:
                if command[1] == "create":
                    staging = Path(command[command.index("-srcfolder") + 1])
                    self.assertTrue((staging / "djvu2pdf.app" / "Contents").is_file())
                    self.assertEqual(
                        (staging / "Applications").resolve(),
                        Path("/Applications").resolve(),
                    )
                    Path(command[-1]).write_bytes(b"test image")

            with (
                patch("create_dmg.sys.platform", "darwin"),
                patch("create_dmg.shutil.which", return_value="/usr/bin/hdiutil"),
                patch("create_dmg.subprocess.run", side_effect=run) as run_command,
            ):
                destination = create_dmg(root)

            self.assertEqual(
                destination,
                (root / "dist" / "djvu2pdf-macOS.dmg").resolve(),
            )
            self.assertEqual(destination.read_bytes(), b"test image")
            self.assertEqual(run_command.call_count, 2)
            self.assertEqual(run_command.call_args_list[-1].args[0][1], "verify")


if __name__ == "__main__":
    unittest.main()
