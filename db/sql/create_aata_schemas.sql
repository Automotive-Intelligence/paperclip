-- Create schemas
CREATE SCHEMA IF NOT EXISTS dealership;
CREATE SCHEMA IF NOT EXISTS consumer;
CREATE SCHEMA IF NOT EXISTS aata;

-- ============================================================
-- DEALERSHIP SCHEMA
-- ============================================================

-- Dealership inventory
CREATE TABLE IF NOT EXISTS dealership.inventory (
    id SERIAL PRIMARY KEY,
    vin VARCHAR(17) UNIQUE NOT NULL,
    year INT NOT NULL,
    make VARCHAR(50) NOT NULL,
    model VARCHAR(50) NOT NULL,
    trim VARCHAR(50),
    color VARCHAR(30),
    msrp DECIMAL(10,2) NOT NULL,
    invoice DECIMAL(10,2) NOT NULL,
    holdback DECIMAL(10,2) DEFAULT 0,
    floor_price DECIMAL(10,2) NOT NULL,
    asking_price DECIMAL(10,2) NOT NULL,
    days_in_inventory INT DEFAULT 0,
    status VARCHAR(20) DEFAULT 'available',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Dealer negotiation sessions
CREATE TABLE IF NOT EXISTS dealership.negotiation_sessions (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(100) UNIQUE NOT NULL,
    vin VARCHAR(17) NOT NULL,
    consumer_session_id VARCHAR(100),
    status VARCHAR(20) DEFAULT 'active',
    dealer_floor_used DECIMAL(10,2),
    final_price DECIMAL(10,2),
    started_at TIMESTAMP DEFAULT NOW(),
    ended_at TIMESTAMP,
    FOREIGN KEY (vin) REFERENCES dealership.inventory(vin)
);

-- Dealer offers (every counter/response)
CREATE TABLE IF NOT EXISTS dealership.offers (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(100) NOT NULL,
    round_num INT NOT NULL,
    offer_amount DECIMAL(10,2) NOT NULL,
    offer_type VARCHAR(20), -- opening, counter, final
    decision VARCHAR(20), -- accept, reject, counter
    reasoning TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    FOREIGN KEY (session_id) REFERENCES dealership.negotiation_sessions(session_id)
);

-- ============================================================
-- CONSUMER SCHEMA
-- ============================================================

-- Buyer profiles
CREATE TABLE IF NOT EXISTS consumer.buyer_profiles (
    id SERIAL PRIMARY KEY,
    buyer_id VARCHAR(100) UNIQUE NOT NULL,
    name VARCHAR(100),
    email VARCHAR(100),
    phone VARCHAR(20),
    credit_tier VARCHAR(20), -- excellent, good, fair, poor
    max_budget DECIMAL(10,2),
    max_monthly_payment DECIMAL(10,2),
    trade_in_vin VARCHAR(17),
    trade_in_value DECIMAL(10,2),
    walk_away_threshold DECIMAL(10,2),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Consumer negotiation sessions
CREATE TABLE IF NOT EXISTS consumer.negotiation_sessions (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(100) UNIQUE NOT NULL,
    buyer_id VARCHAR(100) NOT NULL,
    target_vin VARCHAR(17),
    dealer_session_id VARCHAR(100),
    status VARCHAR(20) DEFAULT 'active',
    max_price DECIMAL(10,2),
    final_price DECIMAL(10,2),
    started_at TIMESTAMP DEFAULT NOW(),
    ended_at TIMESTAMP,
    FOREIGN KEY (buyer_id) REFERENCES consumer.buyer_profiles(buyer_id)
);

-- Consumer offers
CREATE TABLE IF NOT EXISTS consumer.offers (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(100) NOT NULL,
    round_num INT NOT NULL,
    offer_amount DECIMAL(10,2) NOT NULL,
    offer_type VARCHAR(20),
    decision VARCHAR(20),
    reasoning TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    FOREIGN KEY (session_id) REFERENCES consumer.negotiation_sessions(session_id)
);

-- ============================================================
-- AATA TRUST LAYER (Shared)
-- ============================================================

-- Agent certificates (the SSL for AI agents)
CREATE TABLE IF NOT EXISTS aata.agent_certificates (
    id SERIAL PRIMARY KEY,
    agent_id VARCHAR(100) UNIQUE NOT NULL,
    agent_type VARCHAR(20) NOT NULL, -- dealership, consumer
    principal_id VARCHAR(100) NOT NULL, -- dealer license OR buyer_id
    public_key TEXT NOT NULL,
    verification_level INT DEFAULT 1, -- 1=basic, 4=full KYC
    expires_at TIMESTAMP NOT NULL,
    issued_at TIMESTAMP DEFAULT NOW(),
    revoked BOOLEAN DEFAULT FALSE
);

-- Immutable transaction logs (tamper-proof record)
CREATE TABLE IF NOT EXISTS aata.transaction_logs (
    id SERIAL PRIMARY KEY,
    transaction_id VARCHAR(100) UNIQUE NOT NULL,
    session_id VARCHAR(100) NOT NULL,
    round_num INT NOT NULL,
    dealer_agent_id VARCHAR(100),
    consumer_agent_id VARCHAR(100),
    dealer_offer DECIMAL(10,2),
    consumer_offer DECIMAL(10,2),
    action VARCHAR(20), -- offer, counter, accept, reject, walk
    cryptographic_hash VARCHAR(255),
    logged_at TIMESTAMP DEFAULT NOW()
);

-- Completed deals (final receipts)
CREATE TABLE IF NOT EXISTS aata.deal_receipts (
    id SERIAL PRIMARY KEY,
    receipt_id VARCHAR(100) UNIQUE NOT NULL,
    session_id VARCHAR(100) NOT NULL,
    vin VARCHAR(17) NOT NULL,
    final_price DECIMAL(10,2) NOT NULL,
    buyer_agent_id VARCHAR(100),
    dealer_agent_id VARCHAR(100),
    deal_terms JSONB,
    chain_tx_hash VARCHAR(255), -- for future blockchain integration
    closed_at TIMESTAMP DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_dealership_sessions ON dealership.negotiation_sessions(session_id);
CREATE INDEX IF NOT EXISTS idx_consumer_sessions ON consumer.negotiation_sessions(session_id);
CREATE INDEX IF NOT EXISTS idx_aata_transactions ON aata.transaction_logs(session_id);
CREATE INDEX IF NOT EXISTS idx_inventory_status ON dealership.inventory(status);
