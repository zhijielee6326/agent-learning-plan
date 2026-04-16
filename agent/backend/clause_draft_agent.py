"""
条款生成工作流 Agent（模板拼装版本）
- 规则意图识别 → 向量语义检索 → 章节聚合 → 模板拼装（不调用LLM）→ 溯源标注
"""
import json
import re
import os
import asyncio
from typing import AsyncGenerator, List, Dict
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from product_store import ProductStore, get_product_store, STANDARD_CHAPTER_ORDER

# 标准章节列表（用于生成条款）
CLAUSE_CHAPTERS = [
    "总则", "保险责任", "责任免除", "保险金额", "保险期间",
    "保险人义务", "投保人及被保险人义务", "保险金申请", "争议处理", "释义"
]


class ClauseDraftAgent:
    """条款生成工作流 - 模板拼装（不调用LLM，纯检索+组装）"""

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
            tens = remainder // 10
            ones = remainder % 10
            return result + digits[tens] + "十" + (digits[ones] if ones else "")
        return str(num)

    # ========== 释义专用逻辑 ==========

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
        bracket_terms = re.findall(r'「([^」]+)」', all_chapter_text)
        found_terms.update(bracket_terms)
        aka_terms = re.findall(r'以下简称[""\']?([^""\')】]{2,20})[""\']?', all_chapter_text)
        found_terms.update(aka_terms)
        aka_terms2 = re.findall(r'[（(]以下简称([^)）]+)[)）]', all_chapter_text)
        found_terms.update(aka_terms2)
        for product in products:
            for chapter in product.get("chapters", []):
                ch_name = chapter.get("chapter_name", "")
                if "释义" in ch_name:
                    for section in chapter.get("sections", []):
                        content = section.get("content", "")
                        defined = re.findall(r'^[一二三四五六七八九十\d]+[\.、]\s*([^：:是指]{2,20}?)[：:]', content, re.MULTILINE)
                        found_terms.update(defined)
        for term in self.COMMON_INSURANCE_TERMS:
            if term in all_chapter_text:
                found_terms.add(term)
        result = [t for t in found_terms if 2 <= len(t) <= 20]
        return sorted(set(result))

    def _build_definition_chapter(self, terms: List[str], products: List[Dict],
                                   start_number: int) -> str:
        """构建释义章节：按名词聚合释义，多版本选最新"""
        term_definitions = {}
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
                                    "content": content, "product_name": product_name,
                                    "filing_no": filing_no, "filing_time": filing_time,
                                    "registry_no": registry_no,
                                })
                                break
        clauses = []
        clause_num = start_number
        for term in terms:
            defs = term_definitions.get(term, [])
            if defs:
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
                clauses.append(f"第{self._num_to_chinese(clause_num)}条 {term}：指本保险合同中约定的{term}的含义，具体以保险单载明为准。")
            clause_num += 1
        if not clauses:
            return ""
        return "\n\n".join(clauses) + "\n"

    # ========== Step 1: 规则意图识别（不调LLM） ==========

    def _extract_insurance_type(self, query: str) -> str:
        """规则提取险种名称"""
        clean = query
        # 先去除长前缀（避免短词误删）
        for prefix in ["帮我生成一份", "帮我起草一份", "帮我设计一份", "帮我创建一份",
                        "生成一份", "起草一份", "设计一份", "创建一份",
                        "帮我写一份", "帮我做一份", "帮我生成", "帮我起草",
                        "帮我设计", "帮我创建",
                        "的产品条款", "产品条款", "保险条款"]:
            clean = clean.replace(prefix, "")
        # 再去除短指令词（仅在词首/词尾匹配）
        for word in ["生成", "起草", "撰写", "编写", "制作", "设计", "创建", "条款"]:
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
                print(f"[ClauseDraft-Template] 向量语义检索找到 {len(matched_ids)} 个产品")
            except Exception as e:
                print(f"[ClauseDraft-Template] 向量检索失败: {e}")

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

        print(f"[ClauseDraft-Template] 合计匹配 {len(matched_ids)} 个产品ID")

        # 3. 获取完整产品数据
        full_products = []
        for pid in matched_ids[:limit]:
            full = self.store.get_product(pid)
            if full:
                full_products.append(full)

        print(f"[ClauseDraft-Template] 获取到 {len(full_products)} 个完整产品数据")
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

    # ========== Step 4: 模板拼装 ==========

    def _assemble_chapter(self, chapter_name: str, refs: List[Dict],
                          start_number: int) -> str:
        """模板拼装章节：从参考条款中选取、去重、合并、编号"""
        if not refs:
            return f"第{self._num_to_chinese(start_number)}条 本保险合同的{chapter_name}由保险人与投保人在投保时协商确定，具体内容以保险单载明为准。\n"

        seen_contents = set()
        clauses = []
        clause_num = start_number

        for ref in refs:
            content = ref.get("content", "").strip()
            product_name = ref.get("product_name", "未知产品")

            # 按"第X条"分割为单条条款
            sub_clauses = re.split(r'(?=第[一二三四五六七八九十百零\d]+条)', content)
            for sub in sub_clauses:
                sub = sub.strip()
                if not sub or len(sub) < 10:
                    continue
                # 去重：取前50字作为指纹
                fingerprint = sub[:50].replace(" ", "")
                if fingerprint in seen_contents:
                    continue
                seen_contents.add(fingerprint)

                # 重写编号
                sub = re.sub(
                    r'^第[一二三四五六七八九十百零\d]+条',
                    f'第{self._num_to_chinese(clause_num)}条',
                    sub
                )
                clause_num += 1
                # 添加溯源标注
                clauses.append(f"{sub} [{product_name} > {ref.get('chapter_name', chapter_name)}]")

            # 每章最多取 8 条，避免过长
            if len(clauses) >= 8:
                break

        if not clauses:
            return f"第{self._num_to_chinese(start_number)}条 本保险合同的{chapter_name}由保险人与投保人在投保时协商确定，具体内容以保险单载明为准。\n"

        return "\n\n".join(clauses) + "\n"

    # ========== 主流程：模板拼装流式输出 ==========

    async def draft_stream(self, query: str) -> AsyncGenerator[str, None]:
        """流式生成条款（SSE格式）- 模板拼装版本
        流程：规则意图识别 → 向量检索 → 章节聚合 → 去重合并 → 编号重排 → 输出
        不调用 LLM，响应时间 < 2秒
        """
        try:
            # Step 1: 规则提取险种
            yield f"data: {json.dumps({'type': 'progress', 'step': 'intent', 'message': '正在分析需求...'}, ensure_ascii=False)}\n\n"

            insurance_type = self._extract_insurance_type(query)

            yield f"data: {json.dumps({'type': 'progress', 'step': 'intent', 'message': f'识别险种：{insurance_type}'}, ensure_ascii=False)}\n\n"

            # Step 2: 向量检索相关产品
            yield f"data: {json.dumps({'type': 'progress', 'step': 'search', 'message': '正在从知识库中检索相关产品...'}, ensure_ascii=False)}\n\n"

            products = await self._search_related_products_async(insurance_type)
            product_names = [p["product_name"] for p in products]

            yield f"data: {json.dumps({'type': 'progress', 'step': 'search', 'message': f'找到 {len(products)} 个相关产品', 'products': product_names}, ensure_ascii=False)}\n\n"

            if not products:
                print(f"[ClauseDraft-Template] 未找到「{insurance_type}」相关产品，回退到默认产品")
                fallback_type = "意外伤害保险"
                yield f"data: {json.dumps({'type': 'progress', 'step': 'search', 'message': f'未找到精确匹配，将以「{fallback_type}」类产品为参考'}, ensure_ascii=False)}\n\n"
                insurance_type = fallback_type
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

            yield f"data: {json.dumps({'type': 'progress', 'step': 'chapters', 'message': f'模板拼装 {len(chapters_to_generate)} 个章节', 'chapters': chapters_to_generate}, ensure_ascii=False)}\n\n"

            # Step 4: 模板拼装 — 每章取最优参考，去重合并
            clause_number = 1
            all_chapter_text = ""  # 收集所有章节文本，用于释义提取
            for idx, chapter_name in enumerate(chapters_to_generate):
                yield f"data: {json.dumps({'type': 'chapter_start', 'chapter': chapter_name, 'index': idx, 'total': len(chapters_to_generate)}, ensure_ascii=False)}\n\n"

                chapter_header = f"## {chapter_name}\n\n"
                yield f"data: {json.dumps({'type': 'content', 'content': chapter_header}, ensure_ascii=False)}\n\n"

                if chapter_name == "释义":
                    # 释义章节：专用逻辑 — 提取名词 + 聚合释义 + 选最新版本
                    terms = self._extract_defined_terms(all_chapter_text, products)
                    chapter_text = self._build_definition_chapter(terms, products, clause_number)
                else:
                    refs = chapter_refs.get(chapter_name, [])
                    chapter_text = self._assemble_chapter(chapter_name, refs, clause_number)

                all_chapter_text += chapter_text + "\n"

                # 更新条款编号
                line_start_articles = re.findall(r'^\s*第[一二三四五六七八九十百零\d]+条', chapter_text, re.MULTILINE)
                if line_start_articles:
                    clause_number += len(line_start_articles)
                else:
                    clause_number += 2

                # 流式输出（模拟逐字效果）
                chunk_size = 20
                for i in range(0, len(chapter_text), chunk_size):
                    chunk = chapter_text[i:i + chunk_size]
                    yield f"data: {json.dumps({'type': 'content', 'content': chunk}, ensure_ascii=False)}\n\n"
                    await asyncio.sleep(0.005)

                yield f"data: {json.dumps({'type': 'content', 'content': '\n\n'}, ensure_ascii=False)}\n\n"

            # Step 5: 完成
            yield f"data: {json.dumps({'type': 'done', 'message': '条款生成完成（模板版）', 'insurance_type': insurance_type, 'source_products': product_names}, ensure_ascii=False)}\n\n"

        except Exception as e:
            import traceback
            print(f"[ClauseDraft-Template] draft_stream 异常: {e}")
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'message': f'条款生成异常：{str(e)}'}, ensure_ascii=False)}\n\n"

    def is_clause_generation_intent(self, query: str) -> bool:
        """判断是否为条款生成意图（减少误匹配）"""
        generation_keywords = ["生成", "起草", "撰写", "编写", "制作", "设计", "创建", "帮我写", "帮我生成", "帮我做", "帮我设计", "帮我创建"]
        context_keywords = ["条款", "保险", "产品"]
        query_lower = query.lower()
        has_gen = any(kw in query_lower for kw in generation_keywords)
        has_ctx = any(kw in query_lower for kw in context_keywords)
        return has_gen and has_ctx
