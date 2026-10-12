import SessionWorkspace from '@/components/SessionWorkspace';
export default async function Layout({ children, params }: { children: React.ReactNode; params: Promise<{ runId: string }> }) { const { runId } = await params; return <SessionWorkspace key={runId} runId={runId}>{children}</SessionWorkspace>; }
