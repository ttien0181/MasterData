import ClusterWorkspace from '@/components/ClusterWorkspace';
export default async function Page({ searchParams }: { searchParams: Promise<{ cluster?: string }> }) { return <ClusterWorkspace initialClusterId={(await searchParams).cluster} />; }
