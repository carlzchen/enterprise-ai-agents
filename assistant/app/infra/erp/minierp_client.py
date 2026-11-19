"""调用 MiniERP REST API：登录、列出现行 PLM 文档、下载文档文件、导出知识库页面"""
import httpx

from app.infra.config.settings import settings


class MiniErpClient:
    def __init__(self):
        self._token: str | None = None

    def _login(self) -> str:
        r = httpx.post(f"{settings.minierp_api_base}/auth/login", timeout=10,
                       json={"username": settings.minierp_sync_user, "password": settings.minierp_sync_password})
        r.raise_for_status()
        return r.json()["token"]

    def _get(self, path: str, **params) -> httpx.Response:
        for attempt in range(2):
            if not self._token:
                self._token = self._login()
            r = httpx.get(f"{settings.minierp_api_base}{path}", params=params, timeout=30,
                          headers={"Authorization": f"Bearer {self._token}"})
            if r.status_code == 401 and attempt == 0:
                self._token = None
                continue
            r.raise_for_status()
            return r
        raise RuntimeError("MiniERP 登录失败")

    def current_documents(self) -> list[dict]:
        docs, page = [], 1
        while True:
            res = self._get("/views/documents", status="CURRENT", page=page, size=200).json()
            docs.extend(res["records"])
            if len(docs) >= res["total"] or not res["records"]:
                return docs
            page += 1

    def kb_pages(self) -> list[dict]:
        """知识库已发布页面（含正文与版本号）"""
        return self._get("/kb/export").json()

    def download(self, doc_id: int) -> bytes:
        return self._get(f"/plm/documents/{doc_id}/download").content


minierp_client = MiniErpClient()
