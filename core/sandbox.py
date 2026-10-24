"""Per-chat sandboxed workspace. All file paths are resolved and confined to it."""
from __future__ import annotations

import os
from pathlib import Path


class SandboxError(Exception):
    pass


class Sandbox:
    def __init__(self, root: Path, chat_id: int, max_file_bytes: int) -> None:
        self.root = (root / f"chat_{chat_id}").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_file_bytes = max_file_bytes

    def resolve(self, rel_path: str) -> Path:
        if not rel_path or "\x00" in rel_path:
            raise SandboxError("Invalid path")
        candidate = (self.root / rel_path.lstrip("/\\")).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise SandboxError("Path escapes the sandbox")
        return candidate

    def write(self, rel_path: str, content: str) -> str:
        data = content.encode("utf-8")
        if len(data) > self.max_file_bytes:
            raise SandboxError(f"File too large ({len(data)} bytes > {self.max_file_bytes})")
        path = self.resolve(rel_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)  # atomic
        return f"Wrote {len(data)} bytes to {path.relative_to(self.root)}"

    def read(self, rel_path: str, limit: int) -> str:
        path = self.resolve(rel_path)
        if not path.is_file():
            raise SandboxError(f"No such file: {rel_path}")
        if path.stat().st_size > self.max_file_bytes:
            raise SandboxError("File too large to read")
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[:limit]

    def list(self, rel_path: str = ".") -> str:
        path = self.resolve(rel_path) if rel_path not in ("", ".") else self.root
        if not path.is_dir():
            raise SandboxError(f"Not a directory: {rel_path}")
        entries = []
        for p in sorted(path.iterdir()):
            kind = "dir " if p.is_dir() else "file"
            size = p.stat().st_size if p.is_file() else 0
            entries.append(f"{kind} {p.relative_to(self.root)} ({size} B)")
        return "\n".join(entries) or "(empty)"

    def delete(self, rel_path: str) -> str:
        path = self.resolve(rel_path)
        if path == self.root:
            raise SandboxError("Refusing to delete sandbox root")
        if path.is_dir():
            import shutil

            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        else:
            raise SandboxError(f"No such path: {rel_path}")
        return f"Deleted {rel_path}"
