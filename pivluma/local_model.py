"""Asynchronous client for a user-selected model on the local Ollama service."""

import json

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
        + ("Turkish." if language == "tr" else "English.")
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


class LocalModel(QObject):
    completed = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.network = QNetworkAccessManager(self)
        self.network.setProxy(QNetworkProxy(QNetworkProxy.ProxyType.NoProxy))
        self.reply = None

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
