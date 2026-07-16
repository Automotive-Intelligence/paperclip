import { ReactNode, useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { Sidebar, Menu, MenuItem } from 'react-pro-sidebar';
import { api, OpsFleet } from '../lib/api';

type NavItem = {
  label: string;
  to: string;
  icon: string;
};

// Honest fallback fleet size, matching the live Pit Wall registry in app.py
// (_pitwall_team_ids + PITWALL_AGENT_META). Used only until the live count
// loads (or if the API is unreachable) so the sidebar never shows a made-up
// number. The live value from /api/pitwall/ops-dashboard overrides this.
const FALLBACK_FLEET: OpsFleet = { rivers: 5, agents: 23 };

const NAV_ITEMS: NavItem[] = [
  { label: 'Overview', to: '/', icon: 'O' },
  { label: 'AI Phone Guy', to: '/team/aiphoneguy', icon: 'P' },
  { label: 'Worship Digital', to: '/team/callingdigital', icon: 'C' },
  { label: 'Automotive Intel', to: '/team/autointelligence', icon: 'A' },
  { label: 'Agent Empire', to: '/team/agentempire', icon: 'E' },
  { label: 'CustomerAdvocate', to: '/team/customeradvocate', icon: 'V' },
];

export default function DashboardShell({ children }: { children: ReactNode }) {
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const [fleet, setFleet] = useState<OpsFleet>(FALLBACK_FLEET);

  useEffect(() => {
    let active = true;
    api
      .opsDashboard()
      .then((data) => {
        if (active && data.fleet) setFleet(data.fleet);
      })
      .catch(() => {
        // Keep the honest fallback; never show a fabricated number.
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <div className="flex min-h-screen bg-pitbg">
      <Sidebar
        collapsed={collapsed}
        backgroundColor="rgb(10, 15, 20)"
        rootStyles={{
          borderRight: '1px solid rgb(31, 41, 55)',
          color: 'rgb(229, 231, 235)',
        }}
      >
        <div className="flex items-center justify-between px-4 py-5 border-b border-pitborder">
          {!collapsed && (
            <div>
              <div className="text-base font-bold tracking-wider text-pittext">AVO</div>
              <div className="text-[10px] uppercase tracking-wider text-pitmuted">Pit Wall</div>
            </div>
          )}
          <button
            type="button"
            onClick={() => setCollapsed((c) => !c)}
            className="rounded-md border border-pitborder px-2 py-1 text-xs text-pitmuted hover:border-pitgreen/60 hover:text-pittext"
            aria-label="Toggle sidebar"
          >
            {collapsed ? '›' : '‹'}
          </button>
        </div>
        <Menu
          menuItemStyles={{
            button: ({ active }) => ({
              backgroundColor: active ? 'rgba(16, 185, 129, 0.1)' : 'transparent',
              color: active ? 'rgb(16, 185, 129)' : 'rgb(156, 163, 175)',
              borderLeft: active ? '2px solid rgb(16, 185, 129)' : '2px solid transparent',
              '&:hover': {
                backgroundColor: 'rgba(16, 185, 129, 0.05)',
                color: 'rgb(229, 231, 235)',
              },
            }),
          }}
        >
          {NAV_ITEMS.map((item) => {
            const active =
              item.to === '/'
                ? location.pathname === '/' || location.pathname === '/dashboard' || location.pathname === '/pit-wall'
                : location.pathname.startsWith(item.to);
            return (
              <MenuItem
                key={item.to}
                active={active}
                component={<Link to={item.to} />}
                icon={
                  <span className="inline-flex h-6 w-6 items-center justify-center rounded border border-pitborder text-xs font-bold">
                    {item.icon}
                  </span>
                }
              >
                {item.label}
              </MenuItem>
            );
          })}
        </Menu>
        {!collapsed && (
          <div className="mt-auto border-t border-pitborder px-4 py-3 text-[10px] uppercase tracking-wider text-pitmuted">
            <div>North Star</div>
            <div className="text-pittext normal-case tracking-normal text-xs mt-1">
              20+ recurring clients on every car
            </div>
            <div className="normal-case tracking-normal text-[11px] mt-1">MRR today: $0</div>
            <div className="mt-2">
              {fleet.rivers} Rivers · {fleet.agents} Agents
            </div>
          </div>
        )}
      </Sidebar>
      <main className="flex-1 overflow-x-hidden">{children}</main>
    </div>
  );
}
