// Sends changed SQLite pages, never a whole growing archive on every frame.
// SQL and its schema run in Unity unchanged. The worker receives exact committed
// file bytes; it does not approximate cells or independently reimplement writes.
const hooks=new WeakMap();
export function observeFiles(FS){
  if(hooks.has(FS))return hooks.get(FS);
  const watchers=new Map(),write=FS.write;
  FS.write=function(stream,buffer,offset,length,position,canOwn){
    const start=position===undefined?stream.position:position;
    const count=write.call(this,stream,buffer,offset,length,position,canOwn);
    if(watchers.size && count>0){
      const watched=watchers.get(FS.getPath(stream.node));
      if(watched)for(const target of watched)for(let p=Math.floor(start/4096);p<=Math.floor((start+count-1)/4096);p++)target.dirty.add(p);
    }
    return count;
  };
  const api={watchers,watch(path){
    const target={path,dirty:new Set(),length:FS.stat(path).size};
    if(!watchers.has(path))watchers.set(path,new Set());watchers.get(path).add(target);return target;
  },remove(target){const set=watchers.get(target.path);set?.delete(target);if(set?.size===0)watchers.delete(target.path);},collect(target){
    const length=FS.stat(target.path).size,pages=[...target.dirty].sort((a,b)=>a-b),parts=[];
    if(!pages.length && length===target.length)return null;
    const stream=FS.open(target.path,'r');
    try{for(let i=0;i<pages.length;i++){
      const first=pages[i];let last=first;while(i+1<pages.length && pages[i+1]===last+1 && last-first<63)last=pages[++i];
      const offset=first*4096,count=Math.min(length-offset,(last-first+1)*4096);if(count<=0)continue;
      const bytes=new Uint8Array(count);FS.read(stream,bytes,0,count,offset);parts.push({offset,bytes});
    }}finally{FS.close(stream);}
    target.dirty.clear();target.length=length;return {path:target.path,length,parts};
  }};hooks.set(FS,api);return api;
}
export function download(bytes,name,mime='application/octet-stream'){
  const url=URL.createObjectURL(new Blob([bytes],{type:mime}));const anchor=document.createElement('a');anchor.href=url;anchor.download=name;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),60000);
}
export function createViewer({FS,base,livePath,databasePath,mode,format='rgba',onFrame,onStatus,workerFactory=url=>new Worker(url,{type:'module'})}){
  const observer=observeFiles(FS),watched=databasePath?observer.watch(databasePath):null;
  let closed=false,busy=true,commands=[];
  const worker=workerFactory(new URL('viewer-worker.mjs',base));
  const snapshot=()=>{try{return FS.readFile(livePath);}catch{return null;}};
  const initial={type:'init',mode,format,livePath,snapshot:mode==='algorithms'?null:snapshot(),database:watched?{path:databasePath,bytes:FS.readFile(databasePath)}:null};
  worker.onmessage=({data})=>{
    if(closed)return;
    if(data.type==='status')onStatus(data.message,!!data.failed);
    if(data.type==='frame')onFrame(data);
    if(data.type==='ready' || data.type==='ack')busy=false;
    if(data.type==='download')download(data.bytes,data.name,'application/zip');
  };
  worker.onerror=error=>{busy=false;onStatus('The viewer could not start: '+error.message,true);};
  const transfer=[];if(initial.database)transfer.push(initial.database.bytes.buffer);if(initial.snapshot)transfer.push(initial.snapshot.buffer);
  worker.postMessage(initial,transfer);
  return {send(command,motion=false){
    if(closed)return;
    if(motion && commands.at(-1)?.type==='move')commands[commands.length-1]=command;
    else if(commands.length<128)commands.push(command);
  },poll(){
    if(closed || busy)return;
    const update={type:'packet',livePath,commands,snapshot:mode==='algorithms'?null:snapshot(),database:watched?observer.collect(watched):null};
    const transfer=[];if(update.snapshot)transfer.push(update.snapshot.buffer);if(update.database)for(const part of update.database.parts)transfer.push(part.bytes.buffer);
    busy=true;commands=[];worker.postMessage(update,transfer);
  },dispose(){if(closed)return;closed=true;if(watched)observer.remove(watched);worker.terminate();}};
}
