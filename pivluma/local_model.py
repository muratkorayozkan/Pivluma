"""Asynchronous clients for the managed llama.cpp process and cloud gateway."""

import json
import secrets
import socket
from time import monotonic
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QProcess, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkProxy, QNetworkReply, QNetworkRequest


from pivluma.local_assets import ready
from pivluma.llama_forecast import FORECAST_TASK, forecast_request


LOCAL_MODEL = "llama"


def chat_request(model, language, question, facts):
    if model != LOCAL_MODEL:
        raise ValueError("Unsupported local model.")
    language_name = {"tr": "Turkish", "zh": "Simplified Chinese"}.get(language, "English")
    instruction = (
        f"Answer only the question using the supplied facts. Use up to four concise lines in {language_name}. "
        "Keep numbers and units accurate. Do not suggest trades or imply certainty. "
    )
    if facts.get("analysis_topic") == "research":
        instruction += (
            "Compare subsequent returns after positive and negative research scores and state the sample sizes. "
            "Use the words median and range for historical pattern outcomes. "
            "Distinguish historical association from causation. Do not invent article text, events, or forecasts."
        )
    else:
        instruction += "For the final forecast and its bounds, use final_forecast when available."
    facts = dict(facts)
    if forecast := facts.get("forecast"):
        facts = {"final_forecast": forecast[-1], **facts}
    return {
        "model": model,
        "stream": False,
        "temperature": 0,
        "max_tokens": 300,
        "messages": [
            {"role": "system", "content": instruction},
            {"role": "user", "content": json.dumps({"question": question, "facts": facts}, ensure_ascii=False)},
        ],
    }


def cloud_endpoint(address):
    try:
        parsed = urlsplit(address.strip())
    except ValueError:
        return ""
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment):
        return ""
    return address.strip().rstrip("/") + "/v1/analyst"


class LocalModel(QObject):
    completed = Signal(str, str)

    def __init__(self, root, parent=None):
        super().__init__(parent)
        self.root = root
        self.process = QProcess(self)
        self.process.setStandardOutputFile(QProcess.nullDevice())
        self.process.setStandardErrorFile(QProcess.nullDevice())
        self.idle = QTimer(self, singleShot=True, interval=120000)
        self.idle.timeout.connect(self.stop_engine)
        self.generation = 0
        self.endpoint = ""
        self.token = ""
        self.network = QNetworkAccessManager(self)
        self.network.setProxy(QNetworkProxy(QNetworkProxy.ProxyType.NoProxy))
        self.reply = None
        self.cloud_error = ""

    def cancel(self):
        self.generation += 1
        active, self.reply = self.reply, None
        if active is not None:
            active.abort()
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.idle.start()

    def stop_engine(self):
        self.cancel()
        self.idle.stop()
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
            self.process.waitForFinished(3000)

    def start(self, model, language, question, facts):
        self.start_local(model, chat_request(model, language, question, facts))

    def start_forecast(self, facts):
        self.start_local(FORECAST_TASK, forecast_request(facts))

    def start_local(self, model, payload):
        self.cancel()
        self.idle.stop()
        if not ready(self.root):
            self.completed.emit(model, "")
            return
        if self.process.state() == QProcess.ProcessState.NotRunning:
            try:
                with socket.socket() as listener:
                    listener.bind(("127.0.0.1", 0))
                    port = listener.getsockname()[1]
            except OSError:
                self.completed.emit(model, "")
                return
            self.endpoint = f"http://127.0.0.1:{port}"
            self.token = secrets.token_urlsafe(32)
            self.process.setWorkingDirectory(str(self.root))
            self.process.start(str(self.root / "llama-server.exe"), [
                "--model", str(self.root / "model.gguf"), "--host", "127.0.0.1",
                "--port", str(port), "--api-key", self.token, "--ctx-size", "8192",
                "--parallel", "1", "--threads", "4", "--n-gpu-layers", "0", "--reasoning", "off",
            ])
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.wait_until_ready(self.generation, monotonic() + 30, body, model)

    def request(self, route, timeout):
        request = QNetworkRequest(QUrl(self.endpoint + route))
        request.setRawHeader(b"Authorization", f"Bearer {self.token}".encode())
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        request.setTransferTimeout(timeout)
        return request

    def wait_until_ready(self, generation, deadline, body, model):
        if generation != self.generation:
            return
        if monotonic() >= deadline or self.process.state() == QProcess.ProcessState.NotRunning:
            self.stop_engine()
            self.completed.emit(model, "")
            return
        reply = self.network.get(self.request("/health", 2000))
        self.reply = reply
        reply.finished.connect(lambda: self.engine_health(reply, generation, deadline, body, model))

    def engine_health(self, reply, generation, deadline, body, model):
        reply.deleteLater()
        if reply is not self.reply:
            return
        self.reply = None
        if reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) != 200:
            QTimer.singleShot(250, self, lambda: self.wait_until_ready(generation, deadline, body, model))
            return
        request = self.request("/v1/chat/completions", 90000)
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        response = self.network.post(request, body)
        self.reply = response
        response.finished.connect(lambda: self.finish(response, model))

    def start_cloud(self, address, code, language, question, facts):
        self.cancel()
        self.cloud_error = ""
        url = cloud_endpoint(address)
        if not url or not 32 <= len(code) <= 256 or not code.isascii() or not code.isprintable() or " " in code:
            self.cloud_error = "Enter a valid HTTPS server and cloud access code in Settings."
            self.completed.emit("cloud", "")
            return
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"Authorization", f"Bearer {code}".encode("ascii"))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        request.setTransferTimeout(50000)
        body = json.dumps({"language": language, "question": question, "facts": facts}, ensure_ascii=False)
        reply = self.network.post(request, body.encode())
        self.reply = reply
        reply.finished.connect(lambda: self.finish_cloud(reply))

    def finish_cloud(self, reply):
        reply.deleteLater()
        if reply is not self.reply:
            return
        self.reply = None
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        errors = {
            401: "The cloud analyst did not accept the access code.",
            429: "The cloud analyst daily limit has been reached.",
        }
        self.cloud_error = errors.get(status, "Cloud analyst unavailable. The built-in result is shown.")
        try:
            if status != 200 or reply.bytesAvailable() > 262144:
                raise ValueError()
            result = json.loads(bytes(reply.readAll()))["answer"].strip()
            if not result:
                raise ValueError()
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            result = ""
        self.completed.emit("cloud", result)

    def finish(self, reply, model):
        reply.deleteLater()
        if reply is not self.reply:
            return
        self.reply = None
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                raise ValueError()
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            if status != 200 or reply.bytesAvailable() > 262144:
                raise ValueError()
            result = json.loads(bytes(reply.readAll()))["choices"][0]["message"]["content"].strip()
            if not result:
                raise ValueError()
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            result = ""
        self.idle.start()
        self.completed.emit(model, result)
