import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pivluma.local_assets import download


class LocalAssetTests(unittest.TestCase):
    def test_download_requires_matching_size_and_sha256(self):
        contents = b"test runtime"
        digest = hashlib.sha256(contents).hexdigest()
        with tempfile.TemporaryDirectory() as root:
            destination = Path(root) / "runtime.zip"
            for expected_size, expected_digest in ((len(contents) - 1, digest), (len(contents), "0" * 64)):
                with patch("pivluma.local_assets.urlopen", return_value=io.BytesIO(contents)):
                    with self.assertRaises(ValueError):
                        download(("https://example.test/runtime", expected_size, expected_digest),
                                 destination, lambda _: None, lambda: False)
            with patch("pivluma.local_assets.urlopen", return_value=io.BytesIO(contents)):
                download(("https://example.test/runtime", len(contents), digest),
                         destination, lambda _: None, lambda: False)
            self.assertEqual(destination.read_bytes(), contents)
