import { redirect } from 'next/navigation';
import ClusterOverview from '@/components/ClusterOverview';
export default async function Page({ params, searchParams }: { params: Promise<{ runId: string }>; searchParams: Promise<{ cluster?: string }> }) {
  const { cluster } = await searchParams;
  if (cluster) redirect(`/app/sessions/${encodeURIComponent((await params).runId)}/clusters/records?cluster=${encodeURIComponent(cluster)}`);
  return <ClusterOverview />;
}
