'use client';

import {useState} from 'react';
import Link from 'next/link';
import {browserApiBase} from '../lib/api';
import type {AlertEvent} from '../lib/types';

const csrf = () => decodeURIComponent(
  document.cookie.split('; ').find(v => v.startsWith('hip_csrf='))?.slice('hip_csrf='.length) ?? ''
);

export function AlertInbox({initial}: {initial: AlertEvent[]}) {
  const [items, setItems] = useState(initial);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState('');

  async function markRead(id: string) {
    if (pending !== null) return;
    setPending(id);
    setError('');
    try {
      const response = await fetch(`${browserApiBase()}/v1/me/alerts/${id}/read`, {
        method: 'PATCH', credentials: 'include',
        headers: {'x-csrf-token': csrf()},
      });
      if (!response.ok) throw new Error(`Could not mark alert read (${response.status}).`);
      setItems(previous => previous.map(item => item.id === id
        ? {...item, read_at: new Date().toISOString()} : item));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unable to reach the API.');
    } finally {
      setPending(null);
    }
  }

  return <>
    <p className="muted" aria-live="polite">{error}</p>
    {!items.length && <div className="empty-state">No alerts yet. Enable a saved search to monitor new hackathons.</div>}
    <div className="data-list">{items.map(alert => <article
      className={`event-card ${alert.read_at ? '' : 'unread-alert'}`} key={alert.id}>
      <div className="card-topline"><strong>{alert.alert_type.replace(/_/g, ' ')}</strong>
        <span>{alert.read_at ? 'Read' : 'Unread'}</span></div>
      <h2>{alert.hackathon_name ?? 'Hackathon update'}</h2>
      <p className="muted">{new Date(alert.created_at).toLocaleString()}</p>
      <div className="alert-actions">
        {alert.hackathon_slug && <Link className="button" href={`/hackathons/${alert.hackathon_slug}`}>
          Open event
        </Link>}
        {!alert.read_at && <button disabled={pending !== null}
          onClick={() => void markRead(alert.id)}>
          {pending === alert.id ? 'Saving…' : 'Mark as read'}
        </button>}
      </div>
    </article>)}</div>
  </>;
}
