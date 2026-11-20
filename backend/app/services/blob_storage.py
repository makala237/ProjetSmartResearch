"""Hybrid Binary Object Storage Adapter (Local filesystem, Google Cloud Storage, or Cloudflare R2 / S3)."""

from abc import ABC, abstractmethod
from functools import lru_cache
import logging
import mimetypes
from pathlib import Path
from typing import Optional

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def build_object_key(*parts: str) -> str:
    """Build a normalized slash-separated object key prefixed cleanly."""
    settings = get_settings()
    prefix = (settings.cloud_storage_prefix or "").strip("/ ")
    cleaned = [str(p).strip("/ ") for p in parts if str(p).strip("/ ")]
    if prefix:
        cleaned.insert(0, prefix)
    return "/".join(cleaned)


class BlobStorage(ABC):
    """Abstract binary artifact store for uploaded images, logos, and exports."""

    backend_name: str = "local"
    is_remote: bool = False

    @abstractmethod
    def upload_file(
        self,
        object_key: str,
        local_path: Path,
        content_type: Optional[str] = None,
    ) -> str:
        """Persist a local file into the backing store and return its storage URI/key."""

    @abstractmethod
    def download_to_path(self, object_key: str, dest_path: Path) -> bool:
        """Fetch an object to local dest_path if present; return True when available."""

    @abstractmethod
    def delete(self, object_key: str) -> None:
        """Best-effort delete of object_key from the backing store."""


class LocalBlobStorage(BlobStorage):
    """Default on-disk storage adapter (development & single-node volumes)."""

    backend_name = "local"
    is_remote = False

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir

    def _resolve(self, object_key: str) -> Path:
        safe = Path(*[p for p in object_key.replace("\\", "/").split("/") if p and p != ".."])
        return self.root_dir / safe

    def upload_file(
        self,
        object_key: str,
        local_path: Path,
        content_type: Optional[str] = None,
    ) -> str:
        return str(local_path)

    def download_to_path(self, object_key: str, dest_path: Path) -> bool:
        if dest_path.exists() and dest_path.is_file():
            return True
        alt = self._resolve(object_key)
        if alt.exists() and alt.is_file():
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(alt.read_bytes())
            return True
        return False

    def delete(self, object_key: str) -> None:
        target = self._resolve(object_key)
        if target.exists() and target.is_file():
            try:
                target.unlink()
            except OSError:
                pass


class GCSBlobStorage(BlobStorage):
    """Google Cloud Storage adapter via google-cloud-storage SDK."""

    backend_name = "gcs"
    is_remote = True

    def __init__(self, bucket_name: str) -> None:
        if not bucket_name:
            raise ValueError("CLOUD_STORAGE_BUCKET requis lorsque STORAGE_BACKEND=gcs.")
        from google.cloud import storage  # Lazy import so local dev runs without cloud SDKs

        self.bucket_name = bucket_name
        self._client = storage.Client()
        self._bucket = self._client.bucket(bucket_name)

    def upload_file(
        self,
        object_key: str,
        local_path: Path,
        content_type: Optional[str] = None,
    ) -> str:
        ctype = content_type or mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
        blob = self._bucket.blob(object_key)
        blob.upload_from_filename(str(local_path), content_type=ctype)
        return f"gs://{self.bucket_name}/{object_key}"

    def download_to_path(self, object_key: str, dest_path: Path) -> bool:
        blob = self._bucket.blob(object_key)
        if not blob.exists():
            return False
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(dest_path))
        return dest_path.exists() and dest_path.stat().st_size > 0

    def delete(self, object_key: str) -> None:
        try:
            blob = self._bucket.blob(object_key)
            if blob.exists():
                blob.delete()
        except Exception as exc:
            logger.warning("GCS delete failed for %s: %s", object_key, exc)


class S3R2BlobStorage(BlobStorage):
    """Amazon S3 & Cloudflare R2 compatible object storage adapter via boto3."""

    backend_name = "s3"
    is_remote = True

    def __init__(
        self,
        bucket_name: str,
        endpoint_url: str = "",
        access_key: str = "",
        secret_key: str = "",
        region_name: str = "auto",
    ) -> None:
        if not bucket_name:
            raise ValueError("CLOUD_STORAGE_BUCKET requis lorsque STORAGE_BACKEND=s3.")
        import boto3  # Lazy import so local dev runs without cloud SDKs

        self.bucket_name = bucket_name
        client_kwargs = {"region_name": region_name or "auto"}
        if endpoint_url:
            client_kwargs["endpoint_url"] = endpoint_url
        if access_key and secret_key:
            client_kwargs["aws_access_key_id"] = access_key
            client_kwargs["aws_secret_access_key"] = secret_key
        self._client = boto3.client("s3", **client_kwargs)

    def upload_file(
        self,
        object_key: str,
        local_path: Path,
        content_type: Optional[str] = None,
    ) -> str:
        ctype = content_type or mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
        self._client.upload_file(
            str(local_path),
            self.bucket_name,
            object_key,
            ExtraArgs={"ContentType": ctype},
        )
        return f"s3://{self.bucket_name}/{object_key}"

    def download_to_path(self, object_key: str, dest_path: Path) -> bool:
        try:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            self._client.download_file(self.bucket_name, object_key, str(dest_path))
            return dest_path.exists() and dest_path.stat().st_size > 0
        except Exception:
            return False

    def delete(self, object_key: str) -> None:
        try:
            self._client.delete_object(Bucket=self.bucket_name, Key=object_key)
        except Exception as exc:
            logger.warning("S3/R2 delete failed for %s: %s", object_key, exc)


@lru_cache
def get_blob_storage() -> BlobStorage:
    """Return configured singleton BlobStorage adapter ('local', 'gcs', or 's3')."""
    settings = get_settings()
    backend = (settings.storage_backend or "local").strip().lower()
    if backend == "gcs":
        return GCSBlobStorage(bucket_name=settings.cloud_storage_bucket)
    if backend in ("s3", "r2"):
        return S3R2BlobStorage(
            bucket_name=settings.cloud_storage_bucket,
            endpoint_url=settings.cloud_storage_endpoint_url,
            access_key=settings.cloud_storage_access_key,
            secret_key=settings.cloud_storage_secret_key,
            region_name=settings.cloud_storage_region,
        )
    return LocalBlobStorage(root_dir=settings.storage_dir)
