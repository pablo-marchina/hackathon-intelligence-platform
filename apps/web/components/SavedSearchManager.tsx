'use client';

import {useState} from 'react';
import {browserApiBase} from '../lib/api';
import type {AlertChannelPreferences, SavedSearch} from '../lib/types';

const csrf = () => decodeURIComponent(
  document.cookie.split('; ').find(v => v.startsWith('hip_csrf='))?.slice('hip_csrf='.length) ?? ''
);

export function SavedSearchManager({initial, initialPreferences}: {
  initial: SavedSearch[];
  initialPreferences: AlertChannelPreferences | null;
}) {
  const [items, setItems] = useState(initial);
  const [preferences, setPreferences] = useState(initialPreferences);
  const [busyChannel, setBusyChannel] = useState<string | null>(null);
  const [message, setMessage] = useState('');
  const [channelMessage, setChannelMessage] = useState('');

  async function create(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    let filters: unknown;
    try {
      filters = JSON.parse(String(f.get('filters') || '{}'));
      if (!filters || Array.isArray(filters) || typeof filters !== 'object') {
        throw new Error('expected an object');
      }
    } catch {
      setMessage('Filters must be a JSON object.');
      return;
    }
    try {
      const r = await fetch(`${browserApiBase()}/v1/me/saved-searches`, {
        method: 'POST', credentials: 'include',
        headers: {'content-type': 'application/json', 'x-csrf-token': csrf()},
        body: JSON.stringify({
          name: f.get('name'), original_query: f.get('original_query') || null,
          filters, notifications_enabled: f.get('notifications_enabled') === 'on',
        }),
      });
      if (!r.ok) {
        setMessage(r.status === 422
          ? 'Invalid filters: check field names, values, and date formats.'
          : `Save failed (${r.status}).`);
        return;
      }
      const item = await r.json() as SavedSearch;
      setItems(previous => [item, ...previous]);
      setMessage('Saved. Notifications follow your channel preferences below.');
    } catch {
      setMessage('Unable to reach the API. Try again.');
    }
  }

  async function toggle(item: SavedSearch) {
    try {
      const r = await fetch(`${browserApiBase()}/v1/me/saved-searches/${item.id}`, {
        method: 'PATCH', credentials: 'include',
        headers: {'content-type': 'application/json', 'x-csrf-token': csrf()},
        body: JSON.stringify({notifications_enabled: !item.notifications_enabled}),
      });
      if (!r.ok) { setMessage(`Could not update search (${r.status}).`); return; }
      const updated = await r.json() as SavedSearch;
      setItems(previous => previous.map(x => x.id === item.id ? updated : x));
      setMessage('Saved search updated.');
    } catch {
      setMessage('Unable to reach the API. Try again.');
    }
  }

  async function updateChannel(channel: 'IN_APP' | 'EMAIL', enabled: boolean) {
    if (busyChannel || !preferences) return;
    setBusyChannel(channel);
    setChannelMessage('');
    try {
      const response = await fetch(`${browserApiBase()}/v1/me/alert-preferences`, {
        method: 'PUT', credentials: 'include',
        headers: {'content-type': 'application/json', 'x-csrf-token': csrf()},
        body: JSON.stringify({channel, alert_type: 'new_match', enabled}),
      });
      if (!response.ok) throw new Error(`Update failed (${response.status}).`);
      // Read the effective state back. In particular, email consent also
      // requires an address on the account, not merely a checked box.
      const current = await fetch(`${browserApiBase()}/v1/me/alert-preferences`, {
        credentials: 'include', cache: 'no-store',
      });
      if (!current.ok) throw new Error(`Could not refresh preferences (${current.status}).`);
      const next = await current.json() as AlertChannelPreferences;
      setPreferences(next);
      setChannelMessage(channel === 'EMAIL' && enabled && !next.email_enabled
        ? 'An email address is required before email notifications can be enabled.'
        : 'Notification preferences saved.');
    } catch (error) {
      setChannelMessage(error instanceof Error ? error.message : 'Could not save preferences.');
    } finally {
      setBusyChannel(null);
    }
  }

  return <>
    <section className="notification-settings" aria-label="New match notifications">
      <h2>New match notifications</h2>
      <p className="muted">Choose how to receive alerts for enabled saved searches. Email is off until you explicitly enable it, and requires an account email address.</p>
      {preferences ? <div className="notification-channels">
        <label className="inline-check"><input type="checkbox"
          checked={preferences.in_app_enabled} disabled={busyChannel !== null}
          onChange={event => void updateChannel('IN_APP', event.target.checked)}/>
          In-app alerts
        </label>
        <label className="inline-check"><input type="checkbox"
          checked={preferences.email_enabled} disabled={busyChannel !== null}
          onChange={event => void updateChannel('EMAIL', event.target.checked)}/>
          Email alerts (opt-in)
        </label>
      </div> : <p className="warning">Notification preferences could not be loaded. Changes are unavailable.</p>}
      <p aria-live="polite" className="muted">{channelMessage}</p>
    </section>
    <form className="saved-search-form" onSubmit={create}>
      <div className="form-grid"><label>Name<input name="name" required/></label>
      <label>Original query<input name="original_query"/></label></div>
      <label>Normalized filters JSON
        <textarea name="filters" defaultValue={'{"status":["UPCOMING"]}'}/>
      </label>
      <p className="muted">Use fields from the public search API. Unknown fields and pagination settings are rejected.</p>
      <label className="inline-check"><input type="checkbox" name="notifications_enabled" defaultChecked/> Enable notifications for this search</label>
      <button type="submit">Save search</button><p aria-live="polite" className="muted">{message}</p>
    </form>
    <div className="data-list">{items.map(item => <article className="event-card" key={item.id}>
      <strong>{item.name}</strong><pre>{JSON.stringify(item.filters, null, 2)}</pre>
      <p className="muted">Equivalent saved searches: {item.duplicate_count}</p>
      <button onClick={() => void toggle(item)}>{item.notifications_enabled ? 'Disable notifications' : 'Enable notifications'}</button>
    </article>)}</div>
  </>;
}
