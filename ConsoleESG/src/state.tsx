import {createContext,useCallback,useContext,useEffect,useRef,useState,type ReactNode} from 'react';
import {credentials,readCached,request,type Row,type Service} from './api';
const Context=createContext({epoch:0,auth:0,refresh:()=>{},authChanged:()=>{},connected:false});
export function WorkspaceProvider({children}:{children:ReactNode}){
  const [epoch,setEpoch]=useState(0),[auth,setAuth]=useState(0),[connected,setConnected]=useState(false);
  const refresh=useCallback(()=>setEpoch(x=>x+1),[]),authChanged=useCallback(()=>{setAuth(x=>x+1);setEpoch(x=>x+1);},[]);
  // No credential is pasted: the data platform recognises a request from this
  // machine as the local operator. Probe once, and again if it is not up yet.
  useEffect(()=>{let cancelled=false,timer:ReturnType<typeof setTimeout>|undefined;
    // A caller that supplied a token (scripts, tests, remote use) already has
    // an identity; only the browser workbench needs discovery.
    if(credentials.token)return;
    const probe=()=>{request<Row>('publication','/internal/v1/me').then(me=>{
      if(cancelled)return;
      if(credentials.token)return;
      if(!me||typeof me.operator_ref!=='string'||!Array.isArray(me.roles)){timer=setTimeout(probe,15000);return;}
      credentials.setLocal(me.operator_ref,me.roles.filter((role:unknown):role is string=>typeof role==='string'));
      setAuth(x=>x+1);
    }).catch(()=>{if(!cancelled)timer=setTimeout(probe,15000);});};
    probe();
    return()=>{cancelled=true;clearTimeout(timer);};
  },[]);
  useEffect(()=>{let cursor=0,timer:ReturnType<typeof setTimeout>|undefined,stream:EventSource|undefined,disposed=false;
    const changed=()=>{clearTimeout(timer);timer=setTimeout(refresh,350);};
    const open=()=>{
      if(disposed||stream||document.visibilityState==='hidden')return;
      // Start from the current head: a fresh page needs live changes, not a
      // replay of every event the queue has ever written.
      stream=new EventSource(`/document/api/pipeline/events/stream?after=${cursor}&latest=1`);
      stream.onopen=()=>setConnected(true);
      stream.onerror=()=>{setConnected(false);stream?.close();stream=undefined;clearTimeout(timer);timer=setTimeout(open,5000);};
      const receive=(event:MessageEvent)=>{if(event.lastEventId)cursor=Math.max(cursor,Number(event.lastEventId)||0);changed();};
      stream.onmessage=receive;
      stream.addEventListener('execution',receive as EventListener);
      stream.addEventListener('task',receive as EventListener);
    };
    const onVisibility=()=>{
      if(document.visibilityState==='hidden'){stream?.close();stream=undefined;setConnected(false);}
      else open();
    };
    open();
    document.addEventListener('visibilitychange',onVisibility);
    const fallback=setInterval(()=>{if(document.visibilityState==='visible')refresh();},10000);
    return()=>{disposed=true;document.removeEventListener('visibilitychange',onVisibility);stream?.close();clearTimeout(timer);clearInterval(fallback);};
  },[refresh]);
  return <Context.Provider value={{epoch,auth,refresh,authChanged,connected}}>{children}</Context.Provider>;
}
export const useWorkspace=()=>useContext(Context);
export function useResource<T=Row>(service:Service,path:string|null,authenticated=false){
  const {epoch,auth}=useWorkspace();const key=`${service}:${path}:${auth}:${authenticated}`;const enabled=!!path&&(!authenticated||credentials.ready);
  const [state,setState]=useState<{key:string,data?:T,error:string,loading:boolean}>({key,error:'',loading:enabled});
  const pending=useRef('');
  const retry=useRef<ReturnType<typeof setTimeout>|undefined>(undefined);
  const failures=useRef(0);
  const load=useCallback(()=>{
    if(!path||(authenticated&&!credentials.ready))return;
    // One request at a time per resource: a poll, an event or a re-render must
    // never restart a request that is still on its way.
    if(pending.current===key)return;
    pending.current=key;
    clearTimeout(retry.current);
    setState(previous=>({key,data:previous.key===key?previous.data:undefined,error:'',loading:true}));
    const mine=key;
    readCached<T>(service,path)
      .then(value=>{if(pending.current!==mine)return;pending.current='';failures.current=0;setState({key,data:value,error:'',loading:false});})
      .catch(e=>{if(pending.current!==mine)return;pending.current='';setState(previous=>({key,data:previous.key===key?previous.data:undefined,error:e.message,loading:false}));
        // Only transient failures recover on their own. A missing resource or a
        // permission error is deterministic; retrying it forever hides the cause.
        const status=Number((e as {status?:number}).status) || 0;
        if((status===0||status>=500)&&failures.current<5){
          failures.current+=1;
          retry.current=setTimeout(load,Math.min(30000,2000*2**failures.current));
        }});
  },[service,path,key,authenticated]);
  useEffect(()=>{load();return()=>clearTimeout(retry.current);},[load]);
  useEffect(()=>{if(epoch>0){failures.current=0;load();}},[epoch]);
  return {data:enabled&&state.key===key?state.data:undefined,error:enabled&&state.key===key?state.error:'',loading:enabled&&state.loading,retry:load};
}
