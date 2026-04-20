import type { Business } from "./businesses";

export function ThankYouPage({ business }: { business: Business }) {
  return (
    <div className="min-h-screen bg-white flex flex-col">
      <header className="px-6 py-5 border-b border-gray-100">
        <div className="max-w-3xl mx-auto">
          <span className={`text-sm font-semibold ${business.accentClass}`}>
            {business.name}
          </span>
        </div>
      </header>

      <main className="flex-1 px-6 py-12 md:py-20">
        <div className="max-w-2xl mx-auto">
          <div className="flex flex-col items-center text-center">
            <div className="w-14 h-14 rounded-full bg-green-100 flex items-center justify-center mb-6">
              <svg
                className="w-7 h-7 text-green-600"
                fill="none"
                stroke="currentColor"
                strokeWidth="3"
                viewBox="0 0 24 24"
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <h1 className="text-4xl md:text-5xl font-semibold text-gray-900 tracking-tight">
              You're booked.
            </h1>
            <p className="mt-4 text-lg text-gray-600 max-w-md">
              Check your email for the calendar invite. Here's what happens next:
            </p>
          </div>

          <div className="mt-12 space-y-4">
            {business.steps.map((step, i) => (
              <div
                key={i}
                className={`${business.accentBgClass} rounded-xl p-6 flex gap-5 items-start`}
              >
                <div
                  className={`${business.numberBgClass} text-white font-semibold w-9 h-9 rounded-full flex items-center justify-center shrink-0`}
                >
                  {i + 1}
                </div>
                <div>
                  <h2 className="text-lg font-semibold text-gray-900">
                    {step.title}
                  </h2>
                  <p className="mt-1 text-gray-700 leading-relaxed">{step.body}</p>
                </div>
              </div>
            ))}
          </div>

          <div className="mt-16 text-center text-sm text-gray-500">
            Questions before the call?{" "}
            <a
              href={`mailto:${business.email}`}
              className={`${business.accentClass} font-medium hover:underline`}
            >
              {business.email}
            </a>
          </div>
        </div>
      </main>

      <footer className="px-6 py-6 border-t border-gray-100 text-center text-xs text-gray-400">
        {business.name} · {business.domain}
      </footer>
    </div>
  );
}
