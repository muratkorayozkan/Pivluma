import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile
from urllib.error import HTTPError, URLError
import ssl

from pivluma.updates import ROOT, check_update, latest_update, prepare_update, update_error_message


def package(entries):
    stream = io.BytesIO()
    with ZipFile(stream, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return stream.getvalue()


def release(data, version="0.6.0"):
    name = f"Pivluma-{version}-Windows.zip"
    return {
        "tag_name": f"v{version}", "draft": False, "prerelease": False,
        "assets": [{"name": name, "state": "uploaded", "size": len(data),
                    "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                    "browser_download_url": f"{ROOT}/releases/download/v{version}/{name}"}],
    }


class UpdateTests(unittest.TestCase):
    def test_only_a_newer_verified_release_is_selected(self):
        payload = release(b"archive")
        self.assertIsNone(latest_update(payload, "0.10.0"))
        update = latest_update(payload, "0.5.0")
        self.assertEqual(update.version, "0.6.0")
        payload["assets"][0]["browser_download_url"] = "https://example.com/archive"
        self.assertIsNone(latest_update(payload, "0.5.0"))

    def test_no_release_and_connection_failures_have_distinct_results(self):
        with patch("pivluma.updates.urlopen", side_effect=HTTPError("", 404, "", {}, None)):
            self.assertIsNone(check_update("0.5.0"))
        self.assertIn("rate limit", update_error_message(HTTPError("", 429, "", {}, None)))
        self.assertIn("certificate", update_error_message(
            URLError(ssl.SSLCertVerificationError("untrusted"))
        ))
        self.assertIn("network", update_error_message(URLError("offline")))

    def test_package_is_prepared_without_overwriting_existing_versions(self):
        data = package({"Pivluma/Pivluma.exe": b"exe", "Pivluma/_internal/python.dll": b"dll"})
        update = latest_update(release(data), "0.5.0")
        with tempfile.TemporaryDirectory() as directory, patch(
            "pivluma.updates.urlopen", side_effect=lambda *args, **kwargs: io.BytesIO(data)
        ):
            root = Path(directory)
            old = root / "0.5.0"
            old.mkdir()
            first = prepare_update(update, root)
            second = prepare_update(update, root)
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_bytes(), b"exe")
            self.assertEqual((first.parent / "_internal/python.dll").read_bytes(), b"dll")
            self.assertTrue(old.is_dir())

    def test_bad_digest_and_unsafe_paths_leave_no_install(self):
        for data, substitute in [
            (package({"Pivluma/Pivluma.exe": b"exe"}), b"wrong"),
            (package({"Pivluma/Pivluma.exe": b"exe", "Pivluma/../evil": b"bad"}), None),
        ]:
            update = latest_update(release(data), "0.5.0")
            with self.subTest(substitute=substitute), tempfile.TemporaryDirectory() as directory, patch(
                "pivluma.updates.urlopen", return_value=io.BytesIO(data if substitute is None else substitute)
            ):
                root = Path(directory)
                with self.assertRaises(ValueError):
                    prepare_update(update, root)
                self.assertEqual(list(root.iterdir()), [])
