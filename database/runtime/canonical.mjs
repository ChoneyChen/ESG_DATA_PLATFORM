// Canonical plain JSON for interface envelopes (no floating-point measurements).
import {createHash} from 'node:crypto';
const keyOrder=(a,b)=>{
  const left=Array.from(a,letter=>letter.codePointAt(0)),right=Array.from(b,letter=>letter.codePointAt(0));
  for(let i=0;i<Math.min(left.length,right.length);i++)if(left[i]!==right[i])return left[i]-right[i];
  return left.length-right.length;
};
export function canonical(value){
  if(Array.isArray(value))return '['+value.map(canonical).join(',')+']';
  if(value&&typeof value==='object')return '{'+Object.keys(value).filter(key=>value[key]!==undefined).sort(keyOrder)
    .map(key=>JSON.stringify(key)+':'+canonical(value[key])).join(',')+'}';
  return JSON.stringify(value);
}
export const digest=value=>createHash('sha256').update(canonical(value)+'\n').digest('hex');
export const fault=(code,message)=>Object.assign(new Error(message),{code});
export const encodeCursor=(query,after)=>Buffer.from(JSON.stringify({query,after})).toString('base64url');
export function cursorAfter(cursor,query){
  if(!cursor)return '';
  try{const value=JSON.parse(Buffer.from(cursor,'base64url'));if(value.query!==query||typeof value.after!=='string')throw new Error();return value.after;}
  catch{throw fault('invalid_cursor','Cursor belongs to another product query');}
}
