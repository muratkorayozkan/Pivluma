import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.cloud_server import DailyQuota, make_app


class CloudTests(unittest.TestCase):
    def test_access_and_daily_quota(self):
        token = "pivluma-test-" + "x" * 32
        identity = hashlib.sha256(token.encode()).hexdigest()
        body = json.dumps({"language": "tr", "question": "Tahmin nedir?", "facts": {"horizon": 5}}).encode()
        with tempfile.TemporaryDirectory() as directory, patch(
            "server.cloud_server.generate", return_value="Yerel hesaplanan değerler açıklandı."
        ) as generate:
            app = make_app("test-only", [identity], DailyQuota(Path(directory) / "quota.sqlite3", 1, 2))

            def request(code):
                response = []
                payload = app({
                    "PATH_INFO": "/v1/analyst", "REQUEST_METHOD": "POST",
                    "HTTP_AUTHORIZATION": "Bearer " + code, "CONTENT_LENGTH": str(len(body)),
                    "wsgi.input": io.BytesIO(body),
                }, lambda status, headers: response.append(status))
                return response[0], json.loads(b"".join(payload))

            self.assertTrue(request("unrecognized")[0].startswith("401"))
            self.assertEqual(request(token)[1], {"answer": "Yerel hesaplanan değerler açıklandı."})
            self.assertTrue(request(token)[0].startswith("429"))
            generate.assert_called_once()
