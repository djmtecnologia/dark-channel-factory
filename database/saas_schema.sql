-- ============================================================
-- YouTube Dark Factory v2.1 — SaaS PostgreSQL Schema (Neon)
-- ============================================================

-- Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 1. Tenants (Empresas/Agências)
CREATE TABLE IF NOT EXISTS tenants (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    plan_type TEXT DEFAULT 'free',      -- 'free', 'pro', 'enterprise'
    credits_balance INTEGER DEFAULT 0,
    hotmart_affiliate_id TEXT,
    clickbank_nickname TEXT,
    amazon_tag TEXT,
    auto_approve_content BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 2. Canais (YouTube Channels vinculados)
CREATE TABLE IF NOT EXISTS channels (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    youtube_channel_id TEXT,
    name TEXT NOT NULL,
    niche TEXT NOT NULL,
    language TEXT NOT NULL,             -- BCP-47
    market_region TEXT NOT NULL,        -- 'US', 'ES', 'BR', etc
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 3. Cache de Produtos de Afiliados
CREATE TABLE IF NOT EXISTS affiliate_products_cache (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,            -- 'HOTMART', 'CLICKBANK', 'AMAZON'
    product_name TEXT NOT NULL,
    niche TEXT NOT NULL,
    hotlink_base TEXT NOT NULL,        -- URL base de afiliado
    gravity REAL DEFAULT 0,            -- Relevância (ClickBank)
    commission_pct REAL DEFAULT 0,
    language TEXT NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, platform, hotlink_base)
);

-- 4. Video Jobs (A Fila de Inteligência)
CREATE TABLE IF NOT EXISTS video_jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    channel_id UUID REFERENCES channels(id),
    status TEXT NOT NULL DEFAULT 'PENDING', -- 'PENDING', 'SCOUTED', 'DRAFTED', 'APPROVED', 'RENDERED', 'PUBLISHED', 'FAILED'
    
    -- Dados da IA
    topic TEXT,
    angle TEXT,
    target_keywords TEXT[],
    viral_score REAL,
    
    -- Conteúdo (JSONB para flexibilidade)
    script_data JSONB, 
    thumbnail_data JSONB,
    
    -- Monetização injetada
    injected_affiliate_url TEXT,
    
    -- Local Worker tracking
    worker_id TEXT,
    error_message TEXT,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 5. Performance & ROI Logs (Analyst Agent)
CREATE TABLE IF NOT EXISTS performance_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    video_id UUID REFERENCES video_jobs(id),
    tenant_id UUID REFERENCES tenants(id),
    youtube_video_id TEXT,
    
    -- Métricas YouTube
    views_count INTEGER DEFAULT 0,
    ctr_percent REAL DEFAULT 0,
    retention_percent REAL DEFAULT 0,
    
    -- Financeiro (Fechamento do Loop)
    clicks_count INTEGER DEFAULT 0,
    sales_count INTEGER DEFAULT 0,
    affiliate_revenue DECIMAL(12,2) DEFAULT 0,
    
    last_updated TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 6. Transações de Crédito (Billing)
CREATE TABLE IF NOT EXISTS credit_transactions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES tenants(id),
    amount INTEGER NOT NULL,          -- + para recarga, - para uso
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Índices para performance multi-tenant
CREATE INDEX idx_jobs_tenant ON video_jobs(tenant_id);
CREATE INDEX idx_products_niche ON affiliate_products_cache(niche, language);
CREATE INDEX idx_performance_video ON performance_logs(video_id);
