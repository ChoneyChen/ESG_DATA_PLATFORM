import {useState} from 'react';
import {post} from '../api';
import {useWorkspace} from '../state';

/** Apply the evidence-only review policy to one report. Publication stays a separate action. */
export function AutoReviewButton({reportRef,label='自动审核可证项'}:{reportRef:string,label?:string}) {
  const {refresh}=useWorkspace();
  const [busy,setBusy]=useState(false),[message,setMessage]=useState('');
  const run=async()=>{
    setBusy(true);setMessage('');
    try {
      const result=await post('publication','/internal/v1/auto-review',{report_ref:reportRef,apply:true});
      setMessage(`自动批准 ${result.applied ?? 0} 条；${result.needs_human ?? 0} 条保留人工审核。`);
      refresh();
    } catch(error) { setMessage((error as Error).message); }
    finally { setBusy(false); }
  };
  return <span className="auto-review-action">
    <button type="button" className="secondary" disabled={busy||!reportRef} onClick={run}>
      {busy?'自动审核中…':label}
    </button>
    {message&&<small className="auto-review-result">{message}</small>}
  </span>;
}
