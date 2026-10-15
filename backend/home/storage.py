"""Photo storage for housing posts.

Cloudflare R2 (S3 API) is the production backend: 10 GB free and no egress fee. The
S3 signing is done here with the standard library, so the Vercel bundle does not
need boto3. A local-folder backend exists for development and tests.

Configuration is read from Admin → Home settings first, then from the environment
(HOME_MEDIA_BACKEND, R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY,
HOME_MEDIA_BUCKET, HOME_MEDIA_PUBLIC_BASE). A local-folder backend for development
uses HOME_MEDIA_LOCAL_DIR and HOME_MEDIA_LOCAL_BASE.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

PUT_TIMEOUT_SECONDS = 20


class StorageError(RuntimeError):
    pass


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def sigv4_authorization(
    *,
    method: str,
    host: str,
    path: str,
    query: str,
    headers: dict[str, str],
    payload_hash: str,
    access_key: str,
    secret_key: str,
    region: str,
    service: str,
    amz_date: str,
) -> str:
    """AWS Signature Version 4 `Authorization` header value.

    `headers` are the headers to sign, lower-case names; `host` is added here.
    `path` is the already URI-encoded canonical path.
    """
    signed = {name.lower(): " ".join(str(value).split()) for name, value in headers.items()}
    signed["host"] = host
    names = sorted(signed)
    canonical_headers = "".join(f"{name}:{signed[name]}\n" for name in names)
    signed_headers = ";".join(names)
    canonical_request = "\n".join([method, path, query, canonical_headers, signed_headers, payload_hash])
    day = amz_date[:8]
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()]
    )
    key = _hmac(("AWS4" + secret_key).encode("utf-8"), day)
    for part in (region, service, "aws4_request"):
        key = _hmac(key, part)
    signature = hmac.new(key, to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed_headers}, Signature={signature}"
    )


class Storage:
    def put(self, key: str, data: bytes, content_type: str = "image/jpeg") -> None:
        raise NotImplementedError

    def url(self, key: str) -> str:
        raise NotImplementedError


class LocalStorage(Storage):
    def __init__(self, root: str, base_url: str = "") -> None:
        self.root = Path(root)
        self.base_url = base_url.rstrip("/")

    def put(self, key: str, data: bytes, content_type: str = "image/jpeg") -> None:
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def url(self, key: str) -> str:
        return f"{self.base_url}/{key}" if self.base_url else key


class R2Storage(Storage):
    def __init__(self, *, account_id: str, access_key: str, secret_key: str, bucket: str, public_base: str) -> None:
        self.host = f"{account_id}.r2.cloudflarestorage.com"
        self.access_key = access_key
        self.secret_key = secret_key
        self.bucket = bucket
        self.public_base = public_base.rstrip("/")

    def put(self, key: str, data: bytes, content_type: str = "image/jpeg") -> None:
        path = "/" + urllib.parse.quote(f"{self.bucket}/{key}", safe="/-_.~")
        amz_date = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        payload_hash = hashlib.sha256(data).hexdigest()
        headers = {
            "content-type": content_type,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        authorization = sigv4_authorization(
            method="PUT",
            host=self.host,
            path=path,
            query="",
            headers=headers,
            payload_hash=payload_hash,
            access_key=self.access_key,
            secret_key=self.secret_key,
            region="auto",
            service="s3",
            amz_date=amz_date,
        )
        request = urllib.request.Request(
            f"https://{self.host}{path}",
            data=data,
            method="PUT",
            headers={
                "Content-Type": content_type,
                "x-amz-content-sha256": payload_hash,
                "x-amz-date": amz_date,
                "Authorization": authorization,
                "Cache-Control": "public, max-age=31536000, immutable",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=PUT_TIMEOUT_SECONDS) as response:
                if response.status not in (200, 201):
                    raise StorageError(f"R2 answered {response.status}")
        except urllib.error.HTTPError as exc:
            raise StorageError(f"R2 answered {exc.code}: {exc.read()[:200].decode('utf-8', 'replace')}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise StorageError(f"R2 unreachable: {exc}") from exc

    def url(self, key: str) -> str:
        return f"{self.public_base}/{key}"


def get_storage() -> Storage | None:
    from home import conf

    values = conf.storage_values()
    backend = values["backend"]
    if backend == "local":
        root = (getattr(settings, "HOME_MEDIA_LOCAL_DIR", "") or "").strip()
        return LocalStorage(root, getattr(settings, "HOME_MEDIA_LOCAL_BASE", "") or "") if root else None
    if backend == "r2":
        if not conf.missing_storage_values():
            return R2Storage(
                account_id=values["account_id"],
                access_key=values["access_key"],
                secret_key=values["secret_key"],
                bucket=values["bucket"],
                public_base=values["public_base"],
            )
        logger.warning("Home photo storage is r2 but %s", ", ".join(conf.missing_storage_values()))
    return None


def media_key(source_country: str, media_id: int, digest: str, *, thumb: bool = False) -> str:
    suffix = "t" if thumb else "f"
    return f"home/{source_country.lower()}/{media_id // 1000:05d}/{media_id}-{digest[:10]}-{suffix}.jpg"
