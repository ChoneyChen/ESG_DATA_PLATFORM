import {docRequest,targetedRequest} from "./api.js";

const escape=value=>String(value??"").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;");
const array=(payload,...keys)=>Array.isArray(payload)?payload:keys.flatMap(key=>payload?.[key]||[]);
const write=(request,path,reason)=>request(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({reason})},30000);
const stateLabel=state=>({active:"可使用",archived:"已归档",discarded:"已丢弃",quarantined:"回收区保留中",moving:"回收操作待恢复",restoring:"恢复操作待完成",rollback_pending:"文件已回滚，保留权限待释放",reserving:"清理权限申请待确认",reservation_pending:"文件未移动，保留权限待释放",restored:"已恢复",rolled_back:"操作已回滚",purged:"已彻底清除"})[state]||"已归档";
let host;

async function refresh() {
  if(!host)return;
  const content=host.querySelector("[data-lifecycle-content]");
  content.innerHTML="<p class='muted'>正在读取归档与回收记录…</p>";
  const sources=[
    ["报告归档",docRequest,"/api/report-assets-history",payload=>array(payload,"items").map(row=>({id:row.asset_id,label:row.file_name,endpoint:`/api/report-assets/${row.asset_id}/restore`}))],
    ["报告会话归档",docRequest,"/api/report-sessions?include_archived=true",payload=>array(payload,"items").filter(row=>row.lifecycle?.state&&row.lifecycle.state!=="active").map(row=>({id:row.session_id||row.plan_id,label:row.name||row.session_id,state:row.lifecycle.state,endpoint:`/api/report-sessions/${row.session_id||row.plan_id}/restore`}))],
    ["提取记录归档",targetedRequest,"/api/jobs?include_archived=true",payload=>array(payload,"items").filter(row=>row.lifecycle?.state&&row.lifecycle.state!=="active"&&!row.request?.report_session_id).map(row=>({id:row.job_id,label:row.job_id,state:row.lifecycle.state,endpoint:`/api/jobs/${row.job_id}/restore`}))],
    ["队列归档",docRequest,"/api/pipeline/history",payload=>array(payload,"items","tasks").filter(row=>row.archived_at||row.lifecycle?.state==="archived").map(row=>({id:row.task_id,label:row.stage_label||row.message||row.task_id,endpoint:`/api/pipeline/tasks/${row.task_id}/restore`}))],
    ["报告来源回收区",docRequest,"/api/storage/trash",payload=>array(payload,"items","asset_items").map(row=>({...row,id:row.cleanup_id,label:row.metadata?.asset?.file_name||row.metadata?.selected_runs?.map(run=>run.run_id).join("、")||row.cleanup_id,endpoint:`/api/storage/trash/${row.cleanup_id}/restore`,purge:`/api/storage/trash/${row.cleanup_id}/purge`}))],
    ["提取成果回收区",targetedRequest,"/api/lifecycle/trash",payload=>array(payload,"items").map(row=>({...row,id:row.cleanup_id,label:row.metadata?.identity||row.cleanup_id,endpoint:`/api/lifecycle/trash/${row.cleanup_id}/restore`,purge:`/api/lifecycle/trash/${row.cleanup_id}/purge`}))],
  ];
  const results=await Promise.allSettled(sources.map(async([title,request,path,select])=>({title,request,rows:select(await request(path))})));
  content.innerHTML=results.map((result,index)=>{
    if(result.status==="rejected")return `<div class="deletion-message warning"><strong>${escape(sources[index][0])}</strong>：${escape(result.reason.message)}</div>`;
    const {title,rows}=result.value;
    return `<details ${rows.length?"open":""}><summary>${escape(title)} · ${rows.length} 条</summary>${rows.map((row,rowIndex)=>{
      const recoverable=!row.state||["archived","discarded","quarantined","moving","restoring","rollback_pending","reserving","reservation_pending"].includes(row.state);
      const purgeReady=row.state==="quarantined"&&Date.now()>=Date.parse(row.purge_after);
      return `<div class="report-asset-item"><div><strong>${escape(row.label)}</strong><p class="muted">${escape(stateLabel(row.state))}${row.purge_after?` · 保留至 ${escape(new Date(row.purge_after).toLocaleString("zh-CN"))}`:""}</p>${row.reason?`<p>${escape(row.reason)}</p>`:""}</div><div class="button-row">${recoverable?`<button type="button" class="secondary" data-lifecycle-source="${index}" data-lifecycle-row="${rowIndex}" data-lifecycle-op="restore">恢复</button>`:""}${row.purge?`<button type="button" class="danger" data-lifecycle-source="${index}" data-lifecycle-row="${rowIndex}" data-lifecycle-op="purge" ${purgeReady?"":"disabled"} title="保留期结束后仍需重新核查来源依赖">彻底清除</button>`:""}</div></div>`;
    }).join("")||"<p class='muted'>暂无记录。</p>"}</details>`;
  }).join("");
  content.querySelectorAll("[data-lifecycle-op]").forEach(button=>button.addEventListener("click",async()=>{
    const result=results[Number(button.dataset.lifecycleSource)];if(result.status!=="fulfilled")return;
    const source=result.value,row=source.rows[Number(button.dataset.lifecycleRow)],purge=button.dataset.lifecycleOp==="purge";
    if(purge&&!window.confirm("保留期已结束。此操作会永久清除回收文件，不能恢复；仍会保留审计记录。继续？"))return;
    const reason=window.prompt(purge?"请填写彻底清除原因":"请填写恢复原因",purge?"":"恢复继续使用");if(!reason?.trim())return;
    button.disabled=true;
    try{await write(source.request,purge?row.purge:row.endpoint,reason.trim());await refresh();window.dispatchEvent(new CustomEvent("esg:queue-changed"));}
    catch(error){host.querySelector("[data-lifecycle-message]").textContent=error.message;button.disabled=false;}
  }));
}

export function initLifecycle() {
  const anchor=document.querySelector("[data-app-view='settings-cache']");
  if(!anchor||document.querySelector("#lifecycle-management"))return;
  host=document.createElement("section");host.id="lifecycle-management";host.className="card";host.dataset.appView="settings-cache";
  host.innerHTML=`<div class="section-heading"><div><h2>归档与回收区</h2><p class="muted">归档保留全部来源；清理先进入回收区，保留7天。已入库或被任务引用的来源不能清理。</p></div><button type="button" class="secondary" data-lifecycle-refresh>刷新记录</button></div><p data-lifecycle-message role="status"></p><div data-lifecycle-content><p class="muted">点击刷新查看可恢复记录。</p></div>`;
  anchor.after(host);host.querySelector("[data-lifecycle-refresh]").addEventListener("click",refresh);
  window.addEventListener("esg:navigate",event=>{if(event.detail?.view==="settings")refresh();});
}
