import hashlib
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from pid_framework.models import Document, OriginalExtract, Package, ReconstructedDrawing, Review


class ConflictError(ValueError):
    pass


class Store:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS records "
                "(kind TEXT, id TEXT, payload TEXT NOT NULL, PRIMARY KEY(kind,id))"
            )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.root / "framework.sqlite3", timeout=15)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def save(
        self, kind: str, value: Document | Review | Package | OriginalExtract | ReconstructedDrawing
    ) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO records(kind,id,payload) VALUES(?,?,?)",
                (kind, value.id, value.model_dump_json()),
            )

    def get(self, kind: str, record_id: str) -> str:
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM records WHERE kind=? AND id=?", (kind, record_id)
            ).fetchone()
        if row is None:
            raise KeyError("Record not found")
        return row[0]

    def document(self, record_id: str) -> Document:
        return Document.model_validate_json(self.get("document", record_id))

    def documents(self) -> list[Document]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM records WHERE kind='document' ORDER BY rowid DESC"
            ).fetchall()
        return [Document.model_validate_json(row[0]) for row in rows]

    def update_document(
        self, record_id: str, expected_version: int, change: Callable[[Document], Document]
    ) -> Document:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT payload FROM records WHERE kind='document' AND id=?", (record_id,)
            ).fetchone()
            if row is None:
                raise KeyError("Document not found")
            document = Document.model_validate_json(row[0])
            if document.version != expected_version:
                raise ConflictError("Document changed. Reload before saving.")
            changed = change(document)
            changed.version += 1
            changed = Document.model_validate(changed.model_dump())
            db.execute(
                "UPDATE records SET payload=? WHERE kind='document' AND id=?",
                (changed.model_dump_json(), record_id),
            )
        return changed

    def source(self, document: Document) -> bytes:
        return (self.root / f"{document.sha256}.pdf").read_bytes()

    def save_source(self, document: Document, content: bytes) -> None:
        if hashlib.sha256(content).hexdigest() != document.sha256:
            raise ValueError("Source checksum does not match document")
        target = self.root / f"{document.sha256}.pdf"
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != document.sha256:
                raise ValueError("Stored source failed integrity validation")
            return
        with tempfile.NamedTemporaryFile(dir=self.root, suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            file.write(content)
        try:
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
