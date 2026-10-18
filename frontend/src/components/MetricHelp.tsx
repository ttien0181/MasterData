'use client';
import {useMasterBot} from './MasterBotProvider';
import styles from './MetricHelp.module.css';
export default function MetricHelp({label,message}:{label:string;message:string}){
 const bot=useMasterBot();
 return <button type="button" className={styles.action} aria-label={`Giải thích chỉ số ${label}`} onClick={()=>bot?.explainMetric(message)}/>;
}
