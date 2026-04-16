"""
产品条款检索与溯源模块
- 从JSON产品库加载备案产品数据
- 提供关键词检索、章节匹配、组合查询功能
- 所有检索结果100%基于原文，自动附加溯源标注
"""

import json
import csv
import os
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class SectionResult:
    """单条条款检索结果"""
    product_id: str
    product_name: str
    filing_no: str
    chapter_name: str
    section_id: str
    title: str
    content: str

    def to_dict(self) -> dict:
        return {
            "product_id": self.product_id,
            "product_name": self.product_name,
            "filing_no": self.filing_no,
            "chapter_name": self.chapter_name,
            "section_id": self.section_id,
            "title": self.title,
            "content": self.content,
            "source_tag": f"[{self.product_name}>{self.chapter_name}>{self.title}]"
        }


@dataclass
class SearchResult:
    """检索结果集"""
    query: str
    results: List[SectionResult] = field(default_factory=list)
    matched_products: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "matched_products": self.matched_products,
            "total_sections": len(self.results),
            "results": [r.to_dict() for r in self.results]
        }


# 标准章节顺序
STANDARD_CHAPTER_ORDER = [
    "总则", "保险责任", "责任免除", "保险金额", "保险期间",
    "保险金申请", "释义"
]

# 章节名称映射（处理不同产品的章节命名差异）
CHAPTER_ALIASES = {
    "保险金额与给付比例": "保险金额",
    "保险期间与续保": "保险期间",
}


class ProductStore:
    """产品条款库 - 核心检索引擎"""

    def __init__(self, data_path: str = None):
        if data_path is None:
            data_path = os.path.join(os.path.dirname(__file__), "data", "products.json")
        self.data_path = data_path
        self.products: List[Dict] = []
        self._load_products()

    def _load_products(self):
        """加载JSON产品库（主产品库 + output/目录 + CSV精算数据）"""
        # 1. 加载主产品库（文件不存在则跳过，仅用 output/ 数据）
        if os.path.exists(self.data_path):
            with open(self.data_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.products = data.get("products", [])
            print(f"[ProductStore] 主产品库已加载 {len(self.products)} 个产品")
        else:
            print(f"[ProductStore] products.json 不存在，跳过主产品库加载")

        # 2. 加载 output/ 目录下的真实产品条款文件
        output_dir = os.path.join(os.path.dirname(self.data_path), "output")
        if os.path.isdir(output_dir):
            output_count = 0
            for fname in os.listdir(output_dir):
                if not fname.endswith(".json") or fname.startswith("."):
                    continue
                fpath = os.path.join(output_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        product_data = json.load(f)
                    product = self._convert_output_product(product_data, output_count)
                    if product:
                        self.products.append(product)
                        output_count += 1
                except Exception as e:
                    print(f"[ProductStore] 跳过文件 {fname}: {e}")
            print(f"[ProductStore] output/ 目录已加载 {output_count} 个真实产品条款")

        # 3. 加载 CSV 精算数据
        csv_dir = os.path.dirname(self.data_path)
        for csv_fname in os.listdir(csv_dir):
            if csv_fname.endswith(".csv") and not csv_fname.startswith("."):
                csv_path = os.path.join(csv_dir, csv_fname)
                try:
                    csv_count = self._load_csv_products(csv_path)
                    print(f"[ProductStore] CSV文件 {csv_fname} 已加载 {csv_count} 个产品")
                except Exception as e:
                    print(f"[ProductStore] 加载CSV {csv_fname} 失败: {e}")

    def _load_csv_products(self, csv_path: str) -> int:
        """加载CSV精算数据文件中的产品"""
        count = 0
        existing_names = {p["product_name"] for p in self.products}
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row_idx, row in enumerate(reader):
                product_name = row.get("act_product_name", "").strip().strip('"')
                if not product_name or product_name in existing_names:
                    continue
                kind_text = row.get("kind", "").strip().strip('"')
                period_text = row.get("insurance_period", "").strip().strip('"')
                if not kind_text:
                    continue
                insurance_cat = row.get("insurance_category", "").strip().strip('"')
                filing_no = row.get("filing_no", "").strip().strip('"')
                clause_type = row.get("clause_type", "").strip().strip('"')
                product_code = row.get("act_product_code", "").strip().strip('"')
                category, sub_category = self._infer_category(product_name)
                if insurance_cat:
                    parts = insurance_cat.split("-", 1)
                    category = parts[0] if parts[0] else category
                    sub_category = parts[1] if len(parts) > 1 else sub_category
                chapters = []
                sec_idx = 0
                sections = []
                paragraphs = [p.strip() for p in kind_text.split("\n") if p.strip()]
                for para in paragraphs:
                    sec_id = f"CSV-{row_idx:04d}-{sec_idx}"
                    title = para[:25].replace("\n", " ") + "..." if len(para) > 25 else para
                    sections.append({
                        "section_id": sec_id,
                        "title": title,
                        "content": para
                    })
                    sec_idx += 1
                if sections:
                    chapters.append({"chapter_name": "保险责任", "sections": sections})
                if period_text:
                    chapters.append({
                        "chapter_name": "保险期间",
                        "sections": [{
                            "section_id": f"CSV-{row_idx:04d}-{sec_idx}",
                            "title": "保险期间",
                            "content": period_text
                        }]
                    })
                if not chapters:
                    continue
                product = {
                    "id": f"CSV-{row_idx:04d}",
                    "product_name": product_name,
                    "category": category,
                    "sub_category": sub_category,
                    "version": "",
                    "filing_no": filing_no,
                    "chapters": chapters
                }
                self.products.append(product)
                existing_names.add(product_name)
                count += 1
        return count

    def _convert_output_product(self, data: Dict, index: int) -> Optional[Dict]:
        """将 output/ 目录下的产品JSON转换为标准产品格式"""
        if not isinstance(data, dict) or "保险产品名称" not in data:
            return None
        
        product_name = data.get("保险产品名称", f"未知产品_{index}")
        chapter_list = data.get("章节列表", [])
        if not chapter_list:
            return None

        # 推断产品类别
        category, sub_category = self._infer_category(product_name)

        chapters = []
        section_idx = 0
        for ch in chapter_list:
            chapter_name = ch.get("章节名称", "其他")
            sections = []
            for clause_content in ch.get("条款", []):
                if not clause_content or not clause_content.strip():
                    continue
                section_id = f"OUT-{index:03d}-{section_idx}"
                # 提取标题：取内容的前15个字作为标题
                title = clause_content.strip()[:20].replace("\n", " ") + "..."
                sections.append({
                    "section_id": section_id,
                    "title": title,
                    "content": clause_content.strip()
                })
                section_idx += 1
            if sections:
                chapters.append({
                    "chapter_name": chapter_name,
                    "sections": sections
                })

        if not chapters:
            return None

        return {
            "id": f"OUT-{index:03d}",
            "product_name": product_name,
            "category": category,
            "sub_category": sub_category,
            "version": "",
            "filing_no": "",
            "chapters": chapters
        }

    def _infer_category(self, name: str) -> tuple:
        """根据产品名称推断类别"""
        if "意外" in name:
            return "意外险", "意外伤害保险"
        elif "医疗" in name:
            return "健康险", "医疗保险"
        elif "重疾" in name or "重大疾病" in name:
            return "健康险", "重大疾病保险"
        elif "寿险" in name or "身故" in name:
            return "人寿险", "人寿保险"
        elif "旅行" in name:
            return "意外险", "旅行意外保险"
        elif "工程" in name or "施工" in name:
            return "意外险", "工程保险"
        else:
            return "其他", "其他保险"

    def list_products(self) -> List[Dict]:
        """列出所有可用产品"""
        return [
            {
                "id": p["id"],
                "product_name": p["product_name"],
                "category": p["category"],
                "sub_category": p["sub_category"],
                "version": p["version"],
                "filing_no": p["filing_no"]
            }
            for p in self.products
        ]

    def get_product(self, product_id: str) -> Optional[Dict]:
        """根据ID获取产品"""
        for p in self.products:
            if p["id"] == product_id:
                return p
        return None

    def get_product_by_name(self, name: str) -> Optional[Dict]:
        """根据名称获取产品（支持模糊匹配）"""
        for p in self.products:
            if name in p["product_name"] or p["product_name"] in name:
                return p
        return None

    def search_sections(self, query: str, product_ids: List[str] = None) -> SearchResult:
        """
        全文检索条款
        - 支持关键词搜索（险种、保障范围、责任类型等）
        - 支持限定产品范围
        """
        result = SearchResult(query=query)
        query_lower = query.lower()

        # 关键词列表（空格分割）
        keywords = [kw.strip() for kw in query_lower.split() if kw.strip()]

        for product in self.products:
            # 如果指定了产品范围，则过滤
            if product_ids and product["id"] not in product_ids:
                continue

            product_matched = False

            for chapter in product["chapters"]:
                for section in chapter["sections"]:
                    # 计算匹配度
                    score = self._calculate_match_score(
                        keywords, section, chapter["chapter_name"], product
                    )
                    if score > 0:
                        sr = SectionResult(
                            product_id=product["id"],
                            product_name=product["product_name"],
                            filing_no=product["filing_no"],
                            chapter_name=chapter["chapter_name"],
                            section_id=section["section_id"],
                            title=section["title"],
                            content=section["content"]
                        )
                        result.results.append(sr)
                        product_matched = True

            if product_matched:
                result.matched_products.append(product["product_name"])

        # 按标准章节顺序排序
        result.results.sort(key=lambda x: self._chapter_sort_key(x.chapter_name))
        return result

    def search_by_chapter(self, chapter_name: str, product_ids: List[str] = None) -> SearchResult:
        """按章节名称检索"""
        result = SearchResult(query=f"章节:{chapter_name}")

        for product in self.products:
            if product_ids and product["id"] not in product_ids:
                continue

            product_matched = False

            for chapter in product["chapters"]:
                if chapter_name in chapter["chapter_name"] or chapter["chapter_name"] in chapter_name:
                    for section in chapter["sections"]:
                        sr = SectionResult(
                            product_id=product["id"],
                            product_name=product["product_name"],
                            filing_no=product["filing_no"],
                            chapter_name=chapter["chapter_name"],
                            section_id=section["section_id"],
                            title=section["title"],
                            content=section["content"]
                        )
                        result.results.append(sr)
                        product_matched = True

            if product_matched:
                result.matched_products.append(product["product_name"])

        return result

    def search_by_product(self, product_name_or_id: str) -> SearchResult:
        """获取某个产品的全部条款"""
        result = SearchResult(query=f"产品:{product_name_or_id}")

        product = self.get_product(product_name_or_id)
        if not product:
            product = self.get_product_by_name(product_name_or_id)

        if not product:
            return result

        result.matched_products.append(product["product_name"])

        for chapter in product["chapters"]:
            for section in chapter["sections"]:
                sr = SectionResult(
                    product_id=product["id"],
                    product_name=product["product_name"],
                    filing_no=product["filing_no"],
                    chapter_name=chapter["chapter_name"],
                    section_id=section["section_id"],
                    title=section["title"],
                    content=section["content"]
                )
                result.results.append(sr)

        return result

    def generate_combined_clauses(self, product_names: List[str]) -> Dict:
        """
        生成组合产品条款
        - 自动统一章节结构
- 去重、避免冲突
        - 按标准顺序排列
        """
        combined = {}
        all_sources = []

        for name in product_names:
            product = self.get_product_by_name(name)
            if not product:
                continue

            all_sources.append({
                "product_name": product["product_name"],
                "filing_no": product["filing_no"],
                "id": product["id"]
            })

            for chapter in product["chapters"]:
                # 标准化章节名称
                std_chapter = CHAPTER_ALIASES.get(
                    chapter["chapter_name"], chapter["chapter_name"]
                )

                if std_chapter not in combined:
                    combined[std_chapter] = []

                for section in chapter["sections"]:
                    combined[std_chapter].append({
                        "product_name": product["product_name"],
                        "filing_no": product["filing_no"],
                        "chapter_name": chapter["chapter_name"],
                        "section_id": section["section_id"],
                        "title": section["title"],
                        "content": section["content"],
                        "source_tag": f"[{product['product_name']}>{chapter['chapter_name']}>{section['title']}]"
                    })

        # 按标准顺序排列
        ordered = {}
        for ch in STANDARD_CHAPTER_ORDER:
            if ch in combined:
                ordered[ch] = combined[ch]

        # 添加非标准章节
        for ch in combined:
            if ch not in ordered:
                ordered[ch] = combined[ch]

        return {
            "sources": all_sources,
            "chapters": ordered
        }

    def _calculate_match_score(self, keywords: List[str], section: Dict,
                                chapter_name: str, product: Dict) -> int:
        """计算关键词匹配分数"""
        score = 0
        searchable_text = (
            section["title"] + " " +
            section["content"] + " " +
            chapter_name + " " +
            product["product_name"] + " " +
            product["category"] + " " +
            product.get("sub_category", "")
        ).lower()

        for kw in keywords:
            if kw in searchable_text:
                # 标题匹配权重更高
                if kw in section["title"].lower():
                    score += 3
                elif kw in chapter_name.lower():
                    score += 2
                elif kw in section["content"].lower():
                    score += 1

        return score

    def _chapter_sort_key(self, chapter_name: str) -> int:
        """章节排序键"""
        std_name = CHAPTER_ALIASES.get(chapter_name, chapter_name)
        if std_name in STANDARD_CHAPTER_ORDER:
            return STANDARD_CHAPTER_ORDER.index(std_name)
        return 999


# 全局单例
_store_instance: Optional[ProductStore] = None


def get_product_store() -> ProductStore:
    """获取产品库单例"""
    global _store_instance
    if _store_instance is None:
        _store_instance = ProductStore()
    return _store_instance
