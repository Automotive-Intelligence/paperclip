import { businesses } from "./businesses";

export function IndexPage() {
  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center p-6">
      <div className="max-w-lg w-full bg-white rounded-2xl shadow-sm p-8">
        <h1 className="text-2xl font-semibold text-gray-900">
          Thank You Pages — Preview
        </h1>
        <p className="mt-2 text-gray-600">
          Production uses hostname-based routing. Preview each page here:
        </p>
        <div className="mt-6 space-y-3">
          {businesses.map((b) => (
            <a
              key={b.key}
              href={`/?b=${b.key}`}
              className="block p-4 rounded-xl border border-gray-200 hover:border-gray-300 hover:bg-gray-50 transition"
            >
              <div className={`text-sm font-semibold ${b.accentClass}`}>
                {b.name}
              </div>
              <div className="text-xs text-gray-500 mt-1">{b.hostname}</div>
            </a>
          ))}
        </div>
      </div>
    </div>
  );
}
