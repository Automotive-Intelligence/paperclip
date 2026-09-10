# Missed Call Calculator — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an interactive Missed Call Calculator that captures leads into GHL + Instantly, deployed as a Next.js app on Vercel with a backend API endpoint on Paperclip (Railway).

**Architecture:** Vercel serves the frontend (hero page, 4-question calculator, email gate, dashboard report). On email submit, the frontend POSTs to a new `/api/calculator/submit` endpoint on Paperclip (Railway), which creates a GHL contact and an Instantly lead, then returns calculated report data. Calculator math runs client-side for the preview; the API call only happens at email capture.

**Tech Stack:** Next.js 14 (React) on Vercel, Tailwind CSS, Recharts (12-month chart), FastAPI endpoint on Paperclip (Railway), GHL API, Instantly API.

**Spec:** `docs/superpowers/specs/2026-04-17-missed-call-calculator-design.md`

---

## File Structure

### Backend (Paperclip — existing repo)

| File | Action | Responsibility |
|------|--------|---------------|
| `app.py` | Modify | Add CORS middleware + import calculator endpoint |
| `tools/calculator.py` | Create | Calculator submission logic: validate, calculate, push to GHL + Instantly |
| `tests/test_calculator.py` | Create | Tests for calculation logic and endpoint |

### Frontend (New repo — `missed-call-calculator/`)

| File | Action | Responsibility |
|------|--------|---------------|
| `app/page.tsx` | Create | Hero landing page |
| `app/calculator/page.tsx` | Create | 4-question calculator + preview + email gate |
| `app/report/page.tsx` | Create | Full dashboard report with metrics and CTA |
| `app/layout.tsx` | Create | Root layout with dark theme, fonts, metadata |
| `lib/calculations.ts` | Create | Client-side calculator math (shared formulas) |
| `lib/constants.ts` | Create | Industry benchmarks, default values, colors |
| `components/MetricCard.tsx` | Create | Reusable metric card (calls saved, jobs booked, ROI) |
| `components/BenchmarkBar.tsx` | Create | Industry comparison bar chart |
| `components/ProjectionChart.tsx` | Create | 12-month cumulative revenue chart (Recharts) |
| `components/QuestionStep.tsx` | Create | Single question step with transition |
| `tailwind.config.ts` | Create | Dark theme colors, custom values |

---

## Task 1: Create Instantly Nurture Campaign for Calculator Leads

Before building anything, the Instantly nurture campaign must exist so we have a campaign ID to wire into the API.

**Files:** None (API calls only)

- [ ] **Step 1: Create the nurture campaign in Instantly**

Use the MCP tool `mcp__instantly__create_campaign` with these parameters:

```
name: "AI Phone Guy — Calculator Lead Nurture"
subject: "Your missed call report is ready"
body: (Email 1 from marketing-assets/aiphoneguy/email-sequences/lead-magnet-nurture.md)
sequence_steps: 5
step_delay_days: 2
sequence_subjects: [
  "Your missed call report is ready",
  "The ${{lost_revenue_estimate}} question",
  "How a DFW {{business_type}} business stopped losing calls",
  "The #1 objection I hear from {{business_type}} owners",
  "Last thing on this"
]
sequence_bodies: [Email 1-5 bodies from lead-magnet-nurture.md, HTML formatted]
email_list: ["info@theaiphoneguy.ai"]  (or whatever AI Phone Guy sender is warmed)
daily_limit: 30
timing_from: "09:00"
timing_to: "17:00"
timezone: "America/Chicago"
stop_on_reply: true
track_opens: false
track_clicks: false
```

- [ ] **Step 2: Record the campaign ID**

Save the returned campaign UUID. This will be used in `tools/calculator.py` as the `INSTANTLY_CALCULATOR_NURTURE_CAMPAIGN` env var.

- [ ] **Step 3: Activate the campaign**

Use `mcp__instantly__activate_campaign` with the campaign ID.

---

## Task 2: Add CORS Middleware to Paperclip

The Vercel frontend needs to call the Railway API cross-origin. Currently no CORS middleware exists.

**Files:**
- Modify: `app.py:3646` (after app initialization)

- [ ] **Step 1: Add CORS middleware**

In `app.py`, after the `app = FastAPI(...)` block (around line 3646), add:

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://theaiphoneguy.ai",
        "https://calculator.theaiphoneguy.ai",
        "http://localhost:3000",  # local dev
    ],
    allow_credentials=True,
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["*"],
)
```

- [ ] **Step 2: Verify no conflicts with existing middleware**

Check that the `RequestContextMiddleware` (line 3659) still works after adding CORS. Run:

```bash
python -c "from app import app; print('App loads OK')"
```

Expected: `App loads OK`

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "feat: add CORS middleware for calculator frontend"
```

---

## Task 3: Build Calculator Submission Endpoint

**Files:**
- Create: `tools/calculator.py`
- Create: `tests/test_calculator.py`
- Modify: `app.py` (add import + endpoint)

- [ ] **Step 1: Write tests for calculation logic**

Create `tests/test_calculator.py`:

```python
import pytest
from tools.calculator import calculate_report, CalculatorInput

def test_hvac_basic_calculation():
    """HVAC business, 60 calls/week, 30% miss rate, $850 avg job"""
    input_data = CalculatorInput(
        first_name="John",
        email="john@example.com",
        phone="972-555-1234",
        business_name="DFW Comfort HVAC",
        business_type="HVAC",
        calls_per_week=60,
        miss_rate=30,
        avg_job_value=850,
    )
    report = calculate_report(input_data)

    # 60 * 0.30 * 4.3 = 77.4 missed calls/month
    assert report["missed_calls_monthly"] == 77
    # 77 * 0.25 * 850 = 16,362.50
    assert report["monthly_lost_revenue"] == 16362
    # 16362 * 12
    assert report["annual_lost_revenue"] == 196350
    # 16362 * 0.80
    assert report["recoverable_revenue"] == 13090
    # 13090 / 187 = 69.9x
    assert report["monthly_roi"] == "70x"
    # 77.4 * 0.80 = 61.9 → 62
    assert report["calls_saved_monthly"] == 62
    # 77 * 0.25 = 19.25 → 19
    assert report["jobs_booked_monthly"] == 19
    assert report["benchmark"]["industry"] == "HVAC"
    assert report["benchmark"]["avg_miss_rate"] == 28


def test_plumbing_calculation():
    """Plumbing business, 40 calls/week, 35% miss rate, $650 avg job"""
    input_data = CalculatorInput(
        first_name="Jane",
        email="jane@example.com",
        phone="214-555-5678",
        business_name="Quick Fix Plumbing",
        business_type="Plumbing",
        calls_per_week=40,
        miss_rate=35,
        avg_job_value=650,
    )
    report = calculate_report(input_data)

    # 40 * 0.35 * 4.3 = 60.2 → 60
    assert report["missed_calls_monthly"] == 60
    # 60 * 0.25 * 650 = 9,750
    assert report["monthly_lost_revenue"] == 9750
    assert report["benchmark"]["industry"] == "Plumbing"
    assert report["benchmark"]["avg_miss_rate"] == 32


def test_unknown_business_type_uses_other():
    """Unknown business type falls back to Other benchmarks"""
    input_data = CalculatorInput(
        first_name="Test",
        email="test@example.com",
        phone="555-555-5555",
        business_name="Test Biz",
        business_type="Pet Grooming",
        calls_per_week=30,
        miss_rate=25,
        avg_job_value=200,
    )
    report = calculate_report(input_data)

    assert report["benchmark"]["industry"] == "Other"
    assert report["benchmark"]["avg_miss_rate"] == 30


def test_percentile_rank_calculation():
    """Miss rate worse than industry average → high percentile (bad)"""
    input_data = CalculatorInput(
        first_name="Test",
        email="test@example.com",
        phone="555-555-5555",
        business_name="Test HVAC",
        business_type="HVAC",
        calls_per_week=50,
        miss_rate=40,  # Worse than HVAC avg of 28%
        avg_job_value=850,
    )
    report = calculate_report(input_data)

    # 40% miss rate vs 28% avg → worse than average → percentile > 50
    assert report["benchmark"]["percentile_rank"] > 50
    assert report["benchmark"]["their_miss_rate"] == 40
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/michaelrodriguez/paperclip
python -m pytest tests/test_calculator.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'tools.calculator'`

- [ ] **Step 3: Implement calculator logic**

Create `tools/calculator.py`:

```python
"""Missed Call Calculator — compute report + push to GHL and Instantly."""

import math
import os
import logging
from pydantic import BaseModel, EmailStr
from tools.ghl import create_contact, create_opportunity
from tools.instantly import add_leads_to_campaign

logger = logging.getLogger(__name__)

INDUSTRY_BENCHMARKS = {
    "HVAC":     {"avg_miss_rate": 28, "avg_job_value": 850},
    "Plumbing": {"avg_miss_rate": 32, "avg_job_value": 650},
    "Dental":   {"avg_miss_rate": 22, "avg_job_value": 1200},
    "Roofing":  {"avg_miss_rate": 35, "avg_job_value": 8500},
    "Legal":    {"avg_miss_rate": 38, "avg_job_value": 3500},
    "Other":    {"avg_miss_rate": 30, "avg_job_value": 500},
}

FOUNDER_PRICE = 187  # $187/month


class CalculatorInput(BaseModel):
    first_name: str
    email: str
    phone: str
    business_name: str
    business_type: str
    calls_per_week: int
    miss_rate: int  # percentage 10-60
    avg_job_value: int


def calculate_report(data: CalculatorInput) -> dict:
    """Run the missed call calculation and return report data."""
    missed_calls_monthly = int(data.calls_per_week * (data.miss_rate / 100) * 4.3)
    conversion_rate = 0.25
    monthly_lost_revenue = int(missed_calls_monthly * conversion_rate * data.avg_job_value)
    annual_lost_revenue = int(monthly_lost_revenue * 12)
    recoverable_revenue = int(monthly_lost_revenue * 0.80)
    calls_saved_monthly = int(missed_calls_monthly * 0.80)
    jobs_booked_monthly = int(missed_calls_monthly * conversion_rate)

    roi_raw = recoverable_revenue / FOUNDER_PRICE if FOUNDER_PRICE > 0 else 0
    monthly_roi = f"{round(roi_raw)}x"

    benchmark = INDUSTRY_BENCHMARKS.get(data.business_type, INDUSTRY_BENCHMARKS["Other"])
    industry = data.business_type if data.business_type in INDUSTRY_BENCHMARKS else "Other"

    # Percentile: how bad is their miss rate vs industry average
    # Higher percentile = worse (losing more than X% of businesses)
    avg = benchmark["avg_miss_rate"]
    if data.miss_rate >= avg:
        percentile_rank = min(50 + int((data.miss_rate - avg) / avg * 50), 95)
    else:
        percentile_rank = max(50 - int((avg - data.miss_rate) / avg * 50), 5)

    return {
        "missed_calls_monthly": missed_calls_monthly,
        "monthly_lost_revenue": monthly_lost_revenue,
        "annual_lost_revenue": annual_lost_revenue,
        "recoverable_revenue": recoverable_revenue,
        "monthly_roi": monthly_roi,
        "calls_saved_monthly": calls_saved_monthly,
        "jobs_booked_monthly": jobs_booked_monthly,
        "benchmark": {
            "industry": industry,
            "avg_miss_rate": benchmark["avg_miss_rate"],
            "their_miss_rate": data.miss_rate,
            "percentile_rank": percentile_rank,
        },
    }


def push_calculator_lead_to_ghl(data: CalculatorInput, report: dict) -> dict:
    """Create a GHL contact for the calculator lead."""
    result = create_contact(
        business_name=data.business_name,
        city="DFW",
        business_type=data.business_type,
        email_hook=f"Missed Call Calculator: ${report['monthly_lost_revenue']}/mo lost",
        reason="Calculator lead magnet submission",
        source_agent="tyler",
        tags=["lead-magnet-calculator", data.business_type.lower()],
        email=data.email,
        phone=data.phone,
        contact_name=data.first_name,
        business_key="aiphoneguy",
    )
    logger.info(f"GHL contact created for calculator lead: {data.email}")
    return result


def push_calculator_lead_to_instantly(data: CalculatorInput, report: dict) -> dict:
    """Create an Instantly lead in the calculator nurture campaign."""
    campaign_id = os.getenv("INSTANTLY_CALCULATOR_NURTURE_CAMPAIGN")
    if not campaign_id:
        logger.error("INSTANTLY_CALCULATOR_NURTURE_CAMPAIGN not set")
        return {"status": "error", "reason": "campaign not configured"}

    lead = {
        "email": data.email,
        "first_name": data.first_name,
        "last_name": "",
        "company_name": data.business_name,
        "phone": data.phone,
        "custom_variables": {
            "business_type": data.business_type,
            "calls_per_week": str(data.calls_per_week),
            "miss_rate": str(data.miss_rate),
            "avg_job_value": str(data.avg_job_value),
            "missed_calls_monthly": str(report["missed_calls_monthly"]),
            "lost_revenue_estimate": str(report["monthly_lost_revenue"]),
            "recoverable_revenue": str(report["recoverable_revenue"]),
        },
    }
    result = add_leads_to_campaign(campaign_id, [lead])
    logger.info(f"Instantly lead created for calculator: {data.email} → {result}")
    return result


async def handle_calculator_submit(data: CalculatorInput) -> dict:
    """Full submission handler: calculate → GHL → Instantly → return report."""
    report = calculate_report(data)

    ghl_result = {"status": "skipped"}
    instantly_result = {"status": "skipped"}

    try:
        ghl_result = push_calculator_lead_to_ghl(data, report)
    except Exception as e:
        logger.error(f"GHL push failed for {data.email}: {e}")
        ghl_result = {"status": "error", "reason": str(e)}

    try:
        instantly_result = push_calculator_lead_to_instantly(data, report)
    except Exception as e:
        logger.error(f"Instantly push failed for {data.email}: {e}")
        instantly_result = {"status": "error", "reason": str(e)}

    return {
        **report,
        "_lead_capture": {
            "ghl": ghl_result.get("status", "unknown"),
            "instantly": instantly_result.get("status", "unknown"),
        },
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/michaelrodriguez/paperclip
python -m pytest tests/test_calculator.py -v
```

Expected: All 4 tests PASS

- [ ] **Step 5: Add endpoint to app.py**

At the end of `app.py` (before any `if __name__` block), add:

```python
from tools.calculator import CalculatorInput, handle_calculator_submit

@app.post("/api/calculator/submit")
async def calculator_submit(payload: CalculatorInput):
    """Missed Call Calculator — captures lead into GHL + Instantly, returns report."""
    result = await handle_calculator_submit(payload)
    return result
```

No auth required — this is a public lead capture endpoint.

- [ ] **Step 6: Test the endpoint locally**

```bash
cd /Users/michaelrodriguez/paperclip
python -c "
from tools.calculator import CalculatorInput, calculate_report
data = CalculatorInput(
    first_name='Test', email='test@test.com', phone='555-1234',
    business_name='Test HVAC', business_type='HVAC',
    calls_per_week=60, miss_rate=30, avg_job_value=850
)
report = calculate_report(data)
print(f'Missed calls: {report[\"missed_calls_monthly\"]}/mo')
print(f'Lost revenue: \${report[\"monthly_lost_revenue\"]}/mo')
print(f'Recoverable: \${report[\"recoverable_revenue\"]}/mo')
print(f'ROI: {report[\"monthly_roi\"]}')
"
```

Expected:
```
Missed calls: 77/mo
Lost revenue: $16362/mo
Recoverable: $13090/mo
ROI: 70x
```

- [ ] **Step 7: Commit**

```bash
git add tools/calculator.py tests/test_calculator.py app.py
git commit -m "feat: add calculator submission endpoint with GHL + Instantly integration"
```

---

## Task 4: Set Environment Variable on Railway

**Files:** None (Railway dashboard or CLI)

- [ ] **Step 1: Add the Instantly nurture campaign ID to Railway env vars**

```bash
# Via Railway CLI or dashboard
INSTANTLY_CALCULATOR_NURTURE_CAMPAIGN=<campaign-id-from-task-1>
```

- [ ] **Step 2: Deploy Paperclip to Railway**

```bash
git push origin main
```

Railway auto-deploys on push. Verify the endpoint is live:

```bash
curl -X POST https://<railway-url>/api/calculator/submit \
  -H "Content-Type: application/json" \
  -d '{"first_name":"Test","email":"test@test.com","phone":"555-1234","business_name":"Test HVAC","business_type":"HVAC","calls_per_week":60,"miss_rate":30,"avg_job_value":850}'
```

Expected: JSON response with `missed_calls_monthly`, `monthly_lost_revenue`, etc.

---

## Task 5: Scaffold Next.js Frontend

**Files:** New repo `missed-call-calculator/`

- [ ] **Step 1: Create Next.js project**

```bash
cd /Users/michaelrodriguez/Documents/GitHub
npx create-next-app@14 missed-call-calculator --typescript --tailwind --app --no-src-dir --no-import-alias
cd missed-call-calculator
```

- [ ] **Step 2: Install dependencies**

```bash
npm install recharts
```

- [ ] **Step 3: Configure Tailwind with dark theme colors**

Replace `tailwind.config.ts`:

```typescript
import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./lib/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        "calc-bg": "#0f172a",
        "calc-bg-light": "#1e293b",
        "calc-card": "#334155",
        "calc-green": "#22c55e",
        "calc-blue": "#3b82f6",
        "calc-purple": "#a855f7",
        "calc-red": "#ef4444",
        "calc-amber": "#f59e0b",
      },
    },
  },
  plugins: [],
};
export default config;
```

- [ ] **Step 4: Set up root layout**

Replace `app/layout.tsx`:

```tsx
import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Missed Call Calculator | The AI Phone Guy",
  description:
    "Find out how much revenue your business is losing to missed calls. Free calculator for DFW service businesses.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body
        className={`${inter.className} bg-calc-bg text-white min-h-screen`}
      >
        {children}
      </body>
    </html>
  );
}
```

- [ ] **Step 5: Update globals.css**

Replace `app/globals.css`:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

body {
  background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
  min-height: 100vh;
}

input[type="range"] {
  -webkit-appearance: none;
  appearance: none;
  height: 8px;
  border-radius: 4px;
  background: #334155;
  outline: none;
}

input[type="range"]::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: #22c55e;
  cursor: pointer;
  border: 2px solid #0f172a;
}
```

- [ ] **Step 6: Create environment config**

Create `.env.local`:

```
NEXT_PUBLIC_API_URL=http://localhost:8000
```

Create `.env.production`:

```
NEXT_PUBLIC_API_URL=https://<paperclip-railway-url>
```

- [ ] **Step 7: Commit**

```bash
git init
git add .
git commit -m "feat: scaffold Next.js app with dark theme"
```

---

## Task 6: Build Constants and Calculation Library

**Files:**
- Create: `lib/constants.ts`
- Create: `lib/calculations.ts`

- [ ] **Step 1: Create constants**

Create `lib/constants.ts`:

```typescript
export const INDUSTRY_BENCHMARKS: Record<
  string,
  { avgMissRate: number; avgJobValue: number }
> = {
  HVAC: { avgMissRate: 28, avgJobValue: 850 },
  Plumbing: { avgMissRate: 32, avgJobValue: 650 },
  Dental: { avgMissRate: 22, avgJobValue: 1200 },
  Roofing: { avgMissRate: 35, avgJobValue: 8500 },
  Legal: { avgMissRate: 38, avgJobValue: 3500 },
  Other: { avgMissRate: 30, avgJobValue: 500 },
};

export const BUSINESS_TYPES = [
  "HVAC",
  "Plumbing",
  "Dental",
  "Roofing",
  "Legal",
  "Other",
] as const;

export type BusinessType = (typeof BUSINESS_TYPES)[number];

export const FOUNDER_PRICE = 187;

export const DEMO_LINK = "https://bit.ly/4sMZpTi";

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
```

- [ ] **Step 2: Create client-side calculation library**

Create `lib/calculations.ts`:

```typescript
import { INDUSTRY_BENCHMARKS, FOUNDER_PRICE } from "./constants";

export interface CalculatorInputs {
  businessType: string;
  callsPerWeek: number;
  missRate: number;
  avgJobValue: number;
}

export interface CalculatorPreview {
  missedCallsMonthly: number;
  monthlyLostRevenue: number;
}

export interface FullReport {
  missedCallsMonthly: number;
  monthlyLostRevenue: number;
  annualLostRevenue: number;
  recoverableRevenue: number;
  monthlyRoi: string;
  callsSavedMonthly: number;
  jobsBookedMonthly: number;
  benchmark: {
    industry: string;
    avgMissRate: number;
    theirMissRate: number;
    percentileRank: number;
  };
}

export function calculatePreview(inputs: CalculatorInputs): CalculatorPreview {
  const missedCallsMonthly = Math.floor(
    inputs.callsPerWeek * (inputs.missRate / 100) * 4.3
  );
  const monthlyLostRevenue = Math.floor(
    missedCallsMonthly * 0.25 * inputs.avgJobValue
  );
  return { missedCallsMonthly, monthlyLostRevenue };
}

export function calculateFullReport(inputs: CalculatorInputs): FullReport {
  const preview = calculatePreview(inputs);
  const annualLostRevenue = Math.floor(preview.monthlyLostRevenue * 12);
  const recoverableRevenue = Math.floor(preview.monthlyLostRevenue * 0.8);
  const callsSavedMonthly = Math.floor(preview.missedCallsMonthly * 0.8);
  const jobsBookedMonthly = Math.floor(preview.missedCallsMonthly * 0.25);
  const roiRaw = recoverableRevenue / FOUNDER_PRICE;
  const monthlyRoi = `${Math.round(roiRaw)}x`;

  const benchmark = INDUSTRY_BENCHMARKS[inputs.businessType] ||
    INDUSTRY_BENCHMARKS["Other"];
  const industry = inputs.businessType in INDUSTRY_BENCHMARKS
    ? inputs.businessType
    : "Other";

  const avg = benchmark.avgMissRate;
  let percentileRank: number;
  if (inputs.missRate >= avg) {
    percentileRank = Math.min(
      50 + Math.floor(((inputs.missRate - avg) / avg) * 50),
      95
    );
  } else {
    percentileRank = Math.max(
      50 - Math.floor(((avg - inputs.missRate) / avg) * 50),
      5
    );
  }

  return {
    ...preview,
    annualLostRevenue,
    recoverableRevenue,
    monthlyRoi,
    callsSavedMonthly,
    jobsBookedMonthly,
    benchmark: {
      industry,
      avgMissRate: benchmark.avgMissRate,
      theirMissRate: inputs.missRate,
      percentileRank,
    },
  };
}

export function generateProjectionData(monthlyLost: number): Array<{
  month: string;
  cumulative: number;
}> {
  const months = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
  ];
  return months.map((month, i) => ({
    month,
    cumulative: monthlyLost * (i + 1),
  }));
}
```

- [ ] **Step 3: Commit**

```bash
git add lib/
git commit -m "feat: add calculator constants and calculation library"
```

---

## Task 7: Build Reusable Components

**Files:**
- Create: `components/MetricCard.tsx`
- Create: `components/BenchmarkBar.tsx`
- Create: `components/ProjectionChart.tsx`
- Create: `components/QuestionStep.tsx`

- [ ] **Step 1: Create MetricCard component**

Create `components/MetricCard.tsx`:

```tsx
interface MetricCardProps {
  label: string;
  value: string | number;
  color: "green" | "blue" | "purple";
}

const colorMap = {
  green: {
    bg: "bg-calc-green/10",
    border: "border-calc-green/30",
    text: "text-calc-green",
  },
  blue: {
    bg: "bg-calc-blue/10",
    border: "border-calc-blue/30",
    text: "text-calc-blue",
  },
  purple: {
    bg: "bg-calc-purple/10",
    border: "border-calc-purple/30",
    text: "text-calc-purple",
  },
};

export default function MetricCard({ label, value, color }: MetricCardProps) {
  const c = colorMap[color];
  return (
    <div
      className={`${c.bg} ${c.border} border rounded-xl p-6 text-center`}
    >
      <div className={`text-3xl font-bold ${c.text}`}>{value}</div>
      <div className="text-sm text-gray-400 mt-1 uppercase tracking-wider">
        {label}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Create BenchmarkBar component**

Create `components/BenchmarkBar.tsx`:

```tsx
interface BenchmarkBarProps {
  industry: string;
  avgMissRate: number;
  theirMissRate: number;
  percentileRank: number;
}

export default function BenchmarkBar({
  industry,
  avgMissRate,
  theirMissRate,
  percentileRank,
}: BenchmarkBarProps) {
  const maxRate = 60;
  const avgWidth = (avgMissRate / maxRate) * 100;
  const theirWidth = (theirMissRate / maxRate) * 100;

  return (
    <div className="bg-calc-card rounded-xl p-6">
      <h3 className="text-lg font-semibold mb-4">
        Industry Comparison: {industry}
      </h3>
      <div className="space-y-4">
        <div>
          <div className="flex justify-between text-sm mb-1">
            <span className="text-gray-400">{industry} Average</span>
            <span className="text-gray-300">{avgMissRate}%</span>
          </div>
          <div className="w-full h-3 bg-gray-700 rounded-full overflow-hidden">
            <div
              className="h-full bg-calc-blue rounded-full"
              style={{ width: `${avgWidth}%` }}
            />
          </div>
        </div>
        <div>
          <div className="flex justify-between text-sm mb-1">
            <span className="text-gray-400">Your Business</span>
            <span
              className={
                theirMissRate > avgMissRate
                  ? "text-calc-red"
                  : "text-calc-green"
              }
            >
              {theirMissRate}%
            </span>
          </div>
          <div className="w-full h-3 bg-gray-700 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full ${
                theirMissRate > avgMissRate ? "bg-calc-red" : "bg-calc-green"
              }`}
              style={{ width: `${theirWidth}%` }}
            />
          </div>
        </div>
      </div>
      <p className="text-sm text-gray-400 mt-4">
        You&apos;re losing more calls than{" "}
        <span className="text-white font-semibold">{percentileRank}%</span> of{" "}
        {industry} businesses in DFW.
      </p>
    </div>
  );
}
```

- [ ] **Step 3: Create ProjectionChart component**

Create `components/ProjectionChart.tsx`:

```tsx
"use client";

import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";

interface ProjectionChartProps {
  data: Array<{ month: string; cumulative: number }>;
}

export default function ProjectionChart({ data }: ProjectionChartProps) {
  return (
    <div className="bg-calc-card rounded-xl p-6">
      <h3 className="text-lg font-semibold mb-4">
        12-Month Cumulative Lost Revenue
      </h3>
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={data}>
          <XAxis
            dataKey="month"
            tick={{ fill: "#94a3b8", fontSize: 12 }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            tick={{ fill: "#94a3b8", fontSize: 12 }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`}
          />
          <Tooltip
            contentStyle={{
              background: "#1e293b",
              border: "1px solid #334155",
              borderRadius: "8px",
              color: "#f1f5f9",
            }}
            formatter={(value: number) => [
              `$${value.toLocaleString()}`,
              "Cumulative Lost",
            ]}
          />
          <Bar
            dataKey="cumulative"
            fill="#ef4444"
            radius={[4, 4, 0, 0]}
            fillOpacity={0.8}
          />
        </BarChart>
      </ResponsiveContainer>
      <p className="text-sm text-gray-400 mt-2 text-center">
        Every month you wait, the number grows.
      </p>
    </div>
  );
}
```

- [ ] **Step 4: Create QuestionStep component**

Create `components/QuestionStep.tsx`:

```tsx
"use client";

import { ReactNode } from "react";

interface QuestionStepProps {
  step: number;
  totalSteps: number;
  question: string;
  children: ReactNode;
  onNext: () => void;
  onBack?: () => void;
  canProceed?: boolean;
}

export default function QuestionStep({
  step,
  totalSteps,
  question,
  children,
  onNext,
  onBack,
  canProceed = true,
}: QuestionStepProps) {
  return (
    <div className="max-w-xl mx-auto px-4">
      <div className="mb-8">
        <div className="flex gap-2 mb-4">
          {Array.from({ length: totalSteps }).map((_, i) => (
            <div
              key={i}
              className={`h-1.5 flex-1 rounded-full ${
                i <= step ? "bg-calc-green" : "bg-gray-700"
              }`}
            />
          ))}
        </div>
        <p className="text-sm text-gray-400">
          Question {step + 1} of {totalSteps}
        </p>
      </div>

      <h2 className="text-2xl font-bold mb-8">{question}</h2>

      <div className="mb-8">{children}</div>

      <div className="flex gap-4">
        {onBack && (
          <button
            onClick={onBack}
            className="px-6 py-3 rounded-lg border border-gray-600 text-gray-300 hover:bg-gray-800 transition"
          >
            Back
          </button>
        )}
        <button
          onClick={onNext}
          disabled={!canProceed}
          className="flex-1 px-6 py-3 rounded-lg bg-calc-green text-black font-semibold hover:bg-green-400 transition disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {step === totalSteps - 1 ? "See My Results" : "Next"}
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Commit**

```bash
git add components/
git commit -m "feat: add calculator UI components"
```

---

## Task 8: Build the Hero Page

**Files:**
- Modify: `app/page.tsx`

- [ ] **Step 1: Build hero landing page**

Replace `app/page.tsx`:

```tsx
import Link from "next/link";

export default function Home() {
  return (
    <main className="min-h-screen flex flex-col items-center justify-center px-4 text-center">
      <div className="max-w-3xl mx-auto">
        <p className="text-calc-green text-sm font-semibold tracking-wider uppercase mb-4">
          Free Calculator for DFW Service Businesses
        </p>

        <h1 className="text-4xl md:text-6xl font-bold leading-tight mb-6">
          How Much Revenue Are You{" "}
          <span className="text-calc-red">Losing</span> to Missed Calls?
        </h1>

        <p className="text-xl text-gray-400 mb-10 max-w-2xl mx-auto">
          Most DFW service businesses lose $3,000–$12,000/month to voicemail.
          Find out your number in 60 seconds.
        </p>

        <Link
          href="/calculator"
          className="inline-block px-10 py-4 bg-calc-green text-black text-lg font-bold rounded-xl hover:bg-green-400 transition shadow-lg shadow-calc-green/20"
        >
          Calculate My Lost Revenue →
        </Link>

        <p className="text-sm text-gray-500 mt-6">
          No signup required. Takes 60 seconds.
        </p>
      </div>
    </main>
  );
}
```

- [ ] **Step 2: Verify it renders**

```bash
cd /Users/michaelrodriguez/Documents/GitHub/missed-call-calculator
npm run dev
```

Open http://localhost:3000 — verify dark background, headline, green CTA button.

- [ ] **Step 3: Commit**

```bash
git add app/page.tsx
git commit -m "feat: build hero landing page"
```

---

## Task 9: Build the Calculator Page

**Files:**
- Create: `app/calculator/page.tsx`

- [ ] **Step 1: Build the 4-question calculator with preview and email gate**

Create `app/calculator/page.tsx`:

```tsx
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import QuestionStep from "@/components/QuestionStep";
import {
  BUSINESS_TYPES,
  INDUSTRY_BENCHMARKS,
  API_URL,
} from "@/lib/constants";
import { calculatePreview } from "@/lib/calculations";

type Step = "q1" | "q2" | "q3" | "q4" | "preview" | "gate" | "submitting";

export default function CalculatorPage() {
  const router = useRouter();
  const [step, setStep] = useState<Step>("q1");
  const [businessType, setBusinessType] = useState("");
  const [callsPerWeek, setCallsPerWeek] = useState(50);
  const [missRate, setMissRate] = useState(30);
  const [avgJobValue, setAvgJobValue] = useState(500);
  const [firstName, setFirstName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [businessName, setBusinessName] = useState("");
  const [error, setError] = useState("");

  const handleBusinessTypeSelect = (type: string) => {
    setBusinessType(type);
    const bm = INDUSTRY_BENCHMARKS[type];
    if (bm) setAvgJobValue(bm.avgJobValue);
  };

  const preview = calculatePreview({
    businessType,
    callsPerWeek,
    missRate,
    avgJobValue,
  });

  const handleSubmit = async () => {
    setStep("submitting");
    setError("");
    try {
      const res = await fetch(`${API_URL}/api/calculator/submit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          first_name: firstName,
          email,
          phone,
          business_name: businessName,
          business_type: businessType,
          calls_per_week: callsPerWeek,
          miss_rate: missRate,
          avg_job_value: avgJobValue,
        }),
      });
      if (!res.ok) throw new Error("Submission failed");
      const data = await res.json();
      sessionStorage.setItem("calculatorReport", JSON.stringify(data));
      router.push("/report");
    } catch (e) {
      setError("Something went wrong. Please try again.");
      setStep("gate");
    }
  };

  if (step === "q1") {
    return (
      <main className="min-h-screen flex items-center justify-center py-12">
        <QuestionStep
          step={0}
          totalSteps={4}
          question="What type of business do you run?"
          onNext={() => setStep("q2")}
          canProceed={businessType !== ""}
        >
          <div className="grid grid-cols-2 gap-3">
            {BUSINESS_TYPES.map((type) => (
              <button
                key={type}
                onClick={() => handleBusinessTypeSelect(type)}
                className={`p-4 rounded-xl border text-left transition ${
                  businessType === type
                    ? "border-calc-green bg-calc-green/10 text-white"
                    : "border-gray-700 bg-calc-card text-gray-300 hover:border-gray-500"
                }`}
              >
                {type === "Other" ? "Other Service Business" : type}
              </button>
            ))}
          </div>
        </QuestionStep>
      </main>
    );
  }

  if (step === "q2") {
    return (
      <main className="min-h-screen flex items-center justify-center py-12">
        <QuestionStep
          step={1}
          totalSteps={4}
          question="How many calls does your business get per week?"
          onNext={() => setStep("q3")}
          onBack={() => setStep("q1")}
        >
          <div className="text-center">
            <div className="text-5xl font-bold text-calc-green mb-4">
              {callsPerWeek}
            </div>
            <p className="text-gray-400 mb-6">calls per week</p>
            <input
              type="range"
              min={10}
              max={200}
              value={callsPerWeek}
              onChange={(e) => setCallsPerWeek(Number(e.target.value))}
              className="w-full"
            />
            <div className="flex justify-between text-sm text-gray-500 mt-2">
              <span>10</span>
              <span>200</span>
            </div>
          </div>
        </QuestionStep>
      </main>
    );
  }

  if (step === "q3") {
    return (
      <main className="min-h-screen flex items-center justify-center py-12">
        <QuestionStep
          step={2}
          totalSteps={4}
          question="What percentage of calls go to voicemail or get missed?"
          onNext={() => setStep("q4")}
          onBack={() => setStep("q2")}
        >
          <div className="text-center">
            <div className="text-5xl font-bold text-calc-amber mb-4">
              {missRate}%
            </div>
            <p className="text-gray-400 mb-6">of calls missed</p>
            <input
              type="range"
              min={10}
              max={60}
              value={missRate}
              onChange={(e) => setMissRate(Number(e.target.value))}
              className="w-full"
            />
            <div className="flex justify-between text-sm text-gray-500 mt-2">
              <span>10%</span>
              <span>60%</span>
            </div>
          </div>
        </QuestionStep>
      </main>
    );
  }

  if (step === "q4") {
    return (
      <main className="min-h-screen flex items-center justify-center py-12">
        <QuestionStep
          step={3}
          totalSteps={4}
          question="What's your average job value?"
          onNext={() => setStep("preview")}
          onBack={() => setStep("q3")}
        >
          <div className="text-center">
            <div className="text-5xl font-bold text-calc-blue mb-4">
              ${avgJobValue.toLocaleString()}
            </div>
            <p className="text-gray-400 mb-6">
              Pre-filled for {businessType || "your industry"}. Adjust if
              needed.
            </p>
            <input
              type="range"
              min={100}
              max={20000}
              step={50}
              value={avgJobValue}
              onChange={(e) => setAvgJobValue(Number(e.target.value))}
              className="w-full"
            />
            <div className="flex justify-between text-sm text-gray-500 mt-2">
              <span>$100</span>
              <span>$20,000</span>
            </div>
          </div>
        </QuestionStep>
      </main>
    );
  }

  if (step === "preview") {
    return (
      <main className="min-h-screen flex items-center justify-center py-12 px-4">
        <div className="max-w-xl mx-auto text-center">
          <p className="text-calc-amber text-sm font-semibold uppercase tracking-wider mb-4">
            Your Results Preview
          </p>
          <h2 className="text-2xl md:text-3xl font-bold mb-6">
            Based on your numbers, you&apos;re missing approximately
          </h2>
          <div className="text-6xl font-bold text-calc-red mb-2">
            {preview.missedCallsMonthly}
          </div>
          <p className="text-xl text-gray-400 mb-4">calls every month</p>
          <p className="text-lg text-gray-300 mb-8">
            At your average job value of{" "}
            <span className="text-white font-semibold">
              ${avgJobValue.toLocaleString()}
            </span>
            , that&apos;s up to{" "}
            <span className="text-calc-red font-bold text-2xl">
              ${preview.monthlyLostRevenue.toLocaleString()}
            </span>{" "}
            in potential revenue walking out the door.
          </p>
          <button
            onClick={() => setStep("gate")}
            className="px-10 py-4 bg-calc-green text-black text-lg font-bold rounded-xl hover:bg-green-400 transition shadow-lg shadow-calc-green/20"
          >
            Get Your Full Revenue Report →
          </button>
          <p className="text-sm text-gray-500 mt-4">
            Includes industry benchmarks, 12-month projections, and ROI
            analysis
          </p>
        </div>
      </main>
    );
  }

  // Gate + Submitting
  return (
    <main className="min-h-screen flex items-center justify-center py-12 px-4">
      <div className="max-w-md mx-auto w-full">
        <h2 className="text-2xl font-bold mb-2 text-center">
          Get Your Full Revenue Report
        </h2>
        <p className="text-gray-400 text-center mb-8">
          See your industry comparison, 12-month projection, and ROI analysis.
        </p>
        {error && (
          <p className="text-calc-red text-sm text-center mb-4">{error}</p>
        )}
        <div className="space-y-4">
          <input
            type="text"
            placeholder="First Name"
            value={firstName}
            onChange={(e) => setFirstName(e.target.value)}
            className="w-full px-4 py-3 rounded-lg bg-calc-card border border-gray-700 text-white placeholder-gray-500 focus:border-calc-green focus:outline-none"
          />
          <input
            type="email"
            placeholder="Email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full px-4 py-3 rounded-lg bg-calc-card border border-gray-700 text-white placeholder-gray-500 focus:border-calc-green focus:outline-none"
          />
          <input
            type="tel"
            placeholder="Phone"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            className="w-full px-4 py-3 rounded-lg bg-calc-card border border-gray-700 text-white placeholder-gray-500 focus:border-calc-green focus:outline-none"
          />
          <input
            type="text"
            placeholder="Business Name"
            value={businessName}
            onChange={(e) => setBusinessName(e.target.value)}
            className="w-full px-4 py-3 rounded-lg bg-calc-card border border-gray-700 text-white placeholder-gray-500 focus:border-calc-green focus:outline-none"
          />
          <button
            onClick={handleSubmit}
            disabled={
              step === "submitting" || !firstName || !email || !phone || !businessName
            }
            className="w-full px-6 py-4 bg-calc-green text-black text-lg font-bold rounded-xl hover:bg-green-400 transition disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {step === "submitting" ? "Generating Report..." : "Get My Report"}
          </button>
        </div>
        <p className="text-xs text-gray-500 text-center mt-4">
          We&apos;ll send you a few helpful emails. Unsubscribe anytime.
        </p>
      </div>
    </main>
  );
}
```

- [ ] **Step 2: Test the full calculator flow locally**

```bash
npm run dev
```

Open http://localhost:3000 → click CTA → step through all 4 questions → verify preview shows calculated numbers → verify email gate form renders. Submit will fail until the API is deployed — that's expected.

- [ ] **Step 3: Commit**

```bash
git add app/calculator/
git commit -m "feat: build 4-step calculator with preview and email gate"
```

---

## Task 10: Build the Report Dashboard Page

**Files:**
- Create: `app/report/page.tsx`

- [ ] **Step 1: Build the full report dashboard**

Create `app/report/page.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import MetricCard from "@/components/MetricCard";
import BenchmarkBar from "@/components/BenchmarkBar";
import ProjectionChart from "@/components/ProjectionChart";
import { generateProjectionData } from "@/lib/calculations";
import { DEMO_LINK, FOUNDER_PRICE } from "@/lib/constants";

interface ReportData {
  missed_calls_monthly?: number;
  missedCallsMonthly?: number;
  monthly_lost_revenue?: number;
  monthlyLostRevenue?: number;
  annual_lost_revenue?: number;
  annualLostRevenue?: number;
  recoverable_revenue?: number;
  recoverableRevenue?: number;
  monthly_roi?: string;
  monthlyRoi?: string;
  calls_saved_monthly?: number;
  callsSavedMonthly?: number;
  jobs_booked_monthly?: number;
  jobsBookedMonthly?: number;
  benchmark?: {
    industry: string;
    avg_miss_rate?: number;
    avgMissRate?: number;
    their_miss_rate?: number;
    theirMissRate?: number;
    percentile_rank?: number;
    percentileRank?: number;
  };
}

function normalize(data: ReportData) {
  return {
    missedCallsMonthly: data.missed_calls_monthly ?? data.missedCallsMonthly ?? 0,
    monthlyLostRevenue: data.monthly_lost_revenue ?? data.monthlyLostRevenue ?? 0,
    annualLostRevenue: data.annual_lost_revenue ?? data.annualLostRevenue ?? 0,
    recoverableRevenue: data.recoverable_revenue ?? data.recoverableRevenue ?? 0,
    monthlyRoi: data.monthly_roi ?? data.monthlyRoi ?? "0x",
    callsSavedMonthly: data.calls_saved_monthly ?? data.callsSavedMonthly ?? 0,
    jobsBookedMonthly: data.jobs_booked_monthly ?? data.jobsBookedMonthly ?? 0,
    benchmark: {
      industry: data.benchmark?.industry ?? "Other",
      avgMissRate: data.benchmark?.avg_miss_rate ?? data.benchmark?.avgMissRate ?? 30,
      theirMissRate: data.benchmark?.their_miss_rate ?? data.benchmark?.theirMissRate ?? 30,
      percentileRank: data.benchmark?.percentile_rank ?? data.benchmark?.percentileRank ?? 50,
    },
  };
}

export default function ReportPage() {
  const router = useRouter();
  const [report, setReport] = useState<ReturnType<typeof normalize> | null>(null);

  useEffect(() => {
    const stored = sessionStorage.getItem("calculatorReport");
    if (!stored) {
      router.push("/");
      return;
    }
    setReport(normalize(JSON.parse(stored)));
  }, [router]);

  if (!report) return null;

  const projectionData = generateProjectionData(report.monthlyLostRevenue);

  return (
    <main className="min-h-screen py-12 px-4">
      <div className="max-w-4xl mx-auto">
        {/* Header */}
        <div className="text-center mb-12">
          <p className="text-calc-green text-sm font-semibold uppercase tracking-wider mb-2">
            Your Revenue Report
          </p>
          <h1 className="text-3xl md:text-4xl font-bold mb-4">
            You could recover{" "}
            <span className="text-calc-green">
              ${report.recoverableRevenue.toLocaleString()}/month
            </span>
          </h1>
          <p className="text-gray-400 text-lg">
            That&apos;s{" "}
            <span className="text-white font-semibold">
              ${report.annualLostRevenue.toLocaleString()}
            </span>{" "}
            in potential revenue over the next 12 months.
          </p>
        </div>

        {/* Metric Cards */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
          <MetricCard
            label="Calls Saved / Month"
            value={report.callsSavedMonthly}
            color="green"
          />
          <MetricCard
            label="Jobs Booked / Month"
            value={report.jobsBookedMonthly}
            color="blue"
          />
          <MetricCard
            label="ROI vs $187/mo"
            value={report.monthlyRoi}
            color="purple"
          />
        </div>

        {/* Benchmark */}
        <div className="mb-8">
          <BenchmarkBar
            industry={report.benchmark.industry}
            avgMissRate={report.benchmark.avgMissRate}
            theirMissRate={report.benchmark.theirMissRate}
            percentileRank={report.benchmark.percentileRank}
          />
        </div>

        {/* Projection Chart */}
        <div className="mb-8">
          <ProjectionChart data={projectionData} />
        </div>

        {/* The Fix */}
        <div className="bg-calc-green/10 border border-calc-green/30 rounded-xl p-8 text-center mb-8">
          <h3 className="text-xl font-bold mb-2">
            What if you answered every call, 24/7?
          </h3>
          <p className="text-gray-300 mb-4">
            The AI Phone Guy catches the calls your team can&apos;t get to.
            After hours, during jobs, on weekends. No hardware, no new phone
            number.
          </p>
          <div className="text-4xl font-bold text-calc-green mb-1">
            ${report.recoverableRevenue.toLocaleString()}/mo
          </div>
          <p className="text-gray-400">
            in recovered revenue for just ${FOUNDER_PRICE}/month
          </p>
        </div>

        {/* CTA */}
        <div className="text-center">
          <a
            href={DEMO_LINK}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-block px-12 py-5 bg-calc-green text-black text-xl font-bold rounded-xl hover:bg-green-400 transition shadow-lg shadow-calc-green/20"
          >
            Book a 15-Minute Demo
          </a>
          <p className="text-sm text-gray-500 mt-4">
            See the AI receptionist handle a live call. No pitch deck, no
            pressure.
          </p>
        </div>
      </div>
    </main>
  );
}
```

- [ ] **Step 2: Test the report page with mock data**

In browser console at http://localhost:3000, run:

```javascript
sessionStorage.setItem("calculatorReport", JSON.stringify({
  missed_calls_monthly: 77,
  monthly_lost_revenue: 16362,
  annual_lost_revenue: 196350,
  recoverable_revenue: 13090,
  monthly_roi: "70x",
  calls_saved_monthly: 62,
  jobs_booked_monthly: 19,
  benchmark: { industry: "HVAC", avg_miss_rate: 28, their_miss_rate: 30, percentile_rank: 65 }
}));
window.location.href = "/report";
```

Verify: dark dashboard with green recovery number, 3 metric cards, benchmark bar, 12-month chart, green demo CTA.

- [ ] **Step 3: Commit**

```bash
git add app/report/
git commit -m "feat: build full report dashboard with metrics, benchmark, and projection chart"
```

---

## Task 11: Deploy to Vercel

**Files:** None (Vercel dashboard or CLI)

- [ ] **Step 1: Push to GitHub**

```bash
cd /Users/michaelrodriguez/Documents/GitHub/missed-call-calculator
gh repo create missed-call-calculator --private --source=. --push
```

- [ ] **Step 2: Deploy to Vercel**

```bash
npx vercel --prod
```

Or connect the GitHub repo in the Vercel dashboard. Set environment variable:

```
NEXT_PUBLIC_API_URL=https://<paperclip-railway-url>
```

- [ ] **Step 3: End-to-end test**

1. Open the Vercel URL
2. Click "Calculate My Lost Revenue"
3. Select HVAC → 60 calls/week → 30% miss → $850 avg
4. Verify preview shows ~77 missed calls, ~$16,362/month
5. Enter test info and submit
6. Verify report dashboard renders with all metrics
7. Check GHL — test contact created with `lead-magnet-calculator` tag
8. Check Instantly — test lead created in nurture campaign

- [ ] **Step 4: Commit any fixes from testing**

```bash
git add .
git commit -m "fix: adjustments from end-to-end testing"
```

---

## Task 12: Wire Up Custom Domain

**Files:** None (DNS + Vercel settings)

- [ ] **Step 1: Add custom domain in Vercel**

In Vercel project settings → Domains → add `calculator.theaiphoneguy.ai` (or configure path `theaiphoneguy.ai/calculator`).

- [ ] **Step 2: Update CORS origins in Paperclip**

Add the production domain to the CORS allow_origins list in `app.py` if not already covered.

- [ ] **Step 3: Update Instantly nurture emails**

Update any hardcoded calculator URLs in the nurture email sequence to point to the production domain.

- [ ] **Step 4: Final production test**

Run through the full flow on the production domain. Verify GHL contact + Instantly lead creation. Verify demo booking link works.
