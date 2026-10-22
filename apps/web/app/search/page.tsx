import Link from 'next/link';
import { EventCard } from '../../components/EventCard';
import { NaturalSearch } from '../../components/NaturalSearch';
import { ApiUnavailableError, searchHackathons } from '../../lib/api';

type Params = Record<string, string | string[] | undefined>;
type SearchPageProps = { searchParams: Promise<Params> };
const first = (value: string | string[] | undefined) => Array.isArray(value) ? value[0] ?? '' : value ?? '';

function toQuery(params: Params) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (key === 'cursor') continue; // a new filter always starts on the first page
    if (typeof value === 'string' && value !== '') query.append(key, value);
    else if (Array.isArray(value)) value.filter(Boolean).forEach(item => query.append(key, item));
  }
  return query;
}

const eventTypes = [
  ['hackathon', 'Hackathon'], ['buildathon', 'Buildathon'], ['datathon', 'Datathon'],
  ['game_jam', 'Game jam'], ['ideathon', 'Ideathon'], ['innovation_challenge', 'Innovation challenge'],
  ['ai_challenge', 'AI challenge'], ['data_science_competition', 'Data science'],
  ['coding_competition', 'Coding competition'], ['security_ctf', 'Security CTF'],
  ['hardware_challenge', 'Hardware challenge'], ['startup_challenge', 'Startup challenge'],
  ['open_innovation_challenge', 'Open innovation'], ['designathon', 'Designathon'],
  ['quant_competition', 'Quant competition'], ['robotics_competition', 'Robotics'],
] as const;

const states = [
  ['ANNOUNCED', 'Announced'], ['REGISTRATION_UPCOMING', 'Registration upcoming'],
  ['REGISTRATION_OPEN', 'Registration open'], ['REGISTRATION_CLOSED', 'Registration closed'],
  ['UPCOMING', 'Upcoming'], ['ONGOING', 'Ongoing'], ['SUBMISSIONS_OPEN', 'Submissions open'],
  ['JUDGING', 'Judging'], ['RESULTS_PENDING', 'Results pending'], ['COMPLETED', 'Completed'],
  ['CANCELLED', 'Cancelled'], ['POSTPONED', 'Postponed'], ['UNKNOWN', 'Unknown'],
] as const;

function Options({ values }: { values: readonly (readonly [string, string])[] }) {
  return <>{values.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</>;
}

function Filters({ params }: { params: Params }) {
  const selected = (key: string) => first(params[key]);
  return <aside className="filter-panel" aria-label="Hackathon filters">
    <form className="filter-grid" method="get">
      <h2>Explore hackathons</h2>
      <label>Keywords<input name="q" maxLength={300} placeholder="AI, robotics, fintech…" defaultValue={selected('q')} /></label>
      <fieldset className="search-filter-group">
        <legend>Event and registration</legend>
        <label>Registration state<select name="registration_state" defaultValue={selected('registration_state')}>
          <option value="">Any</option>
          <Options values={[["OPEN", "Open"], ["UPCOMING", "Upcoming"], ["CLOSED", "Closed"], ["UNKNOWN", "Unknown"]]} />
        </select></label>
        <label>Event status<select name="status" defaultValue={selected('status')}><option value="">Any</option><Options values={states} /></select></label>
        <label>Event type<select name="event_type" defaultValue={selected('event_type')}><option value="">Any</option><Options values={eventTypes} /></select></label>
        <label>Format<select name="format" defaultValue={selected('format')}><option value="">Any</option>
          <Options values={[["ONLINE", "Online"], ["IN_PERSON", "In person"], ["HYBRID", "Hybrid"], ["UNKNOWN", "Unknown"]]} />
        </select></label>
      </fieldset>
      <fieldset className="search-filter-group">
        <legend>Location and eligibility</legend>
        <label>Event country (ISO-2)<input name="country_code" placeholder="BR" maxLength={2} defaultValue={selected('country_code')} /></label>
        <label>City<input name="city" maxLength={120} defaultValue={selected('city')} /></label>
        <label>My eligible country (ISO-2)<input name="eligible_country" maxLength={2} placeholder="BR" defaultValue={selected('eligible_country')} /></label>
        <label>Team size<input type="number" name="team_size" min={1} max={1000} defaultValue={selected('team_size')} /></label>
        <label>Student required<select name="student_required" defaultValue={selected('student_required')}>
          <option value="">Any</option><option value="true">Yes</option><option value="false">No</option>
        </select></label>
        <label>Individual participation<select name="individual_allowed" defaultValue={selected('individual_allowed')}>
          <option value="">Any</option><option value="true">Allowed</option><option value="false">Not allowed</option>
        </select></label>
      </fieldset>
      <fieldset className="search-filter-group">
        <legend>Dates and awards</legend>
        <label>Starts after<input name="start_from" type="date" defaultValue={selected('start_from').slice(0, 10)} /></label>
        <label>Starts before<input name="start_to" type="date" defaultValue={selected('start_to').slice(0, 10)} /></label>
        <label>Registration deadline after<input name="deadline_from" type="date" defaultValue={selected('deadline_from').slice(0, 10)} /></label>
        <label>Registration deadline before<input name="deadline_to" type="date" defaultValue={selected('deadline_to').slice(0, 10)} /></label>
        <label>Minimum prize (USD)<input name="min_prize_usd" type="number" min={0} step="any" defaultValue={selected('min_prize_usd')} /></label>
        <label>Maximum prize (USD)<input name="max_prize_usd" type="number" min={0} step="any" defaultValue={selected('max_prize_usd')} /></label>
        <label className="search-inline-check"><input type="checkbox" name="free_only" value="true" defaultChecked={selected('free_only') === 'true'} /> Free participation only</label>
      </fieldset>
      <fieldset className="search-filter-group">
        <legend>Topics and organizations</legend>
        <p className="search-filter-hint">Use canonical slugs as listed on event details.</p>
        <label>Track slug<input name="track" placeholder="ai" defaultValue={selected('track')} /></label>
        <label>Technology slug<input name="technology" placeholder="python" defaultValue={selected('technology')} /></label>
        <label>Organizer slug<input name="organizer" defaultValue={selected('organizer')} /></label>
        <label>Sponsor slug<input name="sponsor" defaultValue={selected('sponsor')} /></label>
      </fieldset>
      <label>Sort by<select name="sort" defaultValue={selected('sort') || 'relevance'}>
        <Options values={[["relevance", "Relevance"], ["deadline", "Registration deadline"], ["start", "Start date"], ["quality", "Data quality"]]} />
      </select></label>
      <button type="submit">Apply filters</button>
      <Link className="secondary button search-reset" href="/search">Clear all filters</Link>
    </form>
  </aside>;
}

export default async function SearchPage({ searchParams }: SearchPageProps) {
  const params = await searchParams;
  const query = toQuery(params);
  // Cursor is a separately submitted continuation of the exact same filters.
  if (first(params.cursor)) query.set('cursor', first(params.cursor));
  let body = null;
  let outage = false;
  try {
    body = await searchHackathons(query);
  } catch (error) {
    if (error instanceof ApiUnavailableError) outage = true;
    else throw error;
  }
  const next = body?.next_cursor ? new URLSearchParams(query) : null;
  if (next && body?.next_cursor) next.set('cursor', body.next_cursor);
  return <main>
    <section>
      <p className="eyebrow">Global discovery</p>
      <h1>Find your next hackathon.</h1>
      <p className="lead">Filter by format, location, eligibility, topics, registration dates, prizes and organizers. Results reflect verified available source data, not every event on the web.</p>
    </section>
    <NaturalSearch />
    <div className="search-shell">
      <Filters params={params} />
      <section aria-label="Search results">
        {outage ? <div className="outage"><strong>Data-source outage. Catalog temporarily unavailable.</strong></div> :
          body && body.items.length === 0 ? <div className="empty-state"><h3>No matching hackathons</h3><p>Try removing some filters; many events do not publish every detail.</p></div> :
          body ? <div className="event-grid">{body.items.map(event => <EventCard key={event.id} event={event} />)}</div> : null}
        {next ? <div className="pagination"><Link href={`/search?${next.toString()}`}>Next page →</Link></div> : null}
      </section>
    </div>
  </main>;
}
