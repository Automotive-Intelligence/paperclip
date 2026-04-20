export type Business = {
  key: string;
  name: string;
  domain: string;
  hostname: string;
  accentClass: string;
  accentBgClass: string;
  numberBgClass: string;
  email: string;
  steps: { title: string; body: string }[];
};

export const businesses: Business[] = [
  {
    key: "ai",
    name: "Automotive Intelligence",
    domain: "automotiveintelligence.io",
    hostname: "book.automotiveintelligence.io",
    accentClass: "text-blue-600",
    accentBgClass: "bg-blue-50",
    numberBgClass: "bg-blue-600",
    email: "michael@automotiveintelligence.io",
    steps: [
      {
        title: "We audit your dealership",
        body: "Before the call, we pull public data on your store, ad-spend signals, and tech-stack footprint. Insights specific to your rooftop, not generic slides.",
      },
      {
        title: "We map your AI opportunity",
        body: "On the call, we walk through where AI actually moves the needle: showroom capture, service follow-up, lost-lead recovery. No fluff.",
      },
      {
        title: "You leave with a plan",
        body: "Fit or not, you walk away with an AI readiness scorecard and 3 plays you can run this quarter.",
      },
    ],
  },
  {
    key: "cd",
    name: "Calling Digital",
    domain: "calling.digital",
    hostname: "book.calling.digital",
    accentClass: "text-slate-700",
    accentBgClass: "bg-slate-50",
    numberBgClass: "bg-slate-800",
    email: "michael@calling.digital",
    steps: [
      {
        title: "We audit your competitors",
        body: "Before the call, we map what your top 3 competitors are running for outbound and paid — subject lines, offers, ad copy.",
      },
      {
        title: "We map the gap",
        body: "On the call, we show the specific plays they're running that you're not, and what it'll take to flip that.",
      },
      {
        title: "You leave with a battle plan",
        body: "Fit or not, you leave with a one-page competitive brief you can run with this week.",
      },
    ],
  },
  {
    key: "apg",
    name: "The AI Phone Guy",
    domain: "theaiphoneguy.ai",
    hostname: "book.theaiphoneguy.ai",
    accentClass: "text-cyan-600",
    accentBgClass: "bg-cyan-50",
    numberBgClass: "bg-cyan-600",
    email: "info@theaiphoneguy.ai",
    steps: [
      {
        title: "We pull your missed-call data",
        body: "Before the call, we estimate your miss rate and lost revenue based on your category and volume.",
      },
      {
        title: "We demo the AI agent live",
        body: "On the call, you'll hear your AI agent handle a real scenario — quoting, booking, triaging. Actual voice, not slides.",
      },
      {
        title: "You leave with a number",
        body: "Exact monthly cost, setup timeline, projected recovery. Yes or no, no chasing.",
      },
    ],
  },
];

export function resolveBusiness(hostname: string): Business | null {
  const match = businesses.find((b) => hostname === b.hostname);
  if (match) return match;
  const key = new URLSearchParams(window.location.search).get("b");
  if (key) {
    const preview = businesses.find((b) => b.key === key);
    if (preview) return preview;
  }
  return null;
}
