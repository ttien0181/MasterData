import { redirect } from 'next/navigation';
export default async function Page({ params, searchParams }: { params: Promise<{ runId: string }>; searchParams: Promise<{ cluster?: string }> }) {
  const { runId } = await params;
  const { cluster } = await searchParams;
  redirect(`/app/sessions/${encodeURIComponent(runId)}/clusters${cluster ? `?cluster=${encodeURIComponent(cluster)}` : ''}`);
}
