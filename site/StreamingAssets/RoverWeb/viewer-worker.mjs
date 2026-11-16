// A worker owns Pyodide, SQLite's read replica and Agg. No Python installation.
import {loadPyodide} from './pyodide/pyodide.mjs';
let outputFormat='rgba',py,adapter,ready=false,chain=Promise.resolve();
function status(message,failed=false){postMessage({type:'status',message,failed});}
function writeFile(path,data){py.FS.mkdirTree(path.slice(0,path.lastIndexOf('/'))||'/');py.FS.writeFile(path,data);}
function bytes(proxy){const value=proxy?.toJs?proxy.toJs():proxy;const result=new Uint8Array(value).slice();proxy?.destroy?.();return result;}
async function init(message){
  outputFormat=message.format||'rgba';
  status('Getting the viewer ready in this browser…');
  py=await loadPyodide({indexURL:new URL('./pyodide/',import.meta.url).href});
  await py.loadPackage(['numpy','matplotlib','sqlite3']);
  for(const name of ['ver_mision_rover.py','ver_algoritmos_rover.py','visor_unity.py','mapa_archivo.py','sector_projection.py','rover_web_viewer.py']){
    const response=await fetch(new URL('python/'+name,import.meta.url));if(!response.ok)throw Error('Could not find '+name);
    writeFile('/rover/'+name,new Uint8Array(await response.arrayBuffer()));
  }
  if(message.database)writeFile(message.database.path,message.database.bytes);
  if(message.snapshot)writeFile(message.livePath,message.snapshot);
  py.runPython("import sys; sys.path.insert(0, '/rover'); import rover_web_viewer");
  adapter=py.pyimport('rover_web_viewer');adapter.create(message.mode,message.livePath);
  ready=true;postMessage({type:'ready'});status('Viewer connected · running in the browser');
}
function packet(message){
  if(!ready)throw Error('The viewer is not ready yet.');
  if(message.database){
    adapter.database_will_change();
    const change=message.database;const stream=py.FS.open(change.path,'r+');
    try{for(const part of change.parts)py.FS.write(stream,part.bytes,0,part.bytes.length,part.offset);}
    finally{py.FS.close(stream);}
    py.FS.truncate(change.path,change.length);adapter.database_did_change();
  }
  if(message.snapshot)writeFile(message.livePath,message.snapshot);
  for(const command of message.commands||[]){
    const result=adapter.command(JSON.stringify(command));
    if(command.type==='export' && result){const data=bytes(result);postMessage({type:'download',name:'rover-data.zip',bytes:data},[data.buffer]);}
    else result?.destroy?.();
  }
  const result=outputFormat==='png'?adapter.render_png():adapter.render();const values=result.toJs();result.destroy();
  const pixels=bytes(values[2]);
  postMessage({type:'frame',width:values[0],height:values[1],sequence:values[3],pixels},[pixels.buffer]);
  postMessage({type:'ack'});
}
onmessage=event=>{chain=chain.then(()=>event.data.type==='init'?init(event.data):packet(event.data)).catch(error=>{status('The viewer could not update: '+error.message,true);postMessage({type:'ack'});});};
