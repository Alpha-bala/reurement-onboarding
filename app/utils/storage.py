# utils/storage.py
from __future__ import annotations

import os
import uuid
import shutil
from pathlib import Path
from typing import Optional, Tuple

MEDIA_ROOT = Path(os.getenv("MEDIA_ROOT", "var/uploads")).resolve()
MEDIA_ROOT.mkdir(parents=True, exist_ok=True)


def _safe_part(s: object) -> str:
    txt = str(s or "").strip()
    return "".join(ch for ch in txt if ch.isalnum() or ch in ("-", "_", ".", "@")) or "x"


class Storage:
    # ---------------------------
    # Key builders (always forward slashes in keys)
    # ---------------------------
    def make_candidate_key(
        self, *, candidate_id: str | int, doc_code: str, version_no: int, filename: str
    ) -> str:
        cand = _safe_part(candidate_id)
        doc = _safe_part(doc_code)
        base, ext = os.path.splitext(filename or "file")
        base = _safe_part(base)
        ext = ext or ".bin"
        rand = uuid.uuid4().hex[:8]
        return f"candidates/{cand}/{doc}/v{int(version_no)}/{base}-{rand}{ext}"

    def make_employee_key(
        self, *, employee_id: str | int, doc_code: str, version_no: int, filename: str
    ) -> str:
        emp = _safe_part(employee_id)
        doc = _safe_part(doc_code)
        base, ext = os.path.splitext(filename or "file")
        base = _safe_part(base)
        ext = ext or ".bin"
        rand = uuid.uuid4().hex[:8]
        return f"employees/{emp}/{doc}/v{int(version_no)}/{base}-{rand}{ext}"

    # Back-compat for candidate upload
    def make_key(
        self, *, candidate_id: str | int, doc_code: str, version_no: int, filename: str
    ) -> str:
        return self.make_candidate_key(
            candidate_id=candidate_id, doc_code=doc_code, version_no=version_no, filename=filename
        )

    # ---------------------------
    # Paths & IO
    # ---------------------------
    def _normalize_key(self, storage_key: str) -> str:
        return (storage_key or "").replace("\\", "/").lstrip("/")

    def path_for(self, storage_key: str) -> str:
        key = self._normalize_key(storage_key)
        path = (MEDIA_ROOT / key).resolve()
        if not str(path).startswith(str(MEDIA_ROOT)):
            raise ValueError("Invalid storage key")
        return str(path)

    def exists(self, storage_key: str) -> bool:
        try:
            return Path(self.path_for(storage_key)).exists()
        except Exception:
            return False

    def save_upload(self, file_obj, dest_key: str) -> Tuple[str, str]:
        key = self._normalize_key(dest_key)
        abs_path = Path(self.path_for(key))
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            file_obj.seek(0)
        except Exception:
            pass
        with open(abs_path, "wb") as out:
            while True:
                chunk = file_obj.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        try:
            file_obj.seek(0)
        except Exception:
            pass
        return key, str(abs_path)

    # ---------------------------
    # Copy / Move helpers
    # ---------------------------
    def copy_key(self, src_key: str, dst_key: str, *, overwrite: bool = False) -> str:
        src = Path(self.path_for(src_key))
        dst = Path(self.path_for(dst_key))
        if not src.exists():
            raise FileNotFoundError(f"Source missing: {src}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and not overwrite:
            raise FileExistsError(f"Destination exists: {dst}")
        shutil.copy2(src, dst)
        return dst_key

    def move_key(self, src_key: str, dst_key: str, *, overwrite: bool = False) -> str:
        # atomic on same fs (still checks existence first)
        copied = self.copy_key(src_key, dst_key, overwrite=overwrite)
        try:
            Path(self.path_for(src_key)).unlink(missing_ok=True)
        except TypeError:
            # py<3.8 fallback
            p = Path(self.path_for(src_key))
            if p.exists():
                p.unlink()
        return copied

    def candidate_to_employee_key(
        self, storage_key: str, candidate_id: str | int, employee_id: str | int
    ) -> str:
        cand_prefix = f"candidates/{_safe_part(candidate_id)}/"
        emp_prefix = f"employees/{_safe_part(employee_id)}/"
        key = self._normalize_key(storage_key)
        if not key.startswith(cand_prefix):
            raise ValueError("Key does not belong to this candidate")
        return emp_prefix + key[len(cand_prefix) :]

    def rehome_candidate_file_to_employee(
        self,
        storage_key: str,
        candidate_id: str | int,
        employee_id: str | int,
        *,
        overwrite: bool = True,
        remove_source: bool = False,
    ) -> str:
        dst_key = self.candidate_to_employee_key(storage_key, candidate_id, employee_id)
        if remove_source:
            return self.move_key(storage_key, dst_key, overwrite=overwrite)
        return self.copy_key(storage_key, dst_key, overwrite=overwrite)

    def remove(self, storage_key: str) -> None:
        try:
            Path(self.path_for(storage_key)).unlink(missing_ok=True)
        except TypeError:
            p = Path(self.path_for(storage_key))
            if p.exists():
                p.unlink()

    def presigned_url(self, abs_path: str) -> Optional[str]:
        return None


storage = Storage()


