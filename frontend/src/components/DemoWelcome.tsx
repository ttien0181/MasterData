'use client';
import Link from 'next/link';
import {useState} from 'react';
import {startProductDemo} from '@/lib/api';
import {useAppUser} from './AppSession';
import MoMark from './MoMark';
import EmbeddingOption from './EmbeddingOption';
import {SampleFiles} from './SampleLibrary';
import styles from './DemoExperience.module.css';

export default function DemoWelcome(){
 const user=useAppUser(),[busy,setBusy]=useState(false),[error,setError]=useState(''),[useEmbedding,setUseEmbedding]=useState(false);
 async function start(){if(busy)return;setBusy(true);setError('');try{const result=await startProductDemo(useEmbedding);window.location.assign(`/app/sessions/${encodeURIComponent(result.run_id)}/data`);}catch(e){setError(e instanceof Error?e.message:'Chưa bắt đầu được demo. Bạn thử lại nhé.');setBusy(false);}}
 if(!user.demo)return <main className="workflow-main"><h1>Trải nghiệm sản phẩm</h1><p>Chọn “Demo sản phẩm” ở màn hình đăng nhập để bắt đầu một phiên riêng.</p><Link href="/login">Về đăng nhập</Link></main>;
 return <main className={styles.welcome}><section className={styles.welcomeCard}><MoMark size={80}/><span className={styles.badge}>TRẢI NGHIỆM CÓ HƯỚNG DẪN</span><h1>Một khách hàng,<br/>nhiều nguồn thông tin.</h1><p>Khám phá cách đối chiếu và tập hợp thông tin khách hàng từ <strong>4 nguồn · 2.000 hồ sơ giả lập</strong>. Không cần chuẩn bị dữ liệu hay biết nghiệp vụ trước.</p><ol className={styles.journey}><li>Kiểm tra dữ liệu</li><li>Đối chiếu hồ sơ</li><li>Chọn thông tin đại diện</li><li>Xuất CSV</li></ol><p>Master Bot và các gợi ý trên màn hình sẽ đồng hành cùng bạn. Bạn có thể bỏ qua hướng dẫn và tự do khám phá.</p>{!user.demo.run_id&&<div data-demo="demo-embedding"><EmbeddingOption checked={useEmbedding} disabled={busy} onChange={setUseEmbedding}/></div>}{error&&<p role="alert">{error}</p>}<button data-demo="demo-start" className={styles.primary} disabled={busy} onClick={()=>void start()}>{busy?'Đang chuẩn bị dữ liệu mẫu…':user.demo.run_id?'Tiếp tục trải nghiệm':'Bắt đầu trải nghiệm'} <span aria-hidden="true">→</span></button><small className={styles.note}>Bạn sẽ làm việc trong phiên riêng, không ảnh hưởng đến người khác. Phiên demo không hỗ trợ tải thêm dữ liệu.</small></section><details className={styles.sampleDetails}><summary>Xem và tải dữ liệu mẫu</summary><SampleFiles/></details></main>;
}
