import UploadWorkspace from '@/components/UploadWorkspace';
export default async function Page({ params }: { params: Promise<{ uploadSessionId: string }> }) { return <UploadWorkspace uploadSessionId={(await params).uploadSessionId} />; }
