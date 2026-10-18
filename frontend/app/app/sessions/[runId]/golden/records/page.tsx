import {redirect} from 'next/navigation';
export default async function Page({params,searchParams}:{params:Promise<{runId:string}>;searchParams:Promise<{cluster?:string}>}){const {runId}=await params,{cluster}=await searchParams;redirect(`/app/sessions/${encodeURIComponent(runId)}/golden${cluster?`?cluster=${encodeURIComponent(cluster)}`:''}`);}
