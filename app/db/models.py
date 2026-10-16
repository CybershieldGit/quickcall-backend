import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Text, Boolean, Integer, DateTime, ForeignKey, Float
)
from sqlalchemy.dialects.postgresql import JSONB
from app.db.session import Base

class Reseller(Base):
    __tablename__ = "resellers"

    reseller_id = Column(String(64), primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    company_name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=True)
    api_key = Column(String(128), nullable=True, unique=True, index=True)
    status = Column(String(32), default="active")  # active | suspended
    wholesale_rate_per_min = Column(Float, default=3.00)  # INR / min cost charged by Owner
    wallet_balance = Column(Float, default=1000.0)  # INR prepaid credits
    branding = Column(JSONB, default=dict)  # {portal_title, logo_url, support_email}
    created_at = Column(DateTime, default=datetime.utcnow)

class AdminUser(Base):
    __tablename__ = "admin_users"

    admin_id = Column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex)
    email = Column(String(255), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)
    name = Column(String(128), default="Platform Owner")
    role = Column(String(32), default="superadmin")
    created_at = Column(DateTime, default=datetime.utcnow)

class Tenant(Base):
    __tablename__ = "tenants"

    tenant_id = Column(String(64), primary_key=True, index=True)
    business_name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=True)
    password_hash = Column(String(255), nullable=True)
    api_key = Column(String(128), nullable=True, unique=True, index=True)
    status = Column(String(32), default="active")
    plan = Column(String(64), default="starter")  # starter, growth, enterprise
    telephony_mode = Column(String(32), default="master_pool")  # master_pool | byoc
    reseller_id = Column(String(64), ForeignKey("resellers.reseller_id"), nullable=True, index=True)
    rate_per_min = Column(Float, default=5.00)  # Retail rate in INR charged to this tenant
    wallet_balance = Column(Float, default=500.0)  # Prepaid talktime balance in INR
    exotel_account_sid = Column(String(64), nullable=True)
    exotel_api_key = Column(String(128), nullable=True)
    exotel_api_token = Column(String(128), nullable=True)
    exotel_caller_id = Column(String(32), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class PhoneNumberPool(Base):
    __tablename__ = "phone_number_pool"

    number = Column(String(32), primary_key=True, index=True)
    vendor = Column(String(32), default="rpdigital")
    sip_extension = Column(String(32), nullable=True)
    status = Column(String(32), default="available")  # available | assigned | reserved
    assigned_tenant_id = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Agent(Base):
    __tablename__ = "agents"

    agent_id = Column(String(64), primary_key=True, default=lambda: f"ag_{uuid.uuid4().hex[:12]}", index=True)
    tenant_id = Column(String(64), ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    role_title = Column(String(128), default="Admissions Counselor")
    persona = Column(Text, nullable=False)
    greeting = Column(Text, nullable=False)
    escalation_number = Column(String(32), nullable=True)
    voice_id = Column(String(64), default="default")
    language = Column(String(32), default="hi-IN")
    is_default = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class TenantPhoneNumber(Base):
    __tablename__ = "tenant_phone_numbers"

    number = Column(String(32), primary_key=True, index=True)  # E.164 e.g. +917316902643
    tenant_id = Column(String(64), ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    vendor = Column(String(32), default="rpdigital")
    is_active = Column(Boolean, default=True)
    assigned_agent_id = Column(String(64), nullable=True, index=True)

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
    agent_id = Column(String(64), nullable=True, index=True)
    caller_number = Column(String(32), nullable=True)
    start_time = Column(DateTime, default=datetime.utcnow)
    end_time = Column(DateTime, nullable=True)
    duration = Column(Float, default=0.0)
    outcome_tag = Column(String(64), default="completed")  # completed, transferred, escalation_failed_lead_captured, dropped
    transferred = Column(Boolean, default=False)
    # Executive Brief & Summarization fields
    summary = Column(Text, nullable=True)
    primary_concern = Column(String(255), nullable=True)
    caller_sentiment = Column(String(64), nullable=True)
    action_required = Column(Text, nullable=True)
    executive_notes = Column(Text, nullable=True)

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
    status = Column(String(32), default="hot")  # hot, warm, cold, callback, converted
    captured_at = Column(DateTime, default=datetime.utcnow)

class BillingTransaction(Base):
    __tablename__ = "billing_transactions"

    transaction_id = Column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex)
    call_id = Column(String(64), ForeignKey("calls.call_id"), nullable=True, index=True)
    tenant_id = Column(String(64), ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    reseller_id = Column(String(64), ForeignKey("resellers.reseller_id"), nullable=True, index=True)
    duration_seconds = Column(Float, default=0.0)
    billable_minutes = Column(Float, default=0.0)
    rate_per_min = Column(Float, default=0.0)
    wholesale_rate_per_min = Column(Float, default=0.0)
    client_charged = Column(Float, default=0.0)  # INR deducted from tenant
    reseller_cost = Column(Float, default=0.0)   # INR deducted from reseller
    reseller_profit = Column(Float, default=0.0) # INR profit kept by reseller
    owner_net_profit = Column(Float, default=0.0)# INR net platform profit
    notes = Column(String(255), nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
