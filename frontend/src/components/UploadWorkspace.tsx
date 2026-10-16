"use client";
import { useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { can } from '@/lib/permissions';
import { useAppUser } from './AppSession';
import ResolutionIntake from './ResolutionIntake';
export default function UploadWorkspace({ uploadSessionId }: { uploadSessionId: string }) {
  const user = useAppUser(); const router = useRouter();
  const route = useCallback((id: string) => { if (id !== uploadSessionId) router.replace(`/app/uploads/${encodeURIComponent(id)}`); }, [router, uploadSessionId]);
  if (!can(user, 'create_run')) return <main className="workflow-main"><p>Bạn chưa có quyền upload/tạo phiên.</p></main>;
  return <main className="workflow-main"><h1>Chuẩn bị dữ liệu</h1><ResolutionIntake userId={user.id} workflowVersion={2} initialSessionId={uploadSessionId} onSessionReady={route} onRunCreated={async created => { router.push(`/app/sessions/${encodeURIComponent(created.run_id)}/data`); }} onDismiss={() => router.push('/app')} /></main>;
}
