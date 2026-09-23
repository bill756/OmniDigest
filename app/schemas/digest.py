from typing import List, Optional, Literal
from pydantic import BaseModel, Field, HttpUrl
from datetime import datetime


class DigestRequest(BaseModel):
    url: str = Field(..., description="待解析的文章 URL (支持微信公众号、知乎专栏/回答、以及通用技术博客)")
    force_refresh: bool = Field(default=False, description="是否跳过 Redis 缓存强制重新抓取与分析")
    enable_fact_check: bool = Field(default=True, description="是否开启智能动态事实核验")
    content_override: Optional[str] = Field(default=None, description="若提供此字段，可直接分析传入的纯文本而无需爬取")


class ClaimItem(BaseModel):
    claim_id: str = Field(..., description="断言唯一标识")
    text: str = Field(..., description="提取的事实断言原文片段")
    claim_type: Literal["hard_data", "health_medical", "breaking_news", "scientific_tech", "general_opinion"] = Field(
        default="general_opinion",
        description="断言类型：硬性数据/医疗健康/重大新闻/前沿科技/常规观点"
    )
    need_verify: bool = Field(default=False, description="是否属于存疑或需核查硬事实")
    search_query: Optional[str] = Field(default=None, description="针对该断言生成的针对性检索词")
    confidence_level: Literal["green", "yellow", "red", "outdated", "unverified"] = Field(
        default="unverified",
        description="置信度等级：green(可靠)/yellow(存疑不确定)/red(虚假/夸大严重违背事实)/outdated(历史属实但现已失效)/unverified(无需核验或未核验)"
    )
    temporal_anchor: Optional[str] = Field(default=None, description="断言涉及的时间基准/锚点，如 2023年")
    confidence_score: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="核验置信度量化打分 (0.0~1.0)")
    verification_reason: Optional[str] = Field(default=None, description="核查论证过程与判断依据")
    sources: List[str] = Field(default_factory=list, description="交叉引用的可靠外部来源 URL 或信息来源")
    evidence_quotes: List[str] = Field(default_factory=list, description="直接摘录自证据片段的原文引文（杜绝杜撰证据）")
    independent_sources_count: int = Field(default=0, description="支撑或验证该断言的独立域名信源数量")
    authority_tier: Optional[str] = Field(default=None, description="最高证据信源权威等级 (Tier 1/Tier 2/Tier 3)")



class DigestResponse(BaseModel):
    url: str
    title: str
    author: Optional[str] = None
    publish_date: Optional[str] = None
    source_platform: str = "web"
    cached: bool = False
    token_count: int = 0
    mindmap: str = Field(..., description="Markdown 树状层级思维导图")
    summary: str = Field(..., description="核心精读内容摘要")
    claims: List[ClaimItem] = Field(default_factory=list, description="提取的关键断言及其核查状态")
    final_report: str = Field(..., description="综合精读报告 Markdown")
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class SearchRequest(BaseModel):
    query: str = Field(..., description="历史摘要语义检索关键词或自然语言问题")
    top_k: int = Field(default=5, ge=1, le=20, description="返回最相似的结果数量")


class SearchResultItem(BaseModel):
    id: int
    url: str
    title: str
    source_platform: str
    summary: str
    similarity: float
    match_reason: Optional[str] = Field(default=None, description="命中原因解析（如标题精准命中、全文关键词强相关等）")
    created_at: str


class SearchResponse(BaseModel):
    query: str
    corrected_query: Optional[str] = Field(default=None, description="智能纠错或意图归一化后的标准词")
    notice: Optional[str] = Field(default=None, description="智能纠错或扩展提示信息")
    total: int
    results: List[SearchResultItem]

