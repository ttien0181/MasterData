import { redirect } from 'next/navigation';

export default async function Page({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  redirect(`/app/sessions/${encodeURIComponent(runId)}/decisions`);
}
