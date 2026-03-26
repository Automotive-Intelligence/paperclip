import React, { useState, useEffect } from 'react';
import axios from 'axios';
import './App.css';

const API_URL = 'http://localhost:8000';
const DEMO_PRESETS = [
  {
    id: 'balanced',
    label: 'Balanced Buyer',
    description: 'Professional buyer who wants a fair deal fast.',
    profile: {
      buyer_name: 'Sarah Johnson',
      max_budget: 50000,
      walk_away_threshold: 49000,
      trade_in_value: 18500
    }
  },
  {
    id: 'aggressive',
    label: 'Aggressive Negotiator',
    description: 'Pushes hard on price and escalates quickly.',
    profile: {
      buyer_name: 'Marcus Reed',
      max_budget: 47000,
      walk_away_threshold: 45500,
      trade_in_value: 12000
    }
  },
  {
    id: 'premium',
    label: 'High-Intent Trade-In',
    description: 'More buying power with a stronger trade-in story.',
    profile: {
      buyer_name: 'Elena Brooks',
      max_budget: 54000,
      walk_away_threshold: 52000,
      trade_in_value: 23000
    }
  }
];

const formatCurrency = (value) => `$${Number(value || 0).toLocaleString()}`;

const formatTimestamp = (value) => {
  if (!value) {
    return null;
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }

  return parsed.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
};

function App() {
  const [showSplash, setShowSplash] = useState(true);
  const [splashCountdown, setSplashCountdown] = useState(10);
  const [inventory, setInventory] = useState([]);
  const [selectedVin, setSelectedVin] = useState(null);
  const [negotiating, setNegotiating] = useState(false);
  const [result, setResult] = useState(null);
  const [sessionDetail, setSessionDetail] = useState(null);
  const [inventoryLoading, setInventoryLoading] = useState(true);
  const [inventoryError, setInventoryError] = useState('');
  const [apiStatus, setApiStatus] = useState('connecting');
  const [activePreset, setActivePreset] = useState(DEMO_PRESETS[0].id);
  const [buyerProfile, setBuyerProfile] = useState({
    buyer_name: 'Sarah Johnson',
    max_budget: 50000,
    walk_away_threshold: 49000,
    trade_in_value: 18500
  });

  useEffect(() => {
    loadApiStatus();
    loadInventory();
  }, []);

  useEffect(() => {
    if (!showSplash) {
      return undefined;
    }

    const timer = setInterval(() => {
      setSplashCountdown((current) => {
        if (current <= 1) {
          clearInterval(timer);
          setShowSplash(false);
          return 0;
        }
        return current - 1;
      });
    }, 1000);

    return () => clearInterval(timer);
  }, [showSplash]);

  const loadApiStatus = async () => {
    try {
      await axios.get(`${API_URL}/`);
      setApiStatus('online');
    } catch (error) {
      console.error('API status error:', error);
      setApiStatus('offline');
    }
  };

  const loadInventory = async () => {
    setInventoryLoading(true);
    setInventoryError('');

    try {
      const response = await axios.get(`${API_URL}/inventory`);
      setInventory(response.data);
      if (response.data.length > 0) {
        setSelectedVin(response.data[0].vin);
      }
    } catch (error) {
      console.error('Error loading inventory:', error);
      setInventoryError('Inventory could not be loaded. Check the backend connection.');
    } finally {
      setInventoryLoading(false);
    }
  };

  const applyPreset = (preset) => {
    setActivePreset(preset.id);
    setBuyerProfile(preset.profile);
    setResult(null);
    setSessionDetail(null);
  };

  const runNegotiation = async (vin, profile) => {
    if (!vin) {
      return;
    }

    setNegotiating(true);
    setResult(null);
    setSessionDetail(null);

    try {
      const response = await axios.post(`${API_URL}/negotiate`, {
        vin,
        ...profile
      });
      setResult(response.data);

      if (response.data.dealer_session_id) {
        const sessionResponse = await axios.get(`${API_URL}/sessions/${response.data.dealer_session_id}`);
        setSessionDetail(sessionResponse.data);
      }
    } catch (error) {
      console.error('Negotiation error:', error);
      setResult({ success: false, message: 'Error during negotiation' });
    } finally {
      setNegotiating(false);
    }
  };

  const startNegotiation = async () => {
    await runNegotiation(selectedVin, buyerProfile);
  };

  const runJoseDemo = async () => {
    if (!inventory.length || negotiating) {
      return;
    }

    const showcaseVehicle = [...inventory].sort((left, right) => {
      const leftScore = (left.days * 1000) + (left.msrp - left.price);
      const rightScore = (right.days * 1000) + (right.msrp - right.price);
      return rightScore - leftScore;
    })[0];

    if (!showcaseVehicle) {
      return;
    }

    const josePreset = showcaseVehicle.price > 52000
      ? DEMO_PRESETS.find((preset) => preset.id === 'premium')
      : DEMO_PRESETS.find((preset) => preset.id === 'balanced');

    if (!josePreset) {
      return;
    }

    setSelectedVin(showcaseVehicle.vin);
    setActivePreset(josePreset.id);
    setBuyerProfile(josePreset.profile);

    await runNegotiation(showcaseVehicle.vin, josePreset.profile);
  };

  const selectedCar = inventory.find(car => car.vin === selectedVin);
  const budgetGap = selectedCar ? selectedCar.price - Number(buyerProfile.max_budget || 0) : 0;
  const estimatedMonthly = selectedCar ? selectedCar.price / 72 : 0;
  const msrpDelta = selectedCar ? selectedCar.msrp - selectedCar.price : 0;
  const resultDelta = result?.final_price && selectedCar ? selectedCar.price - result.final_price : 0;

  const transcriptEntries = sessionDetail
    ? [
        ...(sessionDetail.consumer_offers || []).map((offer) => ({
          id: `consumer-${offer.round_num}-${offer.created_at || offer.offer_amount}`,
          side: 'consumer',
          round: offer.round_num,
          amount: offer.offer_amount,
          decision: offer.decision,
          reasoning: offer.reasoning,
          createdAt: offer.created_at,
        })),
        ...(sessionDetail.dealer_offers || []).map((offer) => ({
          id: `dealer-${offer.round_num}-${offer.created_at || offer.offer_amount}`,
          side: 'dealer',
          round: offer.round_num,
          amount: offer.offer_amount,
          decision: offer.decision,
          reasoning: offer.reasoning,
          createdAt: offer.created_at,
        })),
      ].sort((left, right) => {
        if (left.round !== right.round) {
          return left.round - right.round;
        }
        if (left.side === right.side) {
          return 0;
        }
        return left.side === 'consumer' ? -1 : 1;
      })
    : [];

  return (
    <div className="App">
      {showSplash && (
        <div className="splash-overlay">
          <div className="splash-card">
            <p className="splash-kicker">LIVE DEMO</p>
            <h1>AATA</h1>
            <p>Autonomous agent-to-agent automotive negotiation in real time.</p>
            <div className="splash-metrics">
              <span>Michael Meta II ↔ Vera</span>
              <span>Secure negotiation rails</span>
              <span>Session-backed transcript</span>
            </div>
            <div className="splash-actions">
              <button type="button" onClick={() => setShowSplash(false)}>Start now</button>
              <span>Auto starts in {splashCountdown}s</span>
            </div>
          </div>
        </div>
      )}
      <header className="header">
        <h1>AATA</h1>
        <p>Automotive AI Transaction Authority</p>
        <div className="status-strip">
          <span className={`status-pill ${apiStatus}`}>{apiStatus === 'online' ? 'Backend Connected' : apiStatus === 'offline' ? 'Backend Offline' : 'Connecting'}</span>
          <span className="status-pill neutral">{inventory.length} vehicles staged</span>
          <span className="status-pill neutral">Jose demo mode</span>
        </div>
        <div className="hero-actions">
          <button
            type="button"
            className="jose-demo-btn"
            onClick={runJoseDemo}
            disabled={negotiating || !inventory.length}
          >
            {negotiating ? 'Running demo...' : 'Run Jose Demo'}
          </button>
        </div>
      </header>

      <div className="container">
        {/* Left Panel - Inventory */}
        <div className="panel">
          <h2>🚗 Dealership Inventory</h2>
          {inventoryLoading && <div className="panel-message">Loading inventory...</div>}
          {inventoryError && <div className="panel-message error">{inventoryError}</div>}
          {!inventoryLoading && !inventoryError && (
            <div className="inventory-list">
              {inventory.map(car => (
                <div
                  key={car.vin}
                  className={`car-card ${selectedVin === car.vin ? 'selected' : ''}`}
                  onClick={() => setSelectedVin(car.vin)}
                >
                  <div className="car-card-header">
                    <h3>{car.year} {car.make} {car.model}</h3>
                    <span className="car-badge">{car.vin.slice(-6)}</span>
                  </div>
                  <p className="trim">{car.trim}</p>
                  <p className="color">{car.color}</p>
                  <div className="price-info">
                    <span className="price">{formatCurrency(car.price)}</span>
                    <span className="days">📆 {car.days} days</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Center Panel - Buyer Profile & Negotiation */}
        <div className="panel negotiation-panel">
          <h2>👤 Buyer Profile</h2>
          <div className="preset-list">
            {DEMO_PRESETS.map((preset) => (
              <button
                key={preset.id}
                type="button"
                className={`preset-chip ${activePreset === preset.id ? 'active' : ''}`}
                onClick={() => applyPreset(preset)}
              >
                <span>{preset.label}</span>
                <small>{preset.description}</small>
              </button>
            ))}
          </div>
          <div className="profile-form">
            <label>
              Name:
              <input
                type="text"
                value={buyerProfile.buyer_name}
                onChange={(e) => setBuyerProfile({ ...buyerProfile, buyer_name: e.target.value })}
              />
            </label>
            <label>
              Max Budget:
              <input
                type="number"
                value={buyerProfile.max_budget}
                onChange={(e) => setBuyerProfile({ ...buyerProfile, max_budget: parseFloat(e.target.value) })}
              />
            </label>
            <label>
              Walk Away Threshold:
              <input
                type="number"
                value={buyerProfile.walk_away_threshold}
                onChange={(e) => setBuyerProfile({ ...buyerProfile, walk_away_threshold: parseFloat(e.target.value) })}
              />
            </label>
            <label>
              Trade-in Value:
              <input
                type="number"
                value={buyerProfile.trade_in_value}
                onChange={(e) => setBuyerProfile({ ...buyerProfile, trade_in_value: parseFloat(e.target.value) })}
              />
            </label>
          </div>

          {selectedCar && (
            <div className="selected-car">
              <h3>Selected Vehicle</h3>
              <p>{selectedCar.year} {selectedCar.make} {selectedCar.model}</p>
              <p>Asking: {formatCurrency(selectedCar.price)}</p>
              <div className="selected-car-metrics">
                <div>
                  <span className="metric-label">MSRP Delta</span>
                  <strong>{formatCurrency(msrpDelta)}</strong>
                </div>
                <div>
                  <span className="metric-label">Budget Gap</span>
                  <strong className={budgetGap > 0 ? 'metric-negative' : 'metric-positive'}>{formatCurrency(Math.abs(budgetGap))}</strong>
                </div>
                <div>
                  <span className="metric-label">Est. Monthly</span>
                  <strong>{formatCurrency(estimatedMonthly)}</strong>
                </div>
              </div>
            </div>
          )}

          <button
            className="negotiate-btn"
            onClick={startNegotiation}
            disabled={negotiating || !selectedVin}
          >
            {negotiating ? '🤖 Negotiating...' : '🤝 Start AI Negotiation'}
          </button>

          {result && (
            <div className={`result ${result.success ? 'success' : 'failure'}`}>
              <h3>{result.success ? '🎉 Deal Closed!' : '❌ Deal Not Reached'}</h3>
              {result.final_price && (
                <p className="final-price">Final Price: {formatCurrency(result.final_price)}</p>
              )}
              <div className="result-grid">
                <div>
                  <span className="metric-label">Rounds</span>
                  <strong>{result.rounds}</strong>
                </div>
                <div>
                  <span className="metric-label">Savings vs Ask</span>
                  <strong>{formatCurrency(resultDelta)}</strong>
                </div>
                <div>
                  <span className="metric-label">Outcome</span>
                  <strong>{result.success ? 'Closed' : 'Unresolved'}</strong>
                </div>
              </div>
              <p>{result.message}</p>
              {result.dealer_session_id && (
                <p className="session-id">Session: {result.dealer_session_id}</p>
              )}
            </div>
          )}

          {negotiating && (
            <div className="live-banner">
              <span className="live-dot" />
              AI agents are evaluating budget, trade-in leverage, and walk-away threshold.
            </div>
          )}

          {transcriptEntries.length > 0 && (
            <div className="transcript">
              <h3>Negotiation Transcript</h3>
              <div className="transcript-list">
                {transcriptEntries.map((entry) => (
                  <div key={entry.id} className={`transcript-entry ${entry.side}`}>
                    <div className="transcript-meta">
                      <span className="transcript-party">
                        {entry.side === 'consumer' ? buyerProfile.buyer_name : 'Michael Meta II'}
                      </span>
                      <span className="transcript-round">Round {entry.round}</span>
                    </div>
                    <div className="transcript-topline">
                      <p className="transcript-amount">{formatCurrency(entry.amount)}</p>
                      {entry.decision && (
                        <span className={`decision-pill ${entry.decision}`}>{entry.decision}</span>
                      )}
                    </div>
                    {entry.decision && (
                      <p className="transcript-decision">Decision: {entry.decision}</p>
                    )}
                    {entry.reasoning && (
                      <p className="transcript-reasoning">{entry.reasoning}</p>
                    )}
                    {formatTimestamp(entry.createdAt) && (
                      <p className="transcript-time">{formatTimestamp(entry.createdAt)}</p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Right Panel - Agent Status */}
        <div className="panel">
          <h2>🤖 AI Agents</h2>
          <div className="agent-status">
            <div className="agent">
              <h3>Michael Meta II</h3>
              <p className="agent-role">Dealership Agent</p>
              <span className={`status-badge ${apiStatus === 'online' ? 'online' : 'offline'}`}>{apiStatus === 'online' ? 'Online' : 'Waiting'}</span>
            </div>
            <div className="agent">
              <h3>Vera</h3>
              <p className="agent-role">Consumer Agent</p>
              <span className={`status-badge ${apiStatus === 'online' ? 'online' : 'offline'}`}>{apiStatus === 'online' ? 'Online' : 'Waiting'}</span>
            </div>
          </div>
          <div className="demo-stats">
            <div>
              <span className="metric-label">Selected VIN</span>
              <strong>{selectedCar ? selectedCar.vin : 'None selected'}</strong>
            </div>
            <div>
              <span className="metric-label">Target Budget</span>
              <strong>{formatCurrency(buyerProfile.max_budget)}</strong>
            </div>
            <div>
              <span className="metric-label">Trade-in Leverage</span>
              <strong>{formatCurrency(buyerProfile.trade_in_value)}</strong>
            </div>
          </div>
          <div className="trust-info">
            <h3>🔒 AATA Trust Layer</h3>
            <p>✓ Verified certificates</p>
            <p>✓ Immutable transaction logs</p>
            <p>✓ End-to-end encryption</p>
          </div>
        </div>
      </div>

      <footer className="footer">
        <p>AATA — The Rails for Automotive AI Transactions</p>
      </footer>
    </div>
  );
}

export default App;
