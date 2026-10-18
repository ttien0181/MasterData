'use client';
import styles from './EmbeddingOption.module.css';

export default function EmbeddingOption({checked,disabled=false,onChange}:{checked:boolean;disabled?:boolean;onChange:(value:boolean)=>void}){
 return <label className={styles.option}>
  <input type="checkbox" checked={checked} disabled={disabled} onChange={event=>onChange(event.target.checked)}/>
  <span><strong>So sánh tên và địa chỉ bằng AI (Embedding)</strong>
   <small>{checked?'Bổ sung so sánh tên và địa chỉ gần giống nhau. Thời gian xử lý sẽ lâu hơn.':'Xử lý nhanh theo thông tin nghiệp vụ. Master Bot vẫn có thể giải thích kết quả.'}</small>
  </span>
 </label>;
}
