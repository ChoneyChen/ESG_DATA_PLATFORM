import {type Row} from '../api';
import {deletionBlockers,managementActions,managementPath,recordActions,type ManagedKind} from '../management-api';
import {useResource} from '../state';
import {Actions} from './ui';

/** The owning backend supplies real DELETE commands and their current blockers. */
export function RecordManagement({record,kind,id,compact=false}:{record:Row,kind:ManagedKind,id:string,compact?:boolean}){
 const advertised=recordActions(record);
 const description=useResource('document',kind==='ir'&&!advertised.some(action=>action.action==='delete')?`${managementPath(kind,id)}/deletion`:null,true);
 const current=description.data?{...record,...description.data}:record;
 const actions=managementActions(recordActions(current),kind,id);
 const deletion=actions.find(action=>action.action==='delete');
 const blockers=deletion?.disabled_reason?[deletion.disabled_reason]:deletionBlockers(current);
 return <div className={`record-management ${compact?'compact':''}`}>
  <Actions actions={actions} revision={record.lifecycle?.revision ?? record.revision}/>
  {(deletion?.enabled===false||!deletion)&&<small className="table-secondary">{blockers.join('；') || '当前记录暂不可删除'}</small>}
 </div>;
}
