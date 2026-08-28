import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { api, PartnerComms } from '../lib/api';

// Michael asked the only question that matters about a new channel: "where do I see
// this?" A channel whose answer is "curl an admin endpoint" is a channel that goes
// unread, which is how the flag protocol quietly accumulated 38 unopened messages.
// So the mailbox gets a place in the surface he already opens, and he can send from
// here rather than through a terminal.

function ago(iso: string): string {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso.slice(0, 16);
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 48) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

const KIND_TONE: Record<string, string> = {
  work: 'text-pitmuted border-pitborder',
  action: 'text-pitamber border-pitamber/40',
  note: 'text-pitgreen border-pitgreen/40',
};

export default function PartnerPage() {
  const [data, setData] = useState<PartnerComms | null>(null);
  const [error, setError] = useState('');
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState('');

  const load = () =>
    api
      .partnerComms()
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));

  useEffect(() => {
    load();
  }, []);

  async function send() {
    const body = draft.trim();
    if (!body || sending) return;
    setSending(true);
    setSendError('');
    try {
      await api.sendPartnerNote(body);
      setDraft('');
      await load();
    } catch (e) {
      setSendError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
    }
  }

  if (error) {
    return (
      <div className="p-6">
        <div className="rounded-lg border border-pitred/40 bg-pitred/10 p-4 text-sm text-pitred">
          Could not load the partner channel: {error}
        </div>
      </div>
    );
  }
  if (!data) return <div className="p-6 text-sm text-pitfaint">Reading the channel…</div>;

  const live = data.keys.filter((k) => k.status === 'active');
  const unreadOut = data.notes.filter((n) => n.direction === 'to_partner' && !n.read).length;

  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="space-y-5 p-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-pittext">Ryan</h1>
        <p className="text-sm text-pitfaint">
          The partner channel, both directions. Notes wait in his agent's inbox whether
          or not it is running, so nothing is lost while he is offline.
        </p>
      </header>

      {/* Connection state first: a mailbox is only real if the key on the other end is
          live. A revoked or unused key means messages sent here go nowhere. */}
      <div className="rounded-lg border border-pitborder bg-pitsunk/60 p-4">
        <div className="mb-2 text-[10px] uppercase tracking-wider text-pitfaint">Connection</div>
        {live.length === 0 ? (
          <p className="text-sm text-pitred">
            No active key. Anything sent here will sit unread until a key is issued.
          </p>
        ) : (
          live.map((k) => (
            <div key={k.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm">
              <span className="font-mono text-pittext">{k.label}</span>
              <span className="rounded border border-pitgreen/40 px-1.5 py-0.5 text-[10px] uppercase text-pitgreen">
                {k.scope} scope
              </span>
              <span className="text-xs text-pitmuted tabular-nums">
                {k.use_count.toLocaleString()} calls
              </span>
              <span className="text-xs text-pitfaint">
                last seen {k.last_used_at ? ago(k.last_used_at) : 'never'}
              </span>
            </div>
          ))
        )}
      </div>

      {/* Compose */}
      <div className="rounded-lg border border-pitborder p-4">
        <div className="mb-2 text-[10px] uppercase tracking-wider text-pitfaint">
          Send Ryan a note
        </div>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={3}
          placeholder="He sees this the next time his agent polls."
          className="w-full resize-y rounded-md border border-pitborder bg-pitbg px-3 py-2 text-sm text-pittext placeholder:text-pitfaint focus:border-pitgreen/60 focus:outline-none"
        />
        <div className="mt-2 flex items-center gap-3">
          <button
            type="button"
            onClick={send}
            disabled={!draft.trim() || sending}
            className="rounded-md bg-pittext px-3 py-1.5 text-xs font-medium text-pitinvert disabled:opacity-40"
          >
            {sending ? 'Sending…' : 'Send'}
          </button>
          {sendError && <span className="text-xs text-pitred">{sendError}</span>}
          {unreadOut > 0 && (
            <span className="text-xs text-pitfaint">
              {unreadOut} sent, not yet picked up
            </span>
          )}
        </div>
      </div>

      {/* Mailbox */}
      <div className="space-y-2">
        <div className="text-[10px] uppercase tracking-wider text-pitfaint">Messages</div>
        {data.notes.length === 0 && (
          <p className="text-sm text-pitfaint">Nothing sent either way yet.</p>
        )}
        {data.notes.map((n) => {
          const mine = n.direction === 'to_partner';
          return (
            <div
              key={n.id}
              className={`rounded-lg border p-3 ${
                mine ? 'border-pitborder bg-pitsunk/40' : 'border-pitgreen/40 bg-pitgreen/10'
              }`}
            >
              <div className="mb-1 flex flex-wrap items-center gap-2 text-[10px] uppercase tracking-wider">
                <span className={mine ? 'text-pitmuted' : 'text-pitgreen'}>
                  {mine ? `you → ${'ryan'}` : `${n.from} → you`}
                </span>
                <span className="text-pitfaint normal-case tracking-normal">{ago(n.when)}</span>
                {mine && (
                  <span className={n.read ? 'text-pitfaint' : 'text-pitamber'}>
                    {n.read ? 'picked up' : 'not picked up yet'}
                  </span>
                )}
              </div>
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-pittext">{n.body}</p>
            </div>
          );
        })}
      </div>

      {/* What he can see about us */}
      <div className="space-y-2">
        <div className="text-[10px] uppercase tracking-wider text-pitfaint">
          What his agent sees when it polls
        </div>
        <div className="overflow-x-auto rounded-lg border border-pitborder">
          <table className="w-full text-left text-xs">
            <tbody>
              {data.activity.items.map((it, i) => (
                <tr key={i} className="border-t border-pitborder first:border-t-0">
                  <td className="px-3 py-2 w-20">
                    <span className={`rounded border px-1.5 py-0.5 text-[10px] ${KIND_TONE[it.kind] ?? 'text-pitfaint border-pitborder'}`}>
                      {it.kind}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-pittext">{it.what}</td>
                  <td className="px-3 py-2 w-24 text-right tabular-nums text-pitfaint">
                    {ago(it.when)}
                  </td>
                </tr>
              ))}
              {data.activity.items.length === 0 && (
                <tr>
                  <td className="px-3 py-6 text-center text-pitfaint">Nothing in the window.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {data.activity.sources_failed.length > 0 && (
          <p className="text-[11px] text-pitamber">
            Could not read: {data.activity.sources_failed.join(', ')}. This feed is
            incomplete, which is not the same as quiet.
          </p>
        )}
      </div>
    </motion.div>
  );
}
