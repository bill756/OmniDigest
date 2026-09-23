import re
from typing import List, Dict, Any, Set
import jieba
import jieba.analyse

STOP_WORDS: Set[str] = {
    "如何", "看待", "怎么", "怎样", "为什么", "究竟", "发生", "一起", "最新",
    "引发", "关于", "今日", "近日", "情况", "分析", "讨论", "评价", "大家",
    "个人", "觉得", "认为", "可能", "应该", "难道", "其实", "到底"
}

SUBJECTIVE_PATTERNS = [
    r"^(我|笔者|个人)(认为|觉得|看来|以为|深感|坚信)",
    r"(太可怕了|令人心碎|深表同情|深感痛心|让人唏嘘|难以置信|令人深思)",
    r"^(如何看待|怎么评价|你怎么看|大家怎么看|为什么会这样)",
    r"(欢迎讨论|点赞关注|转发分享|你怎么认为)",
]


def is_valid_topic_entity(tag: str) -> bool:
    """判定是否为有效的特定事件主题实体（排除纯数字、时态及泛化通用词）"""
    t = (tag or "").strip()
    if not t or len(t) < 2:
        return False
    if t.isdigit() or re.match(r"^[0-9一二三四五六七八九十百千万]+$", t):
        return False
    generic = {"事件", "文章", "网友", "通报", "情况", "消息", "内容", "部门", "警方", "官方", "目前", "现场", "发生", "一起"}
    return t not in generic and t not in STOP_WORDS


def extract_topic_entities(title: str, content: str, top_k: int = 6) -> List[str]:
    """使用 TextRank + 权重增强从文章标题与首部内容中提炼核心事件实体

    返回体现文章核心主题的高信息密度实词
    """
    clean_title = re.sub(r"[《》【】\(\)（）\?？!！]", " ", title or "").strip()
    # 标题赋予 3 倍权重以确保核心事件主体占主导地位
    weighted_text = (clean_title + "\n") * 3 + (content[:1500] if content else "")
    if not weighted_text.strip():
        return []

    tags = jieba.analyse.extract_tags(weighted_text, topK=top_k * 4)
    filtered = [t.strip() for t in tags if is_valid_topic_entity(t)]
    return filtered[:top_k]


def split_sentences(text: str) -> List[str]:
    """将中文长文本规范切分为句子列表（保留引号完整性）"""
    if not text:
        return []

    # 规范化换行与多余空格
    normalized = re.sub(r"\r\n|\r", "\n", text)
    # 按中文标点断句
    raw_sentences = re.split(r"([。！？!?；;\n]+)", normalized)

    sentences: List[str] = []
    current = ""

    for part in raw_sentences:
        current += part
        if re.search(r"[。！？!?；;\n]+$", current):
            stripped = current.strip()
            if stripped and len(stripped) >= 3:
                sentences.append(stripped)
            current = ""

    if current.strip():
        sentences.append(current.strip())

    return sentences


def is_pure_subjective(sentence: str) -> bool:
    """快速判断句子是否为纯主观抒情、反问或无事实命题的评论语句"""
    s = sentence.strip()
    if not s or len(s) < 4:
        return True

    # 纯疑问句/反问句
    if s.endswith("？") or s.endswith("?"):
        if any(w in s for w in ["如何看待", "怎么看", "为什么", "凭什么", "到底该"]):
            return True

    for pattern in SUBJECTIVE_PATTERNS:
        if re.search(pattern, s):
            return True

    return False


def clean_event_topic(title: str, topic_entities: List[str]) -> str:
    """从文章标题或主题实体中提炼干净的核心事件名称（去除设问、标点与修饰词）"""
    t = re.sub(r"^(如何看待|怎么看待|怎么评价|如何评价|为什么|关于|大家怎么看|讨论|最新|突发)", "", title or "").strip()
    t = re.sub(r"[\?？!！《》【】\(\)（）]", "", t).strip()
    if t and len(t) >= 4:
        return t
    valid_topics = [w for w in topic_entities if is_valid_topic_entity(w)]
    if valid_topics:
        return "".join(valid_topics[:3])
    return "涉事事件"


def ground_claim_with_antecedent(claim_text: str, title: str, topic_entities: List[str]) -> str:
    """消解指示代词与承前省略，自动补全断言的前因背景（如将“该35岁男性作案后...”自然补全前因）"""
    txt = (claim_text or "").strip()
    if not txt:
        return ""

    event_name = clean_event_topic(title, topic_entities)

    # 优先消解显式弱指示代词（以“该...”开头即使带有局部词仍属于无前因半截句）
    # 模式 1：以“该[数字岁]?[男子/男性/嫌疑人/嫌犯/人员...]”开头
    weak_m = re.match(r"^该([0-9]+岁)?(男性|男子|嫌疑人|嫌犯|当事人|人员|涉案人员|涉事人员|女子|女性|司机|死者|伤者|受害者)(.*)", txt)
    if weak_m:
        age_part = weak_m.group(1) or ""
        role_part = weak_m.group(2)
        rest = weak_m.group(3)
        return f"在{event_name}中，涉案{age_part}{role_part}{rest}"

    # 模式 2：以“作案后/案发后/事发后/行凶后”开头
    if re.match(r"^(作案后|案发后|事发后|行凶后)", txt):
        return f"在{event_name}中，嫌疑人{txt}"

    # 模式 3：以“该案/该事件/该起案件”开头
    if re.match(r"^(该案|该事件|该起案件)", txt):
        rest = re.sub(r"^(该案|该事件|该起案件)", "", txt).lstrip("，, ")
        return f"在{event_name}中，{rest}"

    # 检查是否已经显式包含核心事件关键词
    valid_tokens = [w for w in topic_entities if is_valid_topic_entity(w)]
    has_event = any(w in txt for w in valid_tokens) if valid_tokens else (event_name in txt)

    if not has_event:
        return f"在{event_name}中，{txt}"

    return txt

