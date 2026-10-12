import SessionHub from '@/components/SessionHub';
import SessionEntry from '@/components/SessionEntry';
export default async function Page({ searchParams }: { searchParams: Promise<{ run?: string; view?: string }> }) {
  const { run, view } = await searchParams;
  return run ? <SessionEntry runId={run} view={view} /> : <SessionHub />;
}
