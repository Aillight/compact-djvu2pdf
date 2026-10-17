from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from core.converter import ConversionProfile


class CommandLineConversionTests(unittest.TestCase):
    def test_convert_command_uses_auto_profile_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.djvu"
            output = root / "output.pdf"
            source.write_bytes(b"source")
            expected_result = output.resolve()
            stdout = io.StringIO()

            with (
                patch("main.find_tools") as find_tools,
                patch("main.convert", return_value=expected_result) as convert,
                contextlib.redirect_stdout(stdout),
            ):
                result = main._convert_from_command_line([str(source), str(output)])

        self.assertEqual(result, 0)
        self.assertEqual(convert.call_args.args, (source, output))
        self.assertEqual(convert.call_args.kwargs["profile"], ConversionProfile.AUTO)
        self.assertIs(convert.call_args.kwargs["tools"], find_tools.return_value)
        self.assertIn("PDF создан:", stdout.getvalue())

    def test_convert_command_accepts_explicit_profile(self) -> None:
        with (
            patch("main.find_tools"),
            patch("main.convert", return_value=Path("/tmp/output.pdf")) as convert,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = main._convert_from_command_line(
                ["/tmp/source.djvu", "/tmp/output.pdf", "--profile", "clean-black-and-white"]
            )

        self.assertEqual(result, 0)
        self.assertEqual(
            convert.call_args.kwargs["profile"],
            ConversionProfile.CLEAN_BLACK_AND_WHITE,
        )


if __name__ == "__main__":
    unittest.main()
