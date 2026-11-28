"""Download pinned Windows llama.cpp and Qwen weights into per-user storage."""
import hashlib
import platform
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import Request, urlopen
from zipfile import ZipFile

RUNTIME_URL = "https://github.com/ggml-org/llama.cpp/releases/download/b12823/llama-b12823-bin-win-cpu-x64.zip"
MODEL_URL = ("https://huggingface.co/ggml-org/Qwen3-1.7B-GGUF/resolve/"
             "daeb8e2d528a760970442092f6bf1e55c3b659eb/Qwen3-1.7B-Q4_K_M.gguf")
RUNTIME = (RUNTIME_URL, 25369824, "1988ce5b0945f404b19c7dd7778ce64a489983f7ee0f9b0957c016496d380a91")
MODEL = (MODEL_URL, 1282439264, "d2387ca2dbfee2ffabce7120d3770dadca0b293052bc2f0e138fdc940d9bc7b5")


def supported():
    return sys.platform == "win32" and platform.machine().lower() in ("amd64", "x86_64")


def ready(root):
    model = root / "model.gguf"
    return (root / "llama-server.exe").is_file() and model.is_file() and model.stat().st_size == MODEL[1]


def download(asset, destination, advance, cancelled):
    url, size, digest = asset
    hashed = hashlib.sha256()
    received = 0
    with urlopen(Request(url, headers={"User-Agent": "Pivluma"}), timeout=15) as source, destination.open("wb") as output:
        while block := source.read(1024 * 1024):
            if cancelled():
                raise InterruptedError()
            received += len(block)
            if received > size:
                raise ValueError("Unexpected download size")
            hashed.update(block)
            output.write(block)
            advance(received)
    if received != size or hashed.hexdigest() != digest:
        raise ValueError("Download integrity check failed")


def install(root, progress, cancelled):
    if not supported():
        raise OSError("Windows x64 required")
    if ready(root):
        return
    root.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root.parent).free < 2_000_000_000:
        raise OSError("Not enough disk space")
    with TemporaryDirectory(dir=root.parent) as temp:
        temp = Path(temp)
        archive = temp / "runtime.zip"
        staged = temp / "verified"
        staged.mkdir()
        download(RUNTIME, archive, progress, cancelled)
        with ZipFile(archive) as package:
            for name in package.namelist():
                if Path(name).name == name and (name.endswith(".dll") or name in ("llama-server.exe", "LICENSE-LLVM-OpenMP")):
                    package.extract(name, staged)
        download(MODEL, staged / "model.gguf", lambda received: progress(RUNTIME[1] + received), cancelled)
        if cancelled():
            raise InterruptedError()
        if not ready(staged):
            raise ValueError("Missing package files")
        try:
            staged.rename(root)
        except OSError:
            if not ready(root):
                raise
