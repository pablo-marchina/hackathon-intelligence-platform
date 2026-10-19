import {cookies} from 'next/headers';
import {apiBase} from '../../lib/api';
import {SavedSearchManager} from '../../components/SavedSearchManager';
import type {AlertChannelPreferences, SavedSearch} from '../../lib/types';

export default async function SavedSearchesPage() {
  const c = await cookies();
  const headers = {cookie: c.toString()};
  const [searchesResponse, preferencesResponse] = await Promise.all([
    fetch(`${apiBase()}/v1/me/saved-searches`, {headers, cache: 'no-store'}).catch(() => null),
    fetch(`${apiBase()}/v1/me/alert-preferences`, {headers, cache: 'no-store'}).catch(() => null),
  ]);
  if (!searchesResponse || searchesResponse.status === 401) {
    return <main><h1>Sign in to save searches.</h1></main>;
  }
  if (!searchesResponse.ok) {
    return <main><div className="outage">Saved searches could not be loaded.</div></main>;
  }
  const searches = await searchesResponse.json() as SavedSearch[];
  const preferences = preferencesResponse?.ok
    ? await preferencesResponse.json() as AlertChannelPreferences : null;
  return <main><p className="eyebrow">Saved searches</p>
    <h1>Turn structured filters into persistent monitors.</h1>
    <SavedSearchManager initial={searches} initialPreferences={preferences}/>
  </main>;
}
