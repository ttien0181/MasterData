import ClusterOverview from '@/components/ClusterOverview';
export default async function Page({ searchParams }: { searchParams: Promise<{ cluster?: string }> }) {
  return <ClusterOverview initialClusterId={(await searchParams).cluster} />;
}
