from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime
from typing import Any, TYPE_CHECKING

from agent.core.tool import Tool

if TYPE_CHECKING:
    from agent.core.loop import AgentContext, Job

logger = logging.getLogger(__name__)


class OssWriteFileTool(Tool):
    name = "write_file"
    description = (
        "Write content to a file in cloud storage (Aliyun OSS). "
        "Returns a URL to access the file. "
        "Use this to create documents, specs, reports, or any text-based files."
    )
    parameters = {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "File name with extension (e.g. architecture-spec.md)",
            },
            "content": {
                "type": "string",
                "description": "File content to write",
            },
            "content_type": {
                "type": "string",
                "description": "MIME type (default: text/markdown for .md, text/plain otherwise)",
            },
        },
        "required": ["filename", "content"],
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._endpoint = self.config.get("endpoint")
        self._bucket_name = self.config.get("bucket")
        self._access_key_id = self.config.get("access_key_id")
        self._access_key_secret = self.config.get("access_key_secret")
        self._base_path = self.config.get("base_path", "").rstrip("/")
        self._public_url = self.config.get("public_url", "").rstrip("/")
        self._bucket = None

    def _get_bucket(self):
        if self._bucket is None:
            import oss2
            auth = oss2.Auth(self._access_key_id, self._access_key_secret)
            self._bucket = oss2.Bucket(auth, self._endpoint, self._bucket_name)
        return self._bucket

    async def execute(self, arguments: dict, ctx: AgentContext, job: Job) -> str:
        filename = arguments.get("filename", "")
        content = arguments.get("content", "")
        content_type = arguments.get("content_type")

        if not filename:
            return "Error: filename is required"
        if not content:
            return "Error: content is required"

        if not self._endpoint or not self._bucket_name:
            return "Error: OSS not configured (endpoint/bucket missing)"
        if not self._access_key_id or not self._access_key_secret:
            return "Error: OSS credentials not configured"

        if not content_type:
            if filename.endswith(".md"):
                content_type = "text/markdown"
            elif filename.endswith(".html"):
                content_type = "text/html"
            elif filename.endswith(".json"):
                content_type = "application/json"
            elif filename.endswith(".txt"):
                content_type = "text/plain"
            else:
                content_type = "text/plain"

        month_dir = datetime.now().strftime("%Y-%m")
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()[:8]
        name_part, ext = filename.rsplit(".", 1) if "." in filename else (filename, "")
        object_name = f"{name_part}-{timestamp}-{content_hash}.{ext}"
        object_key = f"{self._base_path}/{month_dir}/{object_name}" if self._base_path else f"{month_dir}/{object_name}"

        try:
            bucket = self._get_bucket()

            def _upload():
                bucket.put_object(object_key, content.encode("utf-8"), headers={"Content-Type": f"{content_type}; charset=utf-8"})

            await asyncio.to_thread(_upload)

            if self._public_url:
                url = f"{self._public_url}/{object_key}"
            else:
                url = bucket.sign_url("GET", object_key, 3600)

            logger.info("Uploaded %s to OSS: %s", filename, object_key)
            return f"File uploaded: {url}"

        except Exception as e:
            logger.exception("OSS upload failed")
            return f"Error: failed to upload file: {e}"
