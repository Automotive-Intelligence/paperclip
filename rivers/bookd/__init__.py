"""Book'd river — 6-agent fleet at full brand parity.

Mirrors rivers/ai_phone_guy/ structure (workflow / sequences / pit_wall),
adapted to Book'd's stack:
  - CRM of record: Twenty (Book'd workspace), business_key="bookd"
    (env TWENTY_BOOKD_URL / TWENTY_BOOKD_API_KEY — see tools/twenty.py)
  - Cold outbound: Instantly (Book'd workspace), meetbookd.com + powerbookd.com
    mailboxes. NEVER the bookd.cx primary domain.

Fleet (see rivers/bookd/workflow.py for run entrypoints):
  marshall (CEO, weekly) · cole (Sales, daily+interval) · hayes (RevOps, interval)
  sutton (Marketing, daily) · quinn (Customer Success, daily) · reid (Intelligence, daily)

Cole + Sutton outbound is HELD until the Book'd mailbox warmup completes
(~2026-07-06). Marshall, Reid, Quinn, and Hayes (reads) run immediately on wire-up.
"""

BUSINESS_KEY = "bookd"
