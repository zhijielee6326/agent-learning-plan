"""
RAG智能体模块 - 向量检索 + LLM
- Qdrant 向量语义检索 + Reranker 精排
- 公司自建 Qwen 大模型生成回答
- 支持流式输出、多轮对话、溯源标注
- 支持通用对话 + 保险 RAG 双模式
"""

import os
import json
import httpx
import asyncio
from typing import List, Dict, Optional, AsyncGenerator
from dataclasses import dataclass, field

# 加载环境变量
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from product_store import get_product_store, ProductStore
from vector_store import get_vector_store, VectorStore


LLM_API_TYPE = os.getenv("LLM_API_TYPE", "openai")  # openai 或 anthropic
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "glm-5.1")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.z.ai/api/anthropic")


SYSTEM_PROMPT = """你是「保险产品精算智能体」，一位专业的保险条款分析专家，同时也是一位友好的AI助手。

## 双重角色

1. **通用对话**：你可以进行日常闲聊、回答一般性问题、提供帮助。对于非保险专业问题，用自然友好的方式回答。
2. **保险专家**：当用户询问保险相关问题时，你应基于检索到的备案条款数据进行专业分析。

## 保险专业工作原则（仅在回答保险相关问题时适用）

- **忠于原文**：所有条款引用必须来自检索到的真实备案条款数据。
- **溯源标注**：引用条款时，请使用 `[产品名称>章节名称>条款标题]` 的格式标注来源。
- **无结果诚实**：如果检索不到相关条款，可以基于你的专业知识给出一般性说明，但要明确标注"以上为一般性说明，非具体产品条款"。
- **专业准确**：使用准确的保险术语，解释清晰易懂。

## 回答格式

- 通用对话：自然友好的语言即可
- 保险专业问题：使用 Markdown 格式，引用条款时加粗关键内容，对比分析时使用表格，在回答末尾列出所有引用来源
"""


EDIT_SYSTEM_PROMPT = """你是「保险条款文字编辑助手」，专门协助编辑和优化保险条款文本。

## 工作原则

1. **只输出改写后的文本**：不要输出任何解释、说明、前言、后记。只输出改写后的最终文本。
2. **保持原意不变**：改写时不得改变原文的核心含义、法律效力和关键数据。
3. **遵循指令执行**：严格按照用户的编辑指令进行改写。
4. **保持专业术语**：保险专业术语（如"被保险人"、"免赔额"、"等待期"等）必须保留，不得替换为通俗用语（除非指令明确要求简化）。
5. **保持格式**：如果原文包含Markdown格式，改写后应保持相应的格式。

## 编辑指令类型

- **润色（polish）**：优化文字表达，使语句更通顺、专业，但不改变长度和结构。
- **简化（simplify）**：用更简洁的语言表达相同含义，删除冗余表述，适当缩减篇幅。
- **扩展（expand）**：在不改变原意的前提下，补充细节说明、增加举例、丰富表述。
- **专业改写（professional）**：使用更正式、规范的保险行业用语重新表述，提升文本的专业性和权威性。
- **自定义指令**：按照用户的具体要求进行改写。

## 重要提醒

你的输出将被直接替换原文使用，所以只能输出改写结果，不能有任何额外内容。"""


@dataclass
class ChatMessage:
    """对话消息"""
    role: str  # system, user, assistant
    content: str


@dataclass
class ConversationHistory:
    """对话历史"""
    session_id: str
    messages: List[ChatMessage] = field(default_factory=list)
    max_turns: int = 20  # 保留最近20轮

    def add_message(self, role: str, content: str):
        self.messages.append(ChatMessage(role=role, content=content))
        # 保持历史不超过最大轮数
        if len(self.messages) > self.max_turns * 2 + 1:
            system_msgs = [m for m in self.messages if m.role == "system"]
            other_msgs = [m for m in self.messages if m.role != "system"]
            self.messages = system_msgs + other_msgs[-self.max_turns * 2:]

    def to_list(self) -> List[Dict]:
        return [{"role": m.role, "content": m.content} for m in self.messages]


class LLMAgent:
    """RAG智能体 - 向量检索增强生成"""

    # 保险相关关键词
    INSURANCE_KEYWORDS = [
        "保险", "条款", "保单", "投保", "被保险", "受益人", "保费", "保额",
        "理赔", "赔付", "免赔", "责任", "等待期", "犹豫期", "宽限期",
        "重疾", "医疗", "寿险", "意外险", "年金", "豁免", "续保",
        "安心保", "康安百万", "幸福人生", "京备字", "备案",
        "险种", "保障", "承保", "退保", "现金价值", "保险金",
        "重大疾病", "轻症", "中症", "身故", "伤残", "住院",
        "报销", "给付", "组合", "对比", "产品", "精算",
        "投保人", "被保人", "受益", "合同", "生效", "终止",
        "疾病", "手术", "门诊", "住院", "年龄", "缴费",
        "赔偿", "保函", "免赔额", " deductible",
    ]

    def __init__(self, store: ProductStore = None, vector_store: VectorStore = None):
        self.store = store or get_product_store()
        self.vector_store = vector_store or get_vector_store()
        self.conversations: Dict[str, ConversationHistory] = {}
        self._max_sessions = 100  # 最大会话数，防止内存泄漏
        self.api_key = LLM_API_KEY
        self.model = LLM_MODEL
        self.base_url = LLM_BASE_URL
        # 复用 httpx 异步客户端（连接池）
        self._http_client: Optional[httpx.AsyncClient] = None

    def get_or_create_conversation(self, session_id: str) -> ConversationHistory:
        if session_id not in self.conversations:
            # 超过上限时淘汰最旧的会话
            if len(self.conversations) >= self._max_sessions:
                oldest_id = min(self.conversations, key=lambda k: len(self.conversations[k].messages))
                del self.conversations[oldest_id]
            conv = ConversationHistory(session_id=session_id)
            conv.add_message("system", SYSTEM_PROMPT)
            self.conversations[session_id] = conv
        return self.conversations[session_id]

    def _is_insurance_related(self, query: str) -> bool:
        """判断用户问题是否与保险相关"""
        stripped = query.strip()
        # 短问候语直接判定为非保险问题
        if len(stripped) <= 6 and not any(
            kw in stripped for kw in ["保险", "条款", "保单", "保费", "理赔", "产品", "投保", "保障"]
        ):
            return False
        return any(kw in stripped for kw in self.INSURANCE_KEYWORDS)

    async def _retrieve_context_async(self, query: str) -> Dict:
        """RAG检索（异步）：Qdrant 向量语义检索 + Reranker 精排"""
        # 1. 向量语义检索 + Reranker（异步）
        vector_results = await self.vector_store.search_async(
            query=query,
            limit=20,
            min_score=0.2,
            use_reranker=True,
            rerank_top_k=10,
        )

        results = vector_results

        # 2. 补充：如果提到具体产品名，也用关键词检索补充
        all_products = self.store.list_products()
        mentioned_products = []
        for p in all_products:
            short_name = p["product_name"].split("（")[0]
            if short_name in query or p["product_name"] in query:
                mentioned_products.append(p["product_name"])

        # 获取已有的 section_id 用于去重
        existing_ids = {r.section_id for r in results}

        for pname in mentioned_products:
            product_result = self.store.search_by_product(pname)
            for r in product_result.results:
                if r.section_id not in existing_ids:
                    from vector_store import VectorSearchResult
                    results.append(VectorSearchResult(
                        product_id=r.product_id,
                        product_name=r.product_name,
                        filing_no=r.filing_no,
                        chapter_name=r.chapter_name,
                        section_id=r.section_id,
                        title=r.title,
                        content=r.content,
                        score=0.5,  # 关键词补充的默认分数
                    ))
                    existing_ids.add(r.section_id)

        # 3. 限制上下文长度
        if len(results) > 30:
            results = results[:30]

        # 4. 格式化上下文
        context_parts = []
        sources = []
        for r in results:
            source_tag = f"[{r.product_name}>{r.chapter_name}>{r.title}]"
            sources.append(source_tag)
            context_parts.append(
                f"**来源**: {source_tag}（相关度: {r.score:.3f}）\n"
                f"**条款标题**: {r.title}\n"
                f"**所属产品**: {r.product_name}（备案号：{r.filing_no}）\n"
                f"**所属章节**: {r.chapter_name}\n"
                f"**条款内容**: {r.content}\n"
            )

        product_list = self.store.list_products()

        return {
            "has_results": len(results) > 0,
            "context": "\n---\n".join(context_parts) if context_parts else "未找到相关条款",
            "sources": sources,
            "matched_count": len(results),
            "matched_products": list(set(r.product_name for r in results)),
            "available_products": [
                f"{p['product_name']}（{p['category']}/{p['sub_category']}，备案号：{p['filing_no']}）"
                for p in product_list
            ]
        }

    def _build_messages(self, session_id: str, query: str, context: Dict) -> List[Dict]:
        """构建发送给LLM的消息列表"""
        conv = self.get_or_create_conversation(session_id)

        # 检测是否为追问（之前已有对话轮次）
        prev_messages = [m for m in conv.messages if m.role != "system"]
        is_follow_up = len(prev_messages) > 0

        # 构建对话历史摘要，帮助模型理解上下文
        history_summary = ""
        if is_follow_up:
            recent = prev_messages[-6:]  # 最近3轮
            summary_parts = []
            for m in recent:
                role_label = "用户" if m.role == "user" else "助手"
                # 截取前500字符避免过长，同时提取用户原始问题（去掉RAG附加内容）
                content = m.content
                if m.role == "user" and "## 用户问题" in content:
                    # 从enhanced_query中提取原始问题
                    for line in content.split("\n"):
                        if line.strip() and not line.startswith("##") and not line.startswith("- ") and not line.startswith("**") and not line.startswith("---") and not line.startswith("请基于"):
                            content = line.strip()
                            break
                summary_parts.append(f"【{role_label}】{content[:500]}")
            history_summary = "\n".join(summary_parts)

        # 根据是否有历史上下文，调整指令
        if is_follow_up:
            instruction = f"""## 对话历史（之前的讨论内容）
{history_summary}

---

请综合【对话历史】和【检索到的条款数据】来回答用户的当前问题。重要规则：
1. 如果用户在追问之前讨论过的内容，请结合对话历史理解上下文
2. 引用条款时使用溯源标注格式：`[产品名称>章节名称>条款标题]`
3. 如果当前检索结果不足但对话历史中有相关信息，请结合历史上下文回答
4. 使用Markdown格式回答
5. 在回答末尾添加「## 📎 引用来源」部分列出所有引用的条款来源"""
        else:
            instruction = """请基于以上检索到的条款数据回答用户问题。要求：
1. 引用条款时使用溯源标注格式：`[产品名称>章节名称>条款标题]`
2. 如果检索结果不足以回答问题，请诚实说明
3. 使用Markdown格式回答
4. 在回答末尾添加「## 📎 引用来源」部分列出所有引用的条款来源"""

        enhanced_query = f"""## 用户问题
{query}

## 检索到的相关条款数据（通过向量语义检索 + Reranker精排）
{context['context']}

## 可用产品列表
{chr(10).join(f'- {p}' for p in context['available_products'])}

---

{instruction}"""

        conv.add_message("user", enhanced_query)
        return conv.to_list()

    async def chat_stream(
        self, query: str, session_id: str = "default", mode: str = "standard"
    ) -> AsyncGenerator[str, None]:
        """流式对话 - 支持通用聊天 + RAG保险问答"""

        is_insurance = self._is_insurance_related(query)

        if not is_insurance:
            # 通用对话模式
            conv = self.get_or_create_conversation(session_id)
            conv.add_message("user", query)
            messages = conv.to_list()

            full_response = ""
            try:
                async for chunk in self._call_llm_stream(messages):
                    full_response += chunk
                    yield f"data: {json.dumps({'type': 'content', 'content': chunk}, ensure_ascii=False)}\n\n"
                conv.add_message("assistant", full_response)
            except Exception as e:
                error_msg = f"抱歉，服务暂时不可用：{str(e)}"
                yield f"data: {json.dumps({'type': 'error', 'content': error_msg}, ensure_ascii=False)}\n\n"

            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
            return

        # RAG模式：向量检索 + LLM
        context = await self._retrieve_context_async(query)

        if context["sources"]:
            yield f"data: {json.dumps({'type': 'sources', 'sources': context['sources'][:20], 'matched_count': context['matched_count']}, ensure_ascii=False)}\n\n"

        messages = self._build_messages(session_id, query, context)

        full_response = ""
        try:
            async for chunk in self._call_llm_stream(messages):
                full_response += chunk
                yield f"data: {json.dumps({'type': 'content', 'content': chunk}, ensure_ascii=False)}\n\n"

            conv = self.get_or_create_conversation(session_id)
            conv.add_message("assistant", full_response)

        except Exception as e:
            error_msg = f"LLM服务调用失败：{str(e)}\n\n请检查API配置是否正确。"
            yield f"data: {json.dumps({'type': 'error', 'content': error_msg}, ensure_ascii=False)}\n\n"

        yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"

    async def _get_http_client(self) -> httpx.AsyncClient:
        """获取或创建复用的 httpx 异步客户端"""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=120.0)
        return self._http_client

    def _is_anthropic(self) -> bool:
        """判断是否使用 Anthropic 格式 API"""
        return LLM_API_TYPE == "anthropic"

    def _convert_messages_anthropic(self, messages: List[Dict]) -> tuple:
        """
        将 OpenAI 格式消息转换为 Anthropic 格式。
        Anthropic: system 消息单独提取，messages 只包含 user/assistant。
        返回 (system_prompt, messages)
        """
        system_parts = []
        converted = []
        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            if role == "system":
                system_parts.append(content)
            elif role == "user":
                converted.append({"role": "user", "content": content})
            elif role == "assistant":
                converted.append({"role": "assistant", "content": content})
        system_text = "\n\n".join(system_parts) if system_parts else None
        return system_text, converted

    async def _call_llm_stream(self, messages: List[Dict]) -> AsyncGenerator[str, None]:
        """调用LLM API - 流式输出（复用连接池，支持 OpenAI / Anthropic 格式）"""
        if self._is_anthropic():
            async for chunk in self._call_anthropic_stream(messages):
                yield chunk
            return

        # OpenAI 格式
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "temperature": 0.7,
            "max_tokens": 4096,
        }

        client = await self._get_http_client()
        async with client.stream("POST", url, json=payload, headers=headers) as response:
            if response.status_code != 200:
                error_body = await response.aread()
                raise Exception(f"API返回错误 {response.status_code}: {error_body.decode()}")

            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    choices = data.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                except json.JSONDecodeError:
                    continue

    async def _call_anthropic_stream(self, messages: List[Dict]) -> AsyncGenerator[str, None]:
        """调用 Anthropic Messages API - 流式输出"""
        url = f"{self.base_url}/v1/messages"
        system_text, converted = self._convert_messages_anthropic(messages)

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model,
            "messages": converted,
            "max_tokens": 4096,
            "temperature": 0.7,
            "stream": True,
        }
        if system_text:
            payload["system"] = system_text

        client = await self._get_http_client()
        async with client.stream("POST", url, json=payload, headers=headers) as response:
            if response.status_code != 200:
                error_body = await response.aread()
                raise Exception(f"Anthropic API返回错误 {response.status_code}: {error_body.decode()}")

            current_event = None
            async for line in response.aiter_lines():
                line = line.strip()
                if not line:
                    current_event = None
                    continue

                if line.startswith("event: "):
                    current_event = line[7:].strip()
                    continue

                if line.startswith("data: "):
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        # Anthropic 流式格式
                        if data.get("type") == "content_block_delta":
                            delta = data.get("delta", {})
                            if delta.get("type") == "text_delta":
                                text = delta.get("text", "")
                                if text:
                                    yield text
                        elif data.get("type") == "message_stop":
                            break
                    except json.JSONDecodeError:
                        continue

    async def chat_static(self, query: str, session_id: str = "default") -> Dict:
        """非流式对话"""
        context = await self._retrieve_context_async(query)
        messages = self._build_messages(session_id, query, context)

        if self._is_anthropic():
            url = f"{self.base_url}/v1/messages"
            system_text, converted = self._convert_messages_anthropic(messages)
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"
            }
            payload = {
                "model": self.model,
                "messages": converted,
                "max_tokens": 4096,
                "temperature": 0.7,
            }
            if system_text:
                payload["system"] = system_text
        else:
            url = f"{self.base_url}/chat/completions"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 4096,
            }

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code != 200:
                raise Exception(f"API返回错误 {response.status_code}: {response.text}")

            data = response.json()
            if self._is_anthropic():
                content = data["content"][0]["text"]
            else:
                content = data["choices"][0]["message"]["content"]

            conv = self.get_or_create_conversation(session_id)
            conv.add_message("assistant", content)

            return {
                "content": content,
                "sources": context["sources"][:20],
                "matched_products": context["matched_products"],
                "matched_count": context["matched_count"]
            }

    async def edit_stream(
        self, selected_text: str, instruction: str, context: str = ""
    ) -> AsyncGenerator[str, None]:
        """AI辅助编辑 - 流式输出改写结果"""

        instruction_map = {
            "polish": "请对以下文本进行润色优化，使其更加通顺、专业",
            "simplify": "请简化以下文本，用更简洁的语言表达，删除冗余",
            "expand": "请扩展以下文本，补充更多细节和说明",
            "professional": "请用更专业、规范的保险行业用语改写以下文本",
        }

        user_instruction = instruction_map.get(instruction, instruction)

        user_message = f"## 编辑指令\n{user_instruction}\n\n## 待编辑文本\n{selected_text}"

        if context:
            user_message += f"\n\n## 上下文（仅供参考）\n{context}"

        messages = [
            {"role": "system", "content": EDIT_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]

        try:
            async for chunk in self._call_llm_stream(messages):
                yield f"data: {json.dumps({'type': 'content', 'content': chunk}, ensure_ascii=False)}\n\n"
        except Exception as e:
            error_msg = f"编辑服务调用失败：{str(e)}"
            yield f"data: {json.dumps({'type': 'error', 'content': error_msg}, ensure_ascii=False)}\n\n"

        yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"

    def clear_conversation(self, session_id: str):
        """清除对话历史"""
        if session_id in self.conversations:
            del self.conversations[session_id]
