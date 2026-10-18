import GoldenWorkspace from '@/components/GoldenWorkspace';
export default async function Page({searchParams}:{searchParams:Promise<{cluster?:string}>}){const params=await searchParams;return <GoldenWorkspace initialClusterId={params.cluster}/>;}
