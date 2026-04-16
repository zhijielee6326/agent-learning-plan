"""
条款结构化生成引擎
- 严格按照标准结构输出条款
- 每条条款附加溯源标注
- 支持流式输出、精简/扩展/解释模式
"""

from typing import List, Dict, Optional, AsyncGenerator
from product_store import ProductStore, get_product_store, STANDARD_CHAPTER_ORDER, CHAPTER_ALIASES
import json
import asyncio


class ClauseGenerator:
    """条款生成引擎"""

    def __init__(self, store: ProductStore = None):
        self.store = store or get_product_store()

    async def generate_stream(
        self, query: str, mode: str = "standard"
    ) -> AsyncGenerator[str, None]:
        """
        流式生成条款文本
        mode: standard（标准）, brief（精简）, detailed（扩展）, explain（解释）
        """
        # 1. 解析用户意图
        intent = self._parse_intent(query)
        yield f"data: {json.dumps({'type': 'intent', 'data': intent}, ensure_ascii=False)}\n\n"

        # 2. 检索匹配条款
        if intent["action"] == "single_product":
            search_result = self.store.search_by_product(intent["product"])
        elif intent["action"] == "combined":
            combined = self.store.generate_combined_clauses(intent["products"])
            async for chunk in self._stream_combined(combined, mode):
                yield chunk
            return
        elif intent["action"] == "chapter":
            search_result = self.store.search_by_chapter(
                intent["chapter"],
                [self.store.get_product_by_name(n)["id"]
                 for n in intent.get("products", [])] if intent.get("products") else None
            )
        else:
            search_result = self.store.search_sections(query)

        if not search_result.results:
            yield f"data: {json.dumps({'type': 'no_result', 'message': '无匹配备案条款，请调整查询条件。'}, ensure_ascii=False)}\n\n"
            return

        # 3. 生成结构化条款（流式）
        async for chunk in self._stream_search_result(search_result, mode):
            yield chunk

    async def _stream_search_result(
        self, search_result, mode: str
    ) -> AsyncGenerator[str, None]:
        """流式输出检索结果"""
        # 按产品分组
        grouped = {}
        for r in search_result.results:
            if r.product_name not in grouped:
                grouped[r.product_name] = []
            grouped[r.product_name].append(r)

        source_list = []

        for product_name, sections in grouped.items():
            # 产品标题
            yield f"data: {json.dumps({'type': 'title', 'content': f'## 【{product_name}】'}, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.05)

            # 按章节分组
            chapters = {}
            for s in sections:
                std_ch = CHAPTER_ALIASES.get(s.chapter_name, s.chapter_name)
                if std_ch not in chapters:
                    chapters[std_ch] = []
                chapters[std_ch].append(s)

            chapter_idx = 0
            for ch_name in STANDARD_CHAPTER_ORDER:
                if ch_name not in chapters:
                    continue
                chapter_idx += 1

                # 章节标题
                yield f"data: {json.dumps({'type': 'chapter', 'content': f'### 第{self._number_to_chinese(chapter_idx)}章：{ch_name}'}, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0.03)

                for idx, sec in enumerate(chapters[ch_name], 1):
                    source_tag = f"[{sec.product_name}>{sec.chapter_name}>{sec.title}]"
                    source_list.append(source_tag)

                    if mode == "brief":
                        content = f"**{sec.title}**：{self._brief_content(sec.content)} {source_tag}"
                    elif mode == "explain":
                        content = f"**{idx}. {sec.title}**\n\n{sec.content} {source_tag}\n\n> 📖 **条款解读**：{self._explain_section(sec)}"
                    else:
                        content = f"{idx}. **{sec.title}**：{sec.content} {source_tag}"

                    yield f"data: {json.dumps({'type': 'clause', 'content': content}, ensure_ascii=False)}\n\n"
                    await asyncio.sleep(0.05)

        # 来源清单
        yield f"data: {json.dumps({'type': 'sources', 'sources': source_list}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"

    async def _stream_combined(
        self, combined: Dict, mode: str
    ) -> AsyncGenerator[str, None]:
        """流式输出组合产品条款"""
        sources = combined.get("sources", [])
        chapters = combined.get("chapters", {})

        product_names = " + ".join([s["product_name"] for s in sources])
        yield f"data: {json.dumps({'type': 'title', 'content': f'## 【组合产品条款：{product_names}】'}, ensure_ascii=False)}\n\n"
        await asyncio.sleep(0.05)

        source_list = []

        chapter_idx = 0
        for ch_name in STANDARD_CHAPTER_ORDER:
            if ch_name not in chapters:
                continue
            chapter_idx += 1

            yield f"data: {json.dumps({'type': 'chapter', 'content': f'### 第{self._number_to_chinese(chapter_idx)}章：{ch_name}'}, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.03)

            clause_idx = 0
            for sec in chapters[ch_name]:
                clause_idx += 1
                source_list.append(sec["source_tag"])

                if mode == "brief":
                    content = f"**{sec['title']}**（{sec['product_name']}）：{self._brief_content(sec['content'])} {sec['source_tag']}"
                elif mode == "explain":
                    content = f"{clause_idx}. **{sec['title']}**（{sec['product_name']}）\n\n{sec['content']} {sec['source_tag']}\n\n> 📖 **条款解读**：该条款来源于{sec['product_name']}，备案号：{sec['filing_no']}。"
                else:
                    content = f"{clause_idx}. **{sec['title']}**（{sec['product_name']}）：{sec['content']} {sec['source_tag']}"

                yield f"data: {json.dumps({'type': 'clause', 'content': content}, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0.05)

        yield f"data: {json.dumps({'type': 'sources', 'sources': source_list}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"

    def generate_static(self, query: str, mode: str = "standard") -> Dict:
        """非流式生成（用于导出）"""
        intent = self._parse_intent(query)

        if intent["action"] == "combined":
            combined = self.store.generate_combined_clauses(intent["products"])
            return self._format_combined_static(combined, mode)

        if intent["action"] == "single_product":
            search_result = self.store.search_by_product(intent["product"])
        elif intent["action"] == "chapter":
            search_result = self.store.search_by_chapter(intent["chapter"])
        else:
            search_result = self.store.search_sections(query)

        if not search_result.results:
            return {
                "markdown": "无匹配备案条款，请调整查询条件。",
                "sources": [],
                "matched_products": []
            }

        return self._format_search_static(search_result, mode)

    def _format_search_static(self, search_result, mode: str) -> Dict:
        """格式化检索结果为静态文本"""
        grouped = {}
        for r in search_result.results:
            if r.product_name not in grouped:
                grouped[r.product_name] = []
            grouped[r.product_name].append(r)

        md_lines = []
        source_list = []

        for product_name, sections in grouped.items():
            md_lines.append(f"## 【{product_name}】\n")

            chapters = {}
            for s in sections:
                std_ch = CHAPTER_ALIASES.get(s.chapter_name, s.chapter_name)
                if std_ch not in chapters:
                    chapters[std_ch] = []
                chapters[std_ch].append(s)

            ch_idx = 0
            for ch_name in STANDARD_CHAPTER_ORDER:
                if ch_name not in chapters:
                    continue
                ch_idx += 1
                md_lines.append(f"### 第{self._number_to_chinese(ch_idx)}章：{ch_name}\n")

                for idx, sec in enumerate(chapters[ch_name], 1):
                    tag = f"[{sec.product_name}>{sec.chapter_name}>{sec.title}]"
                    source_list.append(tag)

                    if mode == "brief":
                        md_lines.append(f"**{sec.title}**：{self._brief_content(sec.content)} {tag}")
                    else:
                        md_lines.append(f"{idx}. **{sec.title}**：{sec.content} {tag}")
                md_lines.append("")

        return {
            "markdown": "\n".join(md_lines),
            "sources": source_list,
            "matched_products": search_result.matched_products
        }

    def _format_combined_static(self, combined: Dict, mode: str) -> Dict:
        """格式化组合产品条款"""
        sources = combined.get("sources", [])
        chapters = combined.get("chapters", {})
        product_names = " + ".join([s["product_name"] for s in sources])

        md_lines = [f"## 【组合产品条款：{product_names}】\n"]
        source_list = []

        ch_idx = 0
        for ch_name in STANDARD_CHAPTER_ORDER:
            if ch_name not in chapters:
                continue
            ch_idx += 1
            md_lines.append(f"### 第{self._number_to_chinese(ch_idx)}章：{ch_name}\n")

            for idx, sec in enumerate(chapters[ch_name], 1):
                source_list.append(sec["source_tag"])
                if mode == "brief":
                    md_lines.append(
                        f"**{sec['title']}**（{sec['product_name']}）："
                        f"{self._brief_content(sec['content'])} {sec['source_tag']}"
                    )
                else:
                    md_lines.append(
                        f"{idx}. **{sec['title']}**（{sec['product_name']}）："
                        f"{sec['content']} {sec['source_tag']}"
                    )
            md_lines.append("")

        return {
            "markdown": "\n".join(md_lines),
            "sources": source_list,
            "matched_products": [s["product_name"] for s in sources]
        }

    def _parse_intent(self, query: str) -> Dict:
        """解析用户查询意图"""
        query_lower = query.lower()

        # 检查是否为组合查询
        all_products = self.store.list_products()
        mentioned_products = []
        for p in all_products:
            if p["product_name"] in query or any(
                kw in query for kw in p["product_name"].split("（")[0].split("（")
            ):
                mentioned_products.append(p["product_name"])

        # 组合查询
        if "组合" in query or "+" in query or "搭配" in query:
            # 尝试提取所有产品名
            product_names = []
            for p in all_products:
                short_name = p["product_name"].split("（")[0]
                if short_name in query or p["product_name"] in query:
                    product_names.append(p["product_name"])

            if len(product_names) >= 2:
                return {
                    "action": "combined",
                    "products": product_names
                }

        # 单产品查询
        if len(mentioned_products) == 1:
            return {
                "action": "single_product",
                "product": mentioned_products[0]
            }

        # 章节查询
        for ch in STANDARD_CHAPTER_ORDER:
            if ch in query:
                return {
                    "action": "chapter",
                    "chapter": ch,
                    "products": mentioned_products if mentioned_products else None
                }

        # 通用搜索
        return {
            "action": "search",
            "keywords": query
        }

    def _brief_content(self, content: str) -> str:
        """精简内容（取前100字）"""
        if len(content) <= 100:
            return content
        return content[:100] + "……"

    def _explain_section(self, sec) -> str:
        """条款解释"""
        return (
            "该条款来源于{}（备案号：{}），"
            "属于「{}」章节中关于「{}」的规定。"
            "此条款明确了保险公司在该方面的权利与义务，以及对被保险人的保障范围。"
        ).format(sec.product_name, sec.filing_no, sec.chapter_name, sec.title)

    @staticmethod
    def _number_to_chinese(n: int) -> str:
        """数字转中文"""
        mapping = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九", 10: "十"}
        return mapping.get(n, str(n))
