'use client';
import {useEffect,useRef,useState} from 'react';
import styles from './MasterBot.module.css';
const important=/(tỷ lệ|đầy đủ thông tin|chất lượng dữ liệu|hồ sơ trùng|đã có quyết định|chưa có nhóm|đã có nhóm|có cảnh báo|số hồ sơ giảm|bước \d+|golden record|hồ sơ đại diện|hồ sơ khách hàng|hồ sơ nguồn|cặp hồ sơ|cùng một khách hàng|chọn tất cả|đồng ý gộp|không gộp|xem chi tiết|đối chiếu|xuất csv|csv|xuất dữ liệu|chốt dữ liệu|tải lên|thông tin đại diện|thông tin liên hệ|giữ lại|đã lưu|chưa lưu|lịch sử xử lý|quy định|kiểm tra|dữ liệu đã thay đổi|thử lại|đang trả lời|họ tên|điện thoại|số điện thoại|email|địa chỉ|ngày sinh|giấy tờ(?: định danh)?|trùng khớp(?: chính xác)?|khác nhau|mâu thuẫn|chưa xác minh|chưa xác nhận|cần kiểm tra|cần đối chiếu|thiếu dữ liệu|[\w.+-]+@[\w.-]+\.[a-z]{2,}|\+?\d[\d .-]{7,}\d|\d+(?:[.,]\d+)*%?)/giu;
export function HighlightedText({text}:{text:string}) {
 const parts=[];let start=0;
 for(const match of text.matchAll(important)) {
  const index=match.index!;
  parts.push(text.slice(start,index),<mark className={styles.highlight} key={index}>{match[0]}</mark>);
  start=index+match[0].length;
 }
 parts.push(text.slice(start));return <>{parts}</>;
}
export default function MasterBotText({text,onComplete}:{text:string;onComplete?:()=>void}) {
 const completed=useRef(false),done=useRef(onComplete);done.current=onComplete;
 const [visible,setVisible]=useState('');
 useEffect(()=>{completed.current=false;
  setVisible(previous=>text.startsWith(previous)?previous:'');
  const timer=setInterval(()=>setVisible(previous=>{
   const base=text.startsWith(previous)?previous:'';
   const next=base+Array.from(text.slice(base.length)).slice(0,2).join('');
   if(next===text)clearInterval(timer);
   return next;
  }),32);
  return ()=>clearInterval(timer);
 },[text]);
 useEffect(()=>{if(text&&visible===text&&!completed.current){completed.current=true;done.current?.();}},[text,visible]);
 return <p className={styles.reply}><HighlightedText text={text.startsWith(visible)?visible:''}/></p>;
}
