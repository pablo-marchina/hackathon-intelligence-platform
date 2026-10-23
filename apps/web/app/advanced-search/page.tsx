import type {Metadata} from 'next';
import {AdvancedSearch} from '../../components/AdvancedSearch';

export const metadata:Metadata={
  title:'Advanced search',
  description:'Find hackathons using composable filters across dates, eligibility, technologies and sources.',
};

export default function AdvancedSearchPage(){
  return <main>
    <section className="section">
      <p className="eyebrow">Global hackathon discovery</p>
      <h1>Advanced search</h1>
      <p className="lead">Filter by any supported attribute, using combinations of AND, OR and NOT.</p>
    </section>
    <section className="section"><AdvancedSearch/></section>
  </main>;
}
