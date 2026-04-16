"""
条款生成工作流 Agent
- LLM 意图理解 → 向量语义检索 → 逐章节 LLM 生成（流式输出）→ 溯源标注
"""
import json
import re
import os
import asyncio
from typing import AsyncGenerator, List, Dict
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from product_store import ProductStore, get_product_store, STANDARD_CHAPTER_ORDER

# API 配置（统一使用模块级常量）
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

    # ========== 工具方法 ==========

    @staticmethod
    def _num_to_chinese(num: int) -> str:
        """数字转中文序号（支持 1-999）"""
        digits = "零一二三四五六七八九"
        if num <= 0:
            return "零"
        if num < 10:
            return digits[num]
        if num < 20:
            return "十" + (digits[num - 10] if num > 10 else "")
        if num < 100:
            tens = num // 10
            ones = num % 10
            return digits[tens] + "十" + (digits[ones] if ones else "")
        if num < 1000:
            hundreds = num // 100
            remainder = num % 100
            result = digits[hundreds] + "百"
            if remainder == 0:
                return result
            if remainder < 10:
                return result + "零" + digits[remainder]
            if remainder < 20:
                return result + "一十" + (digits[remainder - 10] if remainder > 10 else "")
            # 20-99
            tens = remainder // 10
            ones = remainder % 10
            return result + digits[tens] + "十" + (digits[ones] if ones else "")
        return str(num)

    # ========== 释义专用逻辑 ==========

    # 常见保险术语名词列表（用于匹配需释义的名词）
    COMMON_INSURANCE_TERMS = [
        "保险人", "投保人", "被保险人", "受益人",
        "保险金额", "保险费", "保险期间", "保险责任", "责任免除",
        "意外伤害", "意外事故", "伤残", "身故", "全残",
        "免赔额", "免赔率", "等待期", "观察期", "犹豫期",
        "赔偿限额", "给付比例", "保险金", "理赔",
        "不可抗力", "手续费", "现金价值", "保单年度",
        "医疗机构", "专科医生", "住院", "门诊", "手术",
        "重大疾病", "轻症疾病", "中症疾病",
        "职业类别", "危险等级",
        "保证续保", "续保", "解除合同", "中止", "复效",
        "如实告知", "年龄误告",
        "法定继承人", "近亲属",
    ]

    def _extract_defined_terms(self, all_chapter_text: str, products: List[Dict]) -> List[str]:
        """从已生成章节文本和产品条款中提取需释义的名词"""
        found_terms = set()
        # 1. 从「」包裹的名词
        bracket_terms = re.findall(r'「([^」]+)」', all_chapter_text)
        found_terms.update(bracket_terms)
        # 2. "以下简称XXX"模式
        aka_terms = re.findall(r'以下简称["""\']\s*([^""\""\')\s]{2,15})\s*["""\']?', all_chapter_text)
        found_terms.update(aka_terms)
        # 3. "（以下简称XXX）"模式
        aka_terms2 = re.findall(r'[（(]以下简称\s*["""\']?\s*([^""\""\')\s]{2,15})\s*["""\']?\s*[)）]', all_chapter_text)
        found_terms.update(aka_terms2)
        # 4. 从产品释义章节提取已有名词
        for product in products:
            for chapter in product.get("chapters", []):
                ch_name = chapter.get("chapter_name", "")
                if "释义" in ch_name:
                    for section in chapter.get("sections", []):
                        content = section.get("content", "")
                        defined = re.findall(r'^[一二三四五六七八九十\d]+[\.、]\s*([^：:是指]{2,15}?)[：:是指]', content, re.MULTILINE)
                        found_terms.update(defined)
        # 5. 常见术语列表匹配
        for term in self.COMMON_INSURANCE_TERMS:
            if term in all_chapter_text:
                found_terms.add(term)
        # 清洗：去除引号、过滤无效名词
        cleaned = set()
        for t in found_terms:
            t = t.strip().strip('"\'""''《》【】').strip()
            if 2 <= len(t) <= 15 and re.match(r'^[a-zA-Z\u4e00-\u9fff]+$', t):
                cleaned.add(t)
        return sorted(cleaned)

    def _build_definition_chapter(self, terms: List[str], products: List[Dict],
                                   start_number: int) -> str:
        """构建释义章节：按名词聚合释义，多版本选最新"""
        # 从所有产品的"释义"章节中收集释义
        term_definitions = {}  # term -> [{"content": ..., "product_name": ..., "filing_no": ..., "filing_time": ...}]

        for product in products:
            for chapter in product.get("chapters", []):
                ch_name = chapter.get("chapter_name", "")
                if "释义" not in ch_name:
                    continue
                filing_no = product.get("filing_no", "")
                filing_time = product.get("filing_time", "")
                registry_no = product.get("registry_no", "")
                product_name = product.get("product_name", "")

                for section in chapter.get("sections", []):
                    content = section.get("content", "")
                    # 解析 "XXX：..." 或 "XXX是指..." 格式
                    for term in terms:
                        patterns = [
                            rf'{re.escape(term)}[是指：:]+',
                            rf'^[一二三四五六七八九十\d]+[\.、]\s*{re.escape(term)}\s*[：:是指]',
                        ]
                        for pattern in patterns:
                            match = re.search(pattern, content)
                            if match:
                                if term not in term_definitions:
                                    term_definitions[term] = []
                                term_definitions[term].append({
                                    "content": content,
                                    "product_name": product_name,
                                    "filing_no": filing_no,
                                    "filing_time": filing_time,
                                    "registry_no": registry_no,
                                })
                                break

        # 生成释义条款
        clauses = []
        clause_num = start_number
        for term in terms:
            defs = term_definitions.get(term, [])
            if defs:
                # 按 filing_time 降序排，选最新的
                defs.sort(key=lambda d: d.get("filing_time", ""), reverse=True)
                best = defs[0]
                source = f"[{best['product_name']}"
                if best.get("registry_no"):
                    source += f" > {best['registry_no']}"
                if best.get("filing_time"):
                    source += f" > {best['filing_time']}"
                source += "]"
                clauses.append(f"第{self._num_to_chinese(clause_num)}条 {term}：{best['content']} {source}")
            else:
                # 无参考释义，生成占位
                clauses.append(f"第{self._num_to_chinese(clause_num)}条 {term}：指本保险合同中约定的{term}的含义，具体以保险单载明为准。")
            clause_num += 1

        if not clauses:
            return ""
        return "\n\n".join(clauses) + "\n"

    # ========== Step 1: LLM 意图理解 ==========

    async def _extract_insurance_type_llm(self, query: str) -> str:
        """让 LLM 理解用户意图，提取险种名称"""
        import httpx

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
                    resp = await client.post(
                        f"{LLM_BASE_URL}/v1/messages",
                        headers=_anthropic_headers(),
                        json={"model": LLM_MODEL, "messages": [{"role": "user", "content": prompt}],
                              "max_tokens": 50, "temperature": 0.1}
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        blocks = data.get("content", [])
                        result = blocks[0].get("text", "").strip() if blocks else ""
                    else:
                        result = ""
                else:
                    resp = await client.post(
                        f"{LLM_BASE_URL}/chat/completions",
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
                print(f"[ClauseDraft] LLM 意图识别失败, 回退到规则提取")
        except Exception as e:
            print(f"[ClauseDraft] LLM 意图识别异常: {e}, 回退到规则提取")

        return self._fallback_extract(query)

    def _fallback_extract(self, query: str) -> str:
        """规则回退：去除常见指令词，提取产品名称"""
        clean = query
        # 先去除长前缀（避免短词误删）
        for prefix in ["帮我生成一份", "帮我起草一份", "生成一份", "起草一份",
                        "帮我写一份", "帮我做一份", "帮我生成", "帮我起草",
                        "的产品条款", "产品条款", "保险条款"]:
            clean = clean.replace(prefix, "")
        # 再去除短指令词（仅在词首/词尾匹配）
        for word in ["生成", "起草", "撰写", "编写", "制作", "条款"]:
            clean = re.sub(rf'^{word}', '', clean)
            clean = re.sub(rf'{word}$', '', clean)
        clean = clean.strip()
        return clean or "意外伤害保险"

    # ========== Step 2: 向量语义检索 + 产品库搜索 ==========

    async def _search_related_products_async(self, insurance_type: str, limit: int = 5) -> List[Dict]:
        """异步搜索相关产品"""
        matched_ids = []
        seen_ids = set()

        # 1. 向量语义检索
        if self.vector_store:
            try:
                vquery = f"{insurance_type}保险"
                results = await self.vector_store.search_async(vquery, limit=limit * 4, use_reranker=False)
                for r in results:
                    pid = getattr(r, 'product_id', None) or getattr(r, 'id', None)
                    if pid and pid not in seen_ids:
                        matched_ids.append(pid)
                        seen_ids.add(pid)
                print(f"[ClauseDraft] 向量语义检索找到 {len(matched_ids)} 个产品")
            except Exception as e:
                print(f"[ClauseDraft] 向量检索失败: {e}")

        # 2. 产品名模糊匹配（补充方式）
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
        """从险种名称中提取搜索词"""
        terms = [insurance_type]
        base = insurance_type.replace("保险", "").replace("险种", "").strip()
        if base and base != insurance_type:
            terms.append(base)
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
                # 优先匹配最长的标准章节名（避免短名误匹配）
                for std in sorted(CLAUSE_CHAPTERS, key=len, reverse=True):
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
4. 语言风格与正式保险条款一致
5. 直接输出条款内容，不要输出章节标题（系统会自动添加）

【重要】编号格式（必须严格遵守）：
- 每条条款必须以「第X条」开头，使用中文数字（第一条、第二条…第十条、第十一条…）
- 本章节从第{start_number}条开始连续编号
- 正确：第{start_number}条 本保险合同……
- 错误：1. 本保险合同……（禁止用阿拉伯数字加点）
- 必须使用中文数字格式，不要用"1."、"2."格式！

本产品章节结构：{' > '.join(all_chapters)}

参考内容（来自真实产品条款）：
{refs_block}

请从第{start_number}条开始，生成「{chapter_name}」章节的完整条款内容（至少包含2条，即使参考较少也应根据行业标准补充）："""
        return prompt

    # ========== 主流程：流式生成 ==========

    async def draft_stream(self, query: str) -> AsyncGenerator[str, None]:
        """流式生成条款（SSE格式）- LLM 驱动"""
        import httpx

        try:
            # Step 1: LLM 理解用户意图，提取险种
            yield f"data: {json.dumps({'type': 'progress', 'step': 'intent', 'message': '正在理解您的需求...'}, ensure_ascii=False)}\n\n"

            insurance_type = await self._extract_insurance_type_llm(query)

            yield f"data: {json.dumps({'type': 'progress', 'step': 'intent', 'message': f'识别险种：{insurance_type}'}, ensure_ascii=False)}\n\n"

            # Step 2: 检索相关产品（异步）
            yield f"data: {json.dumps({'type': 'progress', 'step': 'search', 'message': '正在从知识库中检索相关产品...'}, ensure_ascii=False)}\n\n"

            products = await self._search_related_products_async(insurance_type)
            product_names = [p["product_name"] for p in products]

            yield f"data: {json.dumps({'type': 'progress', 'step': 'search', 'message': f'找到 {len(products)} 个相关产品', 'products': product_names}, ensure_ascii=False)}\n\n"

            if not products:
                # 兜底：使用产品库中最常见的产品类型作为参考
                print(f"[ClauseDraft] 未找到「{insurance_type}」相关产品，回退到默认产品")
                fallback_type = "意外伤害保险"
                yield f"data: {json.dumps({'type': 'progress', 'step': 'search', 'message': f'未找到「{insurance_type}」的精确匹配，将以「{fallback_type}」类产品为参考生成'}, ensure_ascii=False)}\n\n"
                insurance_type = fallback_type
                # 从产品库中取前5个产品作为参考
                for product in self.store.products[:5]:
                    full = self.store.get_product(product.get("id", ""))
                    if full:
                        products.append(full)
                product_names = [p["product_name"] for p in products]
                yield f"data: {json.dumps({'type': 'progress', 'step': 'search', 'message': f'已选取 {len(products)} 个参考产品', 'products': product_names}, ensure_ascii=False)}\n\n"
                if not products:
                    yield f"data: {json.dumps({'type': 'error', 'message': '产品库为空，无法生成条款'}, ensure_ascii=False)}\n\n"
                    return

            # Step 3: 按章节聚合参考内容
            chapter_refs = self._group_by_chapter(products)
            chapters_to_generate = [ch for ch in CLAUSE_CHAPTERS if ch in chapter_refs]
            if not chapters_to_generate:
                chapters_to_generate = CLAUSE_CHAPTERS[:5]

            yield f"data: {json.dumps({'type': 'progress', 'step': 'chapters', 'message': f'将生成 {len(chapters_to_generate)} 个章节', 'chapters': chapters_to_generate}, ensure_ascii=False)}\n\n"

            # Step 4: 逐章节 LLM 生成（释义章节走专用逻辑）
            clause_number = 1
            all_generated_text = ""  # 收集已生成文本，用于释义名词提取
            async with httpx.AsyncClient(timeout=120.0) as client:
                for idx, chapter_name in enumerate(chapters_to_generate):

                    # ===== 释义章节：走专用逻辑（不调LLM） =====
                    if chapter_name == "释义":
                        yield f"data: {json.dumps({'type': 'chapter_start', 'chapter': chapter_name, 'index': idx, 'total': len(chapters_to_generate)}, ensure_ascii=False)}\n\n"
                        yield f"data: {json.dumps({'type': 'content', 'content': f'## {chapter_name}\n\n'}, ensure_ascii=False)}\n\n"

                        yield f"data: {json.dumps({'type': 'progress', 'step': 'definition', 'message': '正在提取需释义名词...'}, ensure_ascii=False)}\n\n"
                        terms = self._extract_defined_terms(all_generated_text, products)
                        print(f"[ClauseDraft] 释义：提取到 {len(terms)} 个名词")

                        yield f"data: {json.dumps({'type': 'progress', 'step': 'definition', 'message': f'生成 {len(terms)} 个名词释义'}, ensure_ascii=False)}\n\n"
                        definition_text = self._build_definition_chapter(terms, products, clause_number)

                        if definition_text:
                            # 流式输出释义内容
                            chunk_size = 40
                            for i in range(0, len(definition_text), chunk_size):
                                chunk = definition_text[i:i + chunk_size]
                                yield f"data: {json.dumps({'type': 'content', 'content': chunk}, ensure_ascii=False)}\n\n"
                                await asyncio.sleep(0.005)

                            # 更新编号
                            line_start_articles = re.findall(r'^\s*第[一二三四五六七八九十百零\d]+条', definition_text, re.MULTILINE)
                            clause_number += len(line_start_articles) if line_start_articles else len(terms)
                            all_generated_text += "\n" + definition_text
                        else:
                            fallback = f"第{self._num_to_chinese(clause_number)}条 本保险合同中使用的术语和名词，其含义以保险单载明为准。\n"
                            yield f"data: {json.dumps({'type': 'content', 'content': fallback}, ensure_ascii=False)}\n\n"
                            clause_number += 1

                        yield f"data: {json.dumps({'type': 'content', 'content': '\n\n'}, ensure_ascii=False)}\n\n"
                        continue

                    # ===== 普通章节：LLM 流式生成 =====
                    refs = chapter_refs.get(chapter_name, [])
                    prompt = self._build_chapter_prompt(chapter_name, refs, insurance_type, chapters_to_generate, start_number=clause_number)

                    yield f"data: {json.dumps({'type': 'chapter_start', 'chapter': chapter_name, 'index': idx, 'total': len(chapters_to_generate)}, ensure_ascii=False)}\n\n"

                    chapter_header = f"## {chapter_name}\n\n"
                    yield f"data: {json.dumps({'type': 'content', 'content': chapter_header}, ensure_ascii=False)}\n\n"

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
                                error_decoded = error_body.decode()[:300]
                                print(f"[ClauseDraft] Error body: {error_decoded}")
                                # 错误消息用 error 类型，不混入 content
                                yield f"data: {json.dumps({'type': 'error', 'message': f'章节「{chapter_name}」LLM调用失败 (HTTP {response.status_code})'}, ensure_ascii=False)}\n\n"
                                continue

                            chunk_count = 0
                            if _is_anthropic():
                                async for line in response.aiter_lines():
                                    line = line.strip()
                                    if not line or line.startswith("event: "):
                                        continue
                                    if line.startswith("data: "):
                                        try:
                                            data = json.loads(line[6:].strip())
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

                        # 检测空章节
                        if len(chapter_text.strip()) < 20:
                            print(f"[ClauseDraft] 章节「{chapter_name}」内容过短，补充标准条款")
                            fallback = f"第{self._num_to_chinese(clause_number)}条 本保险合同的{chapter_name}由保险人与投保人在投保时协商确定，具体内容以保险单载明为准。\n"
                            chapter_text = fallback
                            yield f"data: {json.dumps({'type': 'content', 'content': fallback}, ensure_ascii=False)}\n\n"
                    except Exception as e:
                        import traceback
                        print(f"[ClauseDraft] 章节「{chapter_name}」异常: {e}")
                        traceback.print_exc()
                        yield f"data: {json.dumps({'type': 'error', 'message': f'章节「{chapter_name}」生成失败：{str(e)}'}, ensure_ascii=False)}\n\n"

                    # 只统计行首的"第X条"作为条款编号（排除正文引用）
                    line_start_articles = re.findall(r'^\s*第[一二三四五六七八九十百零\d]+条', chapter_text, re.MULTILINE)
                    if line_start_articles:
                        clause_number += len(line_start_articles)
                    else:
                        clause_number += 2

                    all_generated_text += "\n" + chapter_text
                    yield f"data: {json.dumps({'type': 'content', 'content': '\n\n'}, ensure_ascii=False)}\n\n"

            # Step 5: 完成
            yield f"data: {json.dumps({'type': 'done', 'message': '条款生成完成', 'insurance_type': insurance_type, 'source_products': product_names}, ensure_ascii=False)}\n\n"

        except Exception as e:
            # 外层兜底：确保客户端总能收到反馈
            import traceback
            print(f"[ClauseDraft] draft_stream 外层异常: {e}")
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'message': f'条款生成异常：{str(e)}'}, ensure_ascii=False)}\n\n"

    def is_clause_generation_intent(self, query: str) -> bool:
        """判断是否为条款生成意图（减少误匹配）"""
        # 必须包含"条款"或"保险"相关上下文，且包含生成类动词
        generation_keywords = ["生成", "起草", "撰写", "编写", "制作", "设计", "创建", "帮我写", "帮我生成", "帮我做", "帮我设计", "帮我创建"]
        context_keywords = ["条款", "保险", "产品"]
        query_lower = query.lower()
        has_gen = any(kw in query_lower for kw in generation_keywords)
        has_ctx = any(kw in query_lower for kw in context_keywords)
        return has_gen and has_ctx
