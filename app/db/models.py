from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    Boolean,
    ForeignKey,
    JSON,
    Float,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def utc_now():
    return datetime.now(timezone.utc)


class Article(Base):
    """抓取的文章快照与元数据"""
    __tablename__ = "articles"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    url = Column(String(1024), unique=True, index=True, nullable=False)
    url_hash = Column(String(64), index=True, nullable=False)
    title = Column(String(512), nullable=False)
    author = Column(String(128), nullable=True)
    publish_date = Column(String(64), nullable=True)
    platform = Column(String(32), default="web")
    clean_content = Column(Text, nullable=False)
    token_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    # 关联生成的精读报告记录
    digest_records = relationship("DigestRecord", back_populates="article", cascade="all, delete-orphan")


class DigestRecord(Base):
    """精读报告、事实核查与向量化存储"""
    __tablename__ = "digest_records"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    article_id = Column(Integer, ForeignKey("articles.id", ondelete="CASCADE"), nullable=False)
    summary = Column(Text, nullable=False)
    mindmap = Column(Text, nullable=False)
    claims_json = Column(JSON, nullable=True)  # 存储提取出的断言及核查详细信息
    final_report = Column(Text, nullable=False)
    embedding = Column(Text, nullable=True)  # JSON 序列化的向量 float 数组，支持 SQLite 与兼容 PG
    created_at = Column(DateTime, default=utc_now)

    article = relationship("Article", back_populates="digest_records")
