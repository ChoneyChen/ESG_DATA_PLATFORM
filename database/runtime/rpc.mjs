// Transport only. Domain workers supply their own command allowlist.
import {createInterface} from 'node:readline';

export async function serve(db,dispatch) {
  const protocolWrite=process.stdout.write.bind(process.stdout);
  try {
    for await(const line of createInterface({input:process.stdin,crlfDelay:Infinity})) {
      let request;
      try {
        request=JSON.parse(line);
        const result=await dispatch(request.method,request.params||{});
        protocolWrite(JSON.stringify({id:request.id,result})+'\n');
      } catch(error) {
        protocolWrite(JSON.stringify({id:request?.id,error:{code:error.code||'database_error',message:error.message}})+'\n');
      }
    }
  } finally {await db.close();}
}
