import GoldenReview from '@/components/GoldenReview';
export default async function Page({searchParams}:{searchParams:Promise<{status?:string;cluster?:string}>}){
 const params=await searchParams;
 return <GoldenReview initialStatus={['review','confirmed','all'].includes(params.status||'')?params.status:undefined} initialClusterId={params.cluster}/>;
}
