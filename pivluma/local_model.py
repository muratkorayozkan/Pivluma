"""Asynchronous client for a user-selected model on the local Ollama service."""

import json
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkProxy, QNetworkReply, QNetworkRequest


MODELS = ("qwen3:1.7b", "qwen3:4b", "qwen3:8b")
CHAT_URL = "http://127.0.0.1:11434/api/chat"


def chat_request(model, language, question, facts):
    if model not in MODELS:
        raise ValueError("Unsupported local model.")
    instruction = (
        "Explain the supplied market data clearly. Use only the provided facts and prices. "
        "Keep the values, units, dates and limits unchanged. State when information is missing. "
        "Do not suggest trades or imply a forecast is certain. Use "
        + {"tr": "Turkish.", "zh": "Simplified Chinese."}.get(language, "English.")
    )
    return {
        "model": model,
        "stream": False,
        "think": False,
        "options": {"temperature": .15, "num_predict": 260},
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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.network = QNetworkAccessManager(self)
        self.network.setProxy(QNetworkProxy(QNetworkProxy.ProxyType.NoProxy))
        self.reply = None
        self.cloud_error = ""

    def cancel(self):
        active, self.reply = self.reply, None
        if active is not None:
            active.abort()

    def start(self, model, language, question, facts):
        self.cancel()
        request = QNetworkRequest(QUrl(CHAT_URL))
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        request.setTransferTimeout(90000)
        body = json.dumps(chat_request(model, language, question, facts), ensure_ascii=False)
        reply = self.network.post(request, body.encode())
        self.reply = reply
        reply.finished.connect(lambda: self.finish(reply, model))

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
        except (AttributeError, KeyError, TypeError, ValueError):
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
            result = json.loads(bytes(reply.readAll()))["message"]["content"].strip()
            if not result:
                raise ValueError()
        except (AttributeError, KeyError, TypeError, ValueError):
            result = ""
        self.completed.emit(model, result)
