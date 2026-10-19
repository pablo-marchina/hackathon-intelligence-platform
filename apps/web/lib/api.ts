import type {HackathonDetail,HackathonPage} from './types';
export const apiBase=()=> (process.env.API_URL??process.env.NEXT_PUBLIC_API_URL??'http://localhost:8000').replace(/\/$/,'');
export const browserApiBase=()=> (process.env.NEXT_PUBLIC_API_URL??'http://localhost:8000').replace(/\/$/,'');
export class ApiUnavailableError extends Error{}
async function apiFetch<T>(path:string,init?:RequestInit&{next?:{revalidate?:number}}):Promise<T>{let response:Response;try{response=await fetch(`${apiBase()}${path}`,{...init,next:init?.next??{revalidate:60}})}catch(e){throw new ApiUnavailableError(e instanceof Error?e.message:'API unavailable')}if(!response.ok){if(response.status>=500)throw new ApiUnavailableError(`API returned ${response.status}`);throw new Error(`API request failed: ${response.status}`)}return response.json() as Promise<T>}
export const searchHackathons=(q:URLSearchParams)=>apiFetch<HackathonPage>(`/v1/hackathons?${q.toString()}`);
export const getHackathon=(slug:string)=>apiFetch<HackathonDetail>(`/v1/hackathons/${encodeURIComponent(slug)}`);
export const getOrganization=<T=unknown>(slug:string)=>apiFetch<T>(`/v1/organizations/${encodeURIComponent(slug)}`);
export const getSeries=<T=unknown>(slug:string)=>apiFetch<T>(`/v1/series/${encodeURIComponent(slug)}`);
