from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import boto3

from .hashing import sha256_bytes


class ObjectStore(Protocol):
    def put(self, *, key: str, body: bytes, content_type: str | None = None) -> str: ...


@dataclass
class S3ObjectStore:
    endpoint_url: str
    access_key: str
    secret_key: str
    bucket: str
    region: str

    def __post_init__(self) -> None:
        self.client = boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region,
        )

    def put(self, *, key: str, body: bytes, content_type: str | None = None) -> str:
        kwargs: dict[str, object] = {"Bucket": self.bucket, "Key": key, "Body": body}
        if content_type:
            kwargs["ContentType"] = content_type
        self.client.put_object(**kwargs)
        return key


def raw_object_key(source_key: str, fetched_at: datetime, url: str, body: bytes) -> str:
    digest = sha256_bytes(body)
    date = fetched_at.strftime("%Y/%m/%d")
    return f"raw/{source_key}/{date}/{digest}.bin"
