import type {Metadata} from 'next';
import {AdminDashboard} from '../../components/AdminDashboard';

export const metadata:Metadata={title:'Operations'};

export default function AdminPage(){
  return <main className="admin-shell">
    <p className="eyebrow">Operations · W6</p>
    <h1>Source operations</h1>
    <p className="lead">Review quality signals, pause unreliable sources, triage issues and inspect the audit trail.</p>
    <AdminDashboard/>
  </main>;
}
