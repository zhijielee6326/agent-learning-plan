"""
条款生成工作流 Agent
- LLM 意图理解 → 向量语义检索 → 逐章节 LLM 生成（流式输出）→ 溯源标注
"""
import json
import re
import os
from typing import AsyncGenerator, List, Dict, Optional
from dotenv import load_dotenv

# 关键：指定 .env 文件路径
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from product_store import ProductStore, get_product_store, STANDARD_CHAPTER_ORDER

# API 配置
LLM_API_TYPE = os.getenv("LLM_API_TYPE", "openai")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "glm-5.1")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.z.ai/api/anthropic")


def _is_anthropic() -> bool:
    return LLM_API_TYPE == "anthropic"


def _anthropic_headers() -> dict:
    return {
        "x-api-key": LLM_API_KEY,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json"
    }


def _openai_headers() -> dict:
    return {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json"
    }

# 标准章节列表（用于生成条款）
CLAUSE_CHAPTERS = [
    "总则", "保险责任", "责任免除", "保险金额", "保险期间",
    "保险人义务", "投保人及被保险人义务", "保险金申请", "争议处理", "释义"
]


class ClauseDraftAgent:
    """条款生成工作流 - LLM 驱动"""

    def __init__(self, store: ProductStore = None, vector_store=None):
        self.store = store or get_product_store()
        self.vector_store = vector_store

    # ========== Step 1: LLM 意图理解 ==========

    async def _extract_insurance_type_llm(self, query: str) -> str:
        """
        让 LLM 理解用户意图，提取险种名称。
        不使用硬编码关键词，完全由模型思考判断。
        """
        import httpx

        api_url = os.getenv("LLM_BASE_URL", "https://qwen.hi-ins.com.cn/v1") + "/chat/completions"
        api_key = os.getenv("LLM_API_KEY", "")
        model = os.getenv("LLM_MODEL", "/Qwen/Qwen3.5-27B")

        prompt = f"""你是一个保险产品分类专家。用户说了以下内容：

"{query}"

请从中提取用户想要生成的保险产品类型名称。

规则：
1. 只返回产品类型名称，不要任何解释
2. 如果用户提到具体险种，直接返回（如"新能源车险"、"团体意外伤害保险"）
3. 如果用户提到的是大类，返回具体的产品类型（如"医疗"→"医疗保险"）
4. 不要添加"保险"二字如果原词中已经包含
5. 只返回一个名称

示例：
- "帮我生成新能源车险的产品条款" → "新能源车险"
- "起草一份重大疾病保险条款" → "重大疾病保险"
- "生成人身意外伤害保险条款" → "人身意外伤害保险"
- "写一个团体医疗险" → "团体医疗保险"
- "帮我做一份工程保险" → "工程保险"

请直接返回产品类型名称："""

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                if _is_anthropic():
                    url = f"{LLM_BASE_URL}/v1/messages"
                    resp = await client.post(url,
                        headers=_anthropic_headers(),
                        json={"model": LLM_MODEL, "messages": [{"role": "user", "content": prompt}],
                              "max_tokens": 50, "temperature": 0.1}
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        content_blocks = data.get("content", [])
                        result = content_blocks[0].get("text", "").strip() if content_blocks else ""
                    else:
                        result = ""
                else:
                    url = f"{LLM_BASE_URL}/chat/completions"
                    resp = await client.post(url,
                        headers=_openai_headers(),
                        json={"model": LLM_MODEL, "messages": [{"role": "user", "content": prompt}],
                              "temperature": 0.1, "max_tokens": 50}
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        result = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                    else:
                        result = ""

                if result:
                    result = result.strip('"\'""''《》【】').strip()
                    if result:
                        print(f"[ClauseDraft] LLM 识别险种: {query} → {result}")
                        return result
                print(f"[ClauseDraft] LLM 意图识别失败 HTTP {resp.status_code}, 回退到规则提取")
        except Exception as e:
            print(f"[ClauseDraft] LLM 意图识别异常: {e}, 回退到规则提取")

        # 回退：简单去除指令词，提取核心内容
        return self._fallback_extract(query)

    def _fallback_extract(self, query: str) -> str:
        """规则回退：去除常见指令词，提取产品名称"""
        clean = query
        for prefix in ["帮我生成一份", "帮我起草一份", "生成一份", "起草一份",
                        "帮我写一份", "帮我生成", "帮我起草", "帮我做一份",
                        "生成", "起草", "撰写", "编写", "制作",
                        "的产品条款", "产品条款", "保险条款", "条款", "的"]:
            clean = clean.replace(prefix, "")
        return clean.strip() or "意外伤害保险"

    # ========== Step 2: 向量语义检索 + 产品库搜索 ==========

    def _search_related_products(self, insurance_type: str, limit: int = 5) -> List[Dict]:
        """
        搜索相关产品：优先向量语义检索，辅助产品名模糊匹配
        不依赖硬编码关键词字典
        """
        matched_ids = []
        seen_ids = set()

        # 1. 向量语义检索（主要方式）
        if self.vector_store:
            try:
                vquery = f"{insurance_type}保险"
                results = self.vector_store.search(vquery, limit=limit * 4, use_reranker=False)
                for r in results:
                    pid = getattr(r, 'product_id', None) or getattr(r, 'id', None)
                    if pid and pid not in seen_ids:
                        matched_ids.append(pid)
                        seen_ids.add(pid)
                print(f"[ClauseDraft] 向量语义检索找到 {len(matched_ids)} 个产品")
            except Exception as e:
                print(f"[ClauseDraft] 向量检索失败: {e}")

        # 2. 产品名模糊匹配（补充方式）
        # 从险种名中提取搜索词（去掉"保险"等常见后缀，保留核心词）
        search_terms = self._extract_search_terms(insurance_type)
        for product in self.store.products:
            if len(matched_ids) >= limit * 3:
                break
            name = product.get("product_name", "")
            pid = product.get("id", "")
            if pid in seen_ids:
                continue
            for term in search_terms:
                if term in name:
                    matched_ids.append(pid)
                    seen_ids.add(pid)
                    break

        print(f"[ClauseDraft] 合计匹配 {len(matched_ids)} 个产品ID")

        # 3. 获取完整产品数据
        full_products = []
        for pid in matched_ids[:limit]:
            full = self.store.get_product(pid)
            if full:
                full_products.append(full)

        print(f"[ClauseDraft] 获取到 {len(full_products)} 个完整产品数据")
        return full_products

    def _extract_search_terms(self, insurance_type: str) -> List[str]:
        """从险种名称中提取搜索词（用于产品名模糊匹配）"""
        terms = [insurance_type]
        # 去掉"保险"后缀做模糊匹配
        base = insurance_type.replace("保险", "").replace("险种", "").strip()
        if base and base != insurance_type:
            terms.append(base)
        # 如果名称较长，拆分为2-3字的子串
        if len(base) >= 4:
            for i in range(len(base) - 1):
                seg = base[i:i+2]
                if seg not in ["产品", "条款"]:
                    terms.append(seg)
        return terms

    # ========== Step 3: 章节聚合 ==========

    def _group_by_chapter(self, products: List[Dict]) -> Dict[str, List[Dict]]:
        """按章节聚合参考内容"""
        chapters = {}
        for product in products:
            for chapter in product.get("chapters", []):
                ch_name = chapter.get("chapter_name", "其他")
                std_name = ch_name
                for std in CLAUSE_CHAPTERS:
                    if std in ch_name or ch_name in std:
                        std_name = std
                        break
                if std_name not in chapters:
                    chapters[std_name] = []
                for section in chapter.get("sections", []):
                    chapters[std_name].append({
                        "product_name": product["product_name"],
                        "chapter_name": ch_name,
                        "content": section.get("content", ""),
                        "title": section.get("title", ""),
                    })
        return chapters

    # ========== Step 4: 章节生成 Prompt ==========

    def _build_chapter_prompt(self, chapter_name: str, refs: List[Dict],
                               insurance_type: str, all_chapters: List[str],
                               start_number: int = 1) -> str:
        """构建章节生成提示词"""
        ref_texts = []
        for r in refs[:8]:
            snippet = r["content"][:500]
            ref_texts.append(
                f"【参考来源：{r['product_name']} > {r['chapter_name']}】\n{snippet}"
            )
        refs_block = "\n\n".join(ref_texts) if ref_texts else "（无直接参考，请根据行业标准生成）"

        prompt = f"""你是一名资深保险条款起草专家。请根据以下参考内容，为「{insurance_type}」类保险产品起草「{chapter_name}」章节的条款。

要求：
1. 条款必须专业、严谨、符合中国保险监管法规
2. 每条条款后标注来源，格式：[产品名 > 章节名]
3. 如果参考内容有多条，需综合归纳，不可直接复制
4. 条款编号从第{start_number}条开始连续编号（全书连续编号，不按章节重新开始）
5. 语言风格与正式保险条款一致
6. 直接输出条款内容，不要输出章节标题（系统会自动添加）

本产品章节结构：{' > '.join(all_chapters)}

参考内容（来自真实产品条款）：
{refs_block}

请从第{start_number}条开始，生成「{chapter_name}」章节的完整条款内容："""
        return prompt

    # ========== 主流程：流式生成 ==========

    async def draft_stream(self, query: str) -> AsyncGenerator[str, None]:
        """流式生成条款（SSE格式）- LLM 驱动"""
        import httpx

        # Step 1: LLM 理解用户意图，提取险种
        yield f"data: {json.dumps({'type': 'progress', 'step': 'intent', 'message': '正在理解您的需求...'}, ensure_ascii=False)}\n\n"

        insurance_type = await self._extract_insurance_type_llm(query)

        yield f"data: {json.dumps({'type': 'progress', 'step': 'intent', 'message': f'识别险种：{insurance_type}'}, ensure_ascii=False)}\n\n"

        # Step 2: 检索相关产品（向量语义 + 模糊匹配）
        yield f"data: {json.dumps({'type': 'progress', 'step': 'search', 'message': '正在从知识库中检索相关产品...'}, ensure_ascii=False)}\n\n"

        products = self._search_related_products(insurance_type)
        product_names = [p["product_name"] for p in products]

        yield f"data: {json.dumps({'type': 'progress', 'step': 'search', 'message': f'找到 {len(products)} 个相关产品', 'products': product_names}, ensure_ascii=False)}\n\n"

        if not products:
            yield f"data: {json.dumps({'type': 'error', 'message': f'未找到与「{insurance_type}」相关的产品，请尝试其他险种'}, ensure_ascii=False)}\n\n"
            return

        # Step 3: 按章节聚合参考内容
        chapter_refs = self._group_by_chapter(products)
        chapters_to_generate = [ch for ch in CLAUSE_CHAPTERS if ch in chapter_refs]
        if not chapters_to_generate:
            chapters_to_generate = CLAUSE_CHAPTERS[:5]

        yield f"data: {json.dumps({'type': 'progress', 'step': 'chapters', 'message': f'将生成 {len(chapters_to_generate)} 个章节', 'chapters': chapters_to_generate}, ensure_ascii=False)}\n\n"

        # Step 4: 逐章节 LLM 生成
        clause_number = 1  # 全局条款编号计数器
        async with httpx.AsyncClient(timeout=120.0) as client:
            for idx, chapter_name in enumerate(chapters_to_generate):
                refs = chapter_refs.get(chapter_name, [])
                prompt = self._build_chapter_prompt(chapter_name, refs, insurance_type, chapters_to_generate, start_number=clause_number)

                yield f"data: {json.dumps({'type': 'chapter_start', 'chapter': chapter_name, 'index': idx, 'total': len(chapters_to_generate)}, ensure_ascii=False)}\n\n"

                chapter_content = f"## {chapter_name}\n\n"
                yield f"data: {json.dumps({'type': 'content', 'content': chapter_content}, ensure_ascii=False)}\n\n"

                chapter_text = ""
                try:
                    if _is_anthropic():
                        url = f"{LLM_BASE_URL}/v1/messages"
                        req_headers = _anthropic_headers()
                        payload = {"model": LLM_MODEL, "messages": [{"role": "user", "content": prompt}],
                                   "max_tokens": 4096, "temperature": 0.7, "stream": True}
                    else:
                        url = f"{LLM_BASE_URL}/chat/completions"
                        req_headers = _openai_headers()
                        payload = {"model": LLM_MODEL, "messages": [{"role": "user", "content": prompt}],
                                   "stream": True, "temperature": 0.7, "max_tokens": 4096}

                    async with client.stream("POST", url, headers=req_headers, json=payload) as response:
                        print(f"[ClauseDraft] 章节「{chapter_name}」HTTP {response.status_code}")
                        if response.status_code != 200:
                            error_body = await response.aread()
                            error_decoded = error_body.decode()[:500]
                            print(f"[ClauseDraft] Error body: {error_decoded}")
                            error_text = f"\n\n> ⚠️ LLM调用失败 (HTTP {response.status_code}): {error_decoded[:200]}\n\n"
                            yield f"data: {json.dumps({'type': 'content', 'content': error_text}, ensure_ascii=False)}\n\n"
                            continue

                        chunk_count = 0
                        if _is_anthropic():
                            # Anthropic SSE 流式解析
                            async for line in response.aiter_lines():
                                line = line.strip()
                                if not line:
                                    continue
                                if line.startswith("event: "):
                                    continue
                                if line.startswith("data: "):
                                    data_str = line[6:].strip()
                                    try:
                                        data = json.loads(data_str)
                                        if data.get("type") == "content_block_delta":
                                            delta = data.get("delta", {})
                                            if delta.get("type") == "text_delta":
                                                text = delta.get("text", "")
                                                if text:
                                                    chunk_count += 1
                                                    chapter_text += text
                                                    yield f"data: {json.dumps({'type': 'content', 'content': text}, ensure_ascii=False)}\n\n"
                                        elif data.get("type") == "message_stop":
                                            break
                                    except json.JSONDecodeError:
                                        continue
                        else:
                            # OpenAI SSE 流式解析
                            async for line in response.aiter_lines():
                                if not line.startswith("data: "):
                                    continue
                                data_str = line[6:].strip()
                                if data_str == "[DONE]":
                                    break
                                try:
                                    chunk = json.loads(data_str)
                                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                                    text = delta.get("content", "")
                                    if text:
                                        chunk_count += 1
                                        chapter_text += text
                                        yield f"data: {json.dumps({'type': 'content', 'content': text}, ensure_ascii=False)}\n\n"
                                except json.JSONDecodeError:
                                    continue

                        print(f"[ClauseDraft] 章节「{chapter_name}」完成，收到 {chunk_count} 个文本块")
                except Exception as e:
                    import traceback
                    print(f"[ClauseDraft] 章节「{chapter_name}」异常: {e}")
                    traceback.print_exc()
                    error_text = f"\n\n> ⚠️ 章节「{chapter_name}」生成失败：{str(e)}\n\n"
                    chapter_text += error_text
                    yield f"data: {json.dumps({'type': 'content', 'content': error_text}, ensure_ascii=False)}\n\n"

                # 计算本章节中出现的条款编号数量，更新全局计数器
                article_matches = re.findall(r'第[一二三四五六七八九十百零\d]+条', chapter_text)
                if article_matches:
                    clause_number += len(article_matches)
                else:
                    clause_number += 3

                yield f"data: {json.dumps({'type': 'content', 'content': '\n\n'}, ensure_ascii=False)}\n\n"

        # Step 5: 完成
        yield f"data: {json.dumps({'type': 'done', 'message': '条款生成完成', 'insurance_type': insurance_type, 'source_products': product_names}, ensure_ascii=False)}\n\n"

    def is_clause_generation_intent(self, query: str) -> bool:
        """判断是否为条款生成意图"""
        keywords = ["生成", "起草", "撰写", "编写", "制作", "帮我写", "帮我生成",
                     "产品条款", "条款草案", "起草条款", "生成条款"]
        query_lower = query.lower()
        return any(kw in query_lower for kw in keywords)