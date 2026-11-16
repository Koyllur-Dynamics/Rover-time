import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
const limit=100*1024*1024, chunkSize=48*1024*1024;
const digest=data=>createHash('sha256').update(data).digest('hex');
export function splitLargeFiles(site,files){
 const largeFiles={};
 for(const name of files){
  const file=path.join(site,name);
  if(fs.statSync(file).size<=limit)continue;
  const data=fs.readFileSync(file),parts=[];
  for(let offset=0;offset<data.length;offset+=chunkSize){
   const part=name+'.part'+String(parts.length).padStart(3,'0');
   if(fs.existsSync(path.join(site,part)))throw Error('Parte ya existente: '+part);
   fs.writeFileSync(path.join(site,part),data.subarray(offset,offset+chunkSize));parts.push(part);
  }
  largeFiles[name]={bytes:data.length,sha256:digest(data),parts};
  fs.unlinkSync(file);
 }
 return largeFiles;
}
export function verifyLargeFiles(site,manifest){
 const used=new Set();
 for(const [name,entry] of Object.entries(manifest.largeFiles||{})){
  if(!/^Build\/[A-Za-z0-9._-]+$/.test(name)||manifest.files[name]||fs.existsSync(path.join(site,name)))throw Error('Destino de reconstrucción inválido: '+name);
  if(!Array.isArray(entry.parts)||entry.parts.length<2)throw Error('Partes inválidas: '+name);
  const hash=createHash('sha256');let bytes=0;
  for(const [i,part] of entry.parts.entries()){
   if(part!==name+'.part'+String(i).padStart(3,'0')||used.has(part)||!manifest.files[part])throw Error('Parte inválida: '+part);
   used.add(part);const data=fs.readFileSync(path.join(site,part));bytes+=data.length;hash.update(data);
  }
  if(bytes!==entry.bytes||hash.digest('hex')!==entry.sha256)throw Error('Reconstrucción alterada: '+name);
 }
}
export function restoreLargeFiles(base){
 const site=path.join(base,'site'),manifest=JSON.parse(fs.readFileSync(path.join(base,'manifest.json'),'utf8'));
 verifyLargeFiles(site,manifest);
 for(const [name,entry] of Object.entries(manifest.largeFiles||{})){
  const file=path.join(site,name),fd=fs.openSync(file,'wx');
  try{for(const part of entry.parts)fs.writeFileSync(fd,fs.readFileSync(path.join(site,part)));}finally{fs.closeSync(fd);}
  if(fs.statSync(file).size!==entry.bytes||digest(fs.readFileSync(file))!==entry.sha256)throw Error('Archivo reconstruido incorrecto: '+name);
  for(const part of entry.parts)fs.unlinkSync(path.join(site,part));
  console.log('Reconstruido y verificado: '+name+' ('+entry.bytes+' bytes)');
 }
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url))restoreLargeFiles(path.resolve(process.argv[2]||'.'));
