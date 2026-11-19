"""MinIO：原始上传文件（raw/）与文档图片（images/）。桶是私有的，前端看图走知识库服务鉴权后现签的临时链接"""
import io
import threading
from datetime import timedelta
from mimetypes import guess_type

from minio import Minio

from app.infra.config.settings import settings
from app.shared.runtime.logger import logger


class MinioGateway:
    def __init__(self):
        self._client: Minio | None = None
        self._signer: Minio | None = None
        self._lock = threading.Lock()
        self.bucket = settings.minio_bucket

    @property
    def client(self) -> Minio:
        with self._lock:
            if self._client is None:
                self._client = Minio(settings.minio_endpoint, access_key=settings.minio_access_key,
                                     secret_key=settings.minio_secret_key, secure=settings.minio_secure)
                if not self._client.bucket_exists(self.bucket):
                    self._client.make_bucket(self.bucket)
                    logger.warning(f"MinIO 桶 {self.bucket} 不存在，已创建（图片目录需匿名可读，见 docker-compose 的 minio-init）")
        return self._client

    def put_bytes(self, object_name: str, data: bytes, content_type: str | None = None) -> str:
        self.client.put_object(self.bucket, object_name, io.BytesIO(data), length=len(data),
                               content_type=content_type or guess_type(object_name)[0] or "application/octet-stream")
        return self.public_url(object_name)

    def delete_prefix(self, prefix: str) -> int:
        n = 0
        for obj in self.client.list_objects(self.bucket, prefix=prefix, recursive=True):
            self.client.remove_object(self.bucket, obj.object_name)
            n += 1
        return n

    def remove(self, object_name: str) -> None:
        self.client.remove_object(self.bucket, object_name)

    def get_bytes(self, object_name: str) -> bytes:
        r = self.client.get_object(self.bucket, object_name)
        try:
            return r.read()
        finally:
            r.close()
            r.release_conn()

    def presigned_url(self, object_name: str, seconds: int = 600) -> str:
        """临时签名链接：过期失效，链接本身不带账号信息。签名里包含主机名，所以用浏览器能访问的地址（MINIO_PUBLIC_BASE）建一个只用来签名的客户端，
        不能签完再替换主机"""
        if self._signer is None:
            from urllib.parse import urlparse
            u = urlparse(settings.minio_public_base or ("http://" + settings.minio_endpoint))
            self._signer = Minio(u.netloc, access_key=settings.minio_access_key, secret_key=settings.minio_secret_key,
                                 secure=u.scheme == "https", region="us-east-1")       # 指定区域，签名时不用联网查桶的位置
        return self._signer.presigned_get_object(self.bucket, object_name, expires=timedelta(seconds=seconds))

    def make_private(self) -> bool:
        """去掉桶上的匿名读策略（早期 images/ 目录是匿名可读的）"""
        try:
            self.client.delete_bucket_policy(self.bucket)
            return True
        except Exception as e:
            logger.warning(f"MinIO 去掉匿名策略失败：{e}")
            return False

    def public_url(self, object_name: str) -> str:
        return f"{settings.minio_public_base.rstrip('/')}/{self.bucket}/{object_name}"


minio_gateway = MinioGateway()
