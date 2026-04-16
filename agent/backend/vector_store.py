"""
向量存储模块 - Qdrant + Ollama Embedding + Reranker
- 从产品库加载条款，生成向量存入 Qdrant
- 支持语义搜索
- 支持 Reranker 精排
"""

import os
import re
import json
import asyncio
import httpx
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct,
    Filter, FieldCondition, MatchValue
)

# 配置
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "insurance_clauses")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://10.76.0.3:11434/v1")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "qwen3-embedding:8b")
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "dengcao/Qwen3-Reranker-4B:Q8_0")
EMBEDDING_DIM = 4096


@dataclass
class VectorSearchResult:
    """向量检索结果"""
    product_id: str
    product_name: str
    filing_no: str
    chapter_name: str
    section_id: str
    title: str
    content: str
    score: float

    def to_dict(self) -> dict:
        return {
            "product_id": self.product_id,
            "product_name": self.product_name,
            "filing_no": self.filing_no,
            "chapter_name": self.chapter_name,
            "section_id": self.section_id,
            "title": self.title,
            "content": self.content,
            "score": self.score,
            "source_tag": f"[{self.product_name}>{self.chapter_name}>{self.title}]"
        }


class VectorStore:
    """向量存储与检索引擎"""

    def __init__(self):
        self.client = QdrantClient(url=QDRANT_URL)
        # 异步客户端，复用连接池
        self._async_client: Optional[httpx.AsyncClient] = None

    def _get_async_client(self) -> httpx.AsyncClient:
        """获取或创建异步 httpx 客户端（复用连接池）"""
        if self._async_client is None or self._async_client.is_closed:
            self._async_client = httpx.AsyncClient(timeout=60.0)
        return self._async_client

    # ============ Embedding（异步） ============

    async def _get_embedding_async(self, text: str) -> List[float]:
        """异步调用 Ollama 生成 embedding"""
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{OLLAMA_BASE_URL}/embeddings",
                json={"model": EMBEDDING_MODEL, "input": text}
            )
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]

    async def _get_embeddings_batch_async(self, texts: List[str]) -> List[List[float]]:
        """异步批量生成 embedding，并发请求"""
        client = self._get_async_client()
        embeddings: List[Optional[List[float]]] = [None] * len(texts)
        batch_size = 8

        async def _fetch_batch(batch_idx: int, batch: List[str]):
            resp = await client.post(
                f"{OLLAMA_BASE_URL}/embeddings",
                json={"model": EMBEDDING_MODEL, "input": batch}
            )
            resp.raise_for_status()
            data = resp.json()
            for j, item in enumerate(data["data"]):
                embeddings[batch_idx + j] = item["embedding"]

        # 并发请求所有批次
        tasks = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            tasks.append(_fetch_batch(i, batch))

        await asyncio.gather(*tasks)
        return embeddings  # type: ignore

    # ============ Reranker（异步并发） ============

    async def _rerank_async(self, query: str, documents: List[str], top_k: int = 10) -> List[Tuple[int, float]]:
        """异步并发调用 Reranker"""
        try:
            scores: List[float] = [0.0] * len(documents)
            ollama_chat_url = f"{OLLAMA_BASE_URL.replace('/v1', '')}/api/chat"

            async def _score_doc(idx: int, doc: str):
                try:
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        resp = await client.post(
                            ollama_chat_url,
                            json={
                                "model": RERANKER_MODEL,
                                "messages": [
                                    {
                                        "role": "user",
                                        "content": f"Query: {query}\nDocument: {doc}\nRate the relevance of the document to the query on a scale of 0 to 1. Reply with only a number."
                                    }
                                ],
                                "stream": False,
                                "options": {"temperature": 0.0}
                            },
                        )
                        resp.raise_for_status()
                        content = resp.json().get("message", {}).get("content", "0").strip()
                        try:
                            scores[idx] = float(content)
                        except ValueError:
                            nums = re.findall(r'[0-9]*\.?[0-9]+', content)
                            scores[idx] = float(nums[0]) if nums else 0.0
                except Exception:
                    scores[idx] = 0.0

            # 并发请求所有文档评分
            await asyncio.gather(*[_score_doc(i, doc) for i, doc in enumerate(documents)])

            # 按分数排序
            indexed = list(enumerate(scores))
            indexed.sort(key=lambda x: x[1], reverse=True)
            return indexed[:top_k]

        except Exception as e:
            print(f"[Reranker] 调用失败: {e}，跳过重排")
            return [(i, 1.0) for i in range(min(top_k, len(documents)))]

    # ============ 集合管理 ============

    def init_collection(self):
        """初始化 Qdrant 集合"""
        collections = self.client.get_collections().collections
        exists = any(c.name == QDRANT_COLLECTION for c in collections)

        if not exists:
            self.client.create_collection(
                collection_name=QDRANT_COLLECTION,
                vectors_config=VectorParams(
                    size=EMBEDDING_DIM,
                    distance=Distance.COSINE
                )
            )
            print(f"[VectorStore] 创建集合: {QDRANT_COLLECTION}")
        else:
            print(f"[VectorStore] 集合已存在: {QDRANT_COLLECTION}")

    def collection_is_empty(self) -> bool:
        """检查集合是否为空"""
        info = self.client.get_collection(QDRANT_COLLECTION)
        return info.points_count == 0

    # ============ 数据导入（异步） ============

    async def _load_products_to_qdrant_async(self, products: List[Dict]):
        """异步将产品条款导入 Qdrant"""
        print(f"[VectorStore] 开始向量化 {len(products)} 个产品的条款...")

        points = []
        point_id = 0
        texts_to_embed = []

        for product in products:
            for chapter in product["chapters"]:
                for section in chapter["sections"]:
                    text = f"{section['title']}：{section['content']}"
                    texts_to_embed.append(text)
                    points.append({
                        "id": point_id,
                        "product_id": product["id"],
                        "product_name": product["product_name"],
                        "filing_no": product["filing_no"],
                        "chapter_name": chapter["chapter_name"],
                        "section_id": section["section_id"],
                        "title": section["title"],
                        "content": section["content"],
                    })
                    point_id += 1

        print(f"[VectorStore] 共 {len(texts_to_embed)} 条条款，开始生成向量（并发）...")

        # 异步并发批量生成 embedding
        embeddings = await self._get_embeddings_batch_async(texts_to_embed)

        # 构建插入点
        qdrant_points = []
        for i, pt in enumerate(points):
            qdrant_points.append(PointStruct(
                id=pt["id"],
                vector=embeddings[i],
                payload={
                    "product_id": pt["product_id"],
                    "product_name": pt["product_name"],
                    "filing_no": pt["filing_no"],
                    "chapter_name": pt["chapter_name"],
                    "section_id": pt["section_id"],
                    "title": pt["title"],
                    "content": pt["content"],
                }
            ))

        # 分批上传（每批100条）
        batch_size = 100
        for i in range(0, len(qdrant_points), batch_size):
            batch = qdrant_points[i:i + batch_size]
            self.client.upsert(
                collection_name=QDRANT_COLLECTION,
                points=batch
            )
            print(f"[VectorStore] 已上传 {min(i + batch_size, len(qdrant_points))}/{len(qdrant_points)}")

        print(f"[VectorStore] 向量化完成！")

    def load_products_to_qdrant(self, products: List[Dict]):
        """同步包装：将产品条款导入 Qdrant"""
        asyncio.run(self._load_products_to_qdrant_async(products))

    # ============ 语义搜索（异步） ============

    async def search_async(
        self,
        query: str,
        limit: int = 20,
        min_score: float = 0.3,
        use_reranker: bool = True,
        rerank_top_k: int = 10,
    ) -> List[VectorSearchResult]:
        """异步语义搜索：向量检索 + Reranker 精排"""
        # 1. 生成查询向量
        query_vector = await self._get_embedding_async(query)

        # 2. Qdrant 向量检索
        response = self.client.query_points(
            collection_name=QDRANT_COLLECTION,
            query=query_vector,
            limit=limit,
            score_threshold=min_score,
        )

        hits = response.points if hasattr(response, 'points') else response

        if not hits:
            return []

        # 3. Reranker 精排（异步并发）
        if use_reranker and len(hits) > 1:
            documents = [hit.payload["title"] + "：" + hit.payload["content"] for hit in hits]
            ranked = await self._rerank_async(query, documents, top_k=rerank_top_k)

            results = []
            for orig_idx, score in ranked:
                hit = hits[orig_idx]
                results.append(VectorSearchResult(
                    product_id=hit.payload["product_id"],
                    product_name=hit.payload["product_name"],
                    filing_no=hit.payload["filing_no"],
                    chapter_name=hit.payload["chapter_name"],
                    section_id=hit.payload["section_id"],
                    title=hit.payload["title"],
                    content=hit.payload["content"],
                    score=score,
                ))
            return results

        # 不使用 Reranker，直接返回
        return [
            VectorSearchResult(
                product_id=hit.payload["product_id"],
                product_name=hit.payload["product_name"],
                filing_no=hit.payload["filing_no"],
                chapter_name=hit.payload["chapter_name"],
                section_id=hit.payload["section_id"],
                title=hit.payload["title"],
                content=hit.payload["content"],
                score=hit.score,
            )
            for hit in hits
        ]

    def search(
        self,
        query: str,
        limit: int = 20,
        min_score: float = 0.3,
        use_reranker: bool = True,
        rerank_top_k: int = 10,
    ) -> List[VectorSearchResult]:
        """同步包装：语义搜索（安全处理异步上下文）"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # 已在异步上下文中，不能用 asyncio.run()，在新线程中运行
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    asyncio.run,
                    self.search_async(query, limit, min_score, use_reranker, rerank_top_k)
                )
                return future.result(timeout=60)
        else:
            return asyncio.run(self.search_async(query, limit, min_score, use_reranker, rerank_top_k))

    async def close(self):
        """关闭异步客户端"""
        if self._async_client and not self._async_client.is_closed:
            await self._async_client.aclose()


# ============ 全局单例 ============

_vector_store: Optional[VectorStore] = None


def get_vector_store() -> VectorStore:
    global _vector_store
    if _vector_store is None:
        _vector_store = VectorStore()
    return _vector_store


def init_vector_store(products: List[Dict]):
    """初始化向量库并导入数据（启动时调用一次）"""
    global _vector_store
    _vector_store = VectorStore()
    _vector_store.init_collection()

    if _vector_store.collection_is_empty():
        print("[VectorStore] 集合为空，开始导入数据...")
        _vector_store.load_products_to_qdrant(products)
    else:
        print("[VectorStore] 集合已有数据，跳过导入")

    return _vector_store
