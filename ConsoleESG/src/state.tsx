import {createContext,useCallback,useContext,useEffect,useState,type ReactNode} from 'react';
import {credentials,request,type Row,type Service} from './api';
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
  useEffect(()=>{let cursor=0,timer:ReturnType<typeof setTimeout>|undefined,stream:EventSource|undefined;
    const changed=()=>{clearTimeout(timer);timer=setTimeout(refresh,350);};
    const connect=()=>{stream=new EventSource(`/document/api/pipeline/events/stream?after=${cursor}`);stream.onopen=()=>setConnected(true);stream.onerror=()=>setConnected(false);const receive=(event:MessageEvent)=>{if(event.lastEventId)cursor=Math.max(cursor,Number(event.lastEventId)||0);changed();};stream.onmessage=receive;stream.addEventListener('execution',receive as EventListener);stream.addEventListener('task',receive as EventListener);};
    connect();const fallback=setInterval(()=>{if(document.visibilityState==='visible')refresh();},10000);
    return()=>{stream?.close();clearTimeout(timer);clearInterval(fallback);};
  },[refresh]);
  return <Context.Provider value={{epoch,auth,refresh,authChanged,connected}}>{children}</Context.Provider>;
}
export const useWorkspace=()=>useContext(Context);
export function useResource<T=Row>(service:Service,path:string|null,authenticated=false){
  const {epoch,auth}=useWorkspace();const key=`${service}:${path}:${auth}:${authenticated}`;const enabled=!!path&&(!authenticated||credentials.ready);const [state,setState]=useState<{key:string,data?:T,error:string,loading:boolean}>({key:'',error:'',loading:false});
  useEffect(()=>{if(!path || authenticated && !credentials.ready){setState({key,error:'',loading:false});return;}const controller=new AbortController();setState(previous=>({key,data:previous.key===key?previous.data:undefined,error:'',loading:true}));
    request<T>(service,path,{signal:controller.signal}).then(value=>{if(!controller.signal.aborted)setState({key,data:value,error:'',loading:false});}).catch(e=>{if(!controller.signal.aborted)setState(previous=>({key,data:previous.key===key?previous.data:undefined,error:e.message,loading:false}));});return()=>controller.abort();
  },[service,path,epoch,auth,authenticated,key]);return {data:enabled&&state.key===key?state.data:undefined,error:enabled&&state.key===key?state.error:'',loading:enabled&&(state.key!==key||state.loading)};
}
