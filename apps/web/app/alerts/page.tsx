import {cookies} from 'next/headers';
import {apiBase} from '../../lib/api';
import {AlertInbox} from '../../components/AlertInbox';
import type {AlertEvent} from '../../lib/types';

export default async function AlertsPage() {
  const c = await cookies();
  const response = await fetch(`${apiBase()}/v1/me/alerts?limit=50`, {
    headers: {cookie: c.toString()}, cache: 'no-store',
  }).catch(() => null);
  if (!response || response.status === 401) {
    return <main><h1>Sign in to view alerts.</h1></main>;
  }
  if (!response.ok) {
    return <main><div className="outage">Alerts could not be loaded.</div></main>;
  }
  return <main><p className="eyebrow">Inbox</p><h1>Material changes, not noise.</h1>
    <AlertInbox initial={await response.json() as AlertEvent[]}/>
  </main>;
}
