import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Text, Boolean, Integer, DateTime, ForeignKey, Float
)
from sqlalchemy.dialects.postgresql import JSONB
from app.db.session import Base

class Tenant(Base):
    __tablename__ = "tenants"

    tenant_id = Column(String(64), primary_key=True, index=True)
    business_name = Column(String(255), nullable=False)
    status = Column(String(32), default="active")
    created_at = Column(DateTime, default=datetime.utcnow)

class TenantPhoneNumber(Base):
    __tablename__ = "tenant_phone_numbers"

    number = Column(String(32), primary_key=True, index=True)  # E.164 e.g. +12296336435
    tenant_id = Column(String(64), ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    vendor = Column(String(32), default="twilio")
    is_active = Column(Boolean, default=True)

class TenantConfig(Base):
    __tablename__ = "tenant_config"

    config_id = Column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex)
    tenant_id = Column(String(64), ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    version = Column(Integer, default=1)
    persona = Column(Text, nullable=False)
    business_rules = Column(JSONB, default=dict)
    escalation_number = Column(String(32), nullable=False)
    guardrail_topics = Column(JSONB, default=list)
    is_active = Column(Boolean, default=True)

class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    chunk_id = Column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex)
    tenant_id = Column(String(64), ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    source_doc = Column(String(255), nullable=False)
    chunk_text = Column(Text, nullable=False)
    embedding = Column(JSONB, nullable=True)  # Stored as float array in JSONB for universal compatibility

class Call(Base):
    __tablename__ = "calls"

    call_id = Column(String(64), primary_key=True, index=True)
    tenant_id = Column(String(64), ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    caller_number = Column(String(32), nullable=True)
    start_time = Column(DateTime, default=datetime.utcnow)
    end_time = Column(DateTime, nullable=True)
    duration = Column(Float, default=0.0)
    outcome_tag = Column(String(64), default="completed")  # completed, transferred, escalation_failed_lead_captured, dropped
    transferred = Column(Boolean, default=False)

class CallTranscript(Base):
    __tablename__ = "call_transcripts"

    transcript_id = Column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex)
    call_id = Column(String(64), ForeignKey("calls.call_id"), nullable=False, index=True)
    tenant_id = Column(String(64), ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    turn_index = Column(Integer, nullable=False)
    speaker = Column(String(16), nullable=False)  # agent | caller
    text = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)

class Lead(Base):
    __tablename__ = "leads"

    lead_id = Column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex)
    tenant_id = Column(String(64), ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    call_id = Column(String(64), ForeignKey("calls.call_id"), nullable=True, index=True)
    name = Column(String(128), nullable=True)
    phone = Column(String(32), nullable=False)
    reason = Column(Text, nullable=True)
    captured_at = Column(DateTime, default=datetime.utcnow)
