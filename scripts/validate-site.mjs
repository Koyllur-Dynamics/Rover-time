import fs from 'node:fs';
import {verifyLargeFiles} from './large-files.mjs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
export function httpsOrigin(value){
 const u=new URL(value);if(u.protocol!=='https:'||u.username||u.password||u.search||u.hash||u.pathname!=='/'||u.port)throw Error('Se requiere un origen HTTPS sin ruta, credenciales ni puerto.');
 if(u.hostname==='localhost'||u.hostname.endsWith('.invalid')||/^\d+\.\d+\.\d+\.\d+$/.test(u.hostname))throw Error('El origen debe ser público.');return u.origin;
}
export function parsePublicConfig(text){
 if(text.length>16384)throw Error('Configuración pública demasiado grande.');
 const match=text.match(/(?:^|\n)window\.RoverWebConfig=(\{[^\n]*\});\s*$/);
 if(!match)throw Error('Formato de configuración pública inválido.');return JSON.parse(match[1]);
}
export function hash(file){return createHash('sha256').update(fs.readFileSync(file)).digest('hex');}
export function list(dir,prefix=''){
 return fs.readdirSync(dir,{withFileTypes:true}).flatMap(e=>{const relative=prefix+e.name,p=path.join(dir,e.name);if(e.isSymbolicLink())throw Error('No se publican enlaces: '+relative);return e.isDirectory()?list(p,relative+'/'):[relative];}).sort();
}
export function validate(base,{requireReady=false,repository=process.env.GITHUB_REPOSITORY}={}){
 const site=path.join(base,'site'),meta=JSON.parse(fs.readFileSync(path.join(base,'publication.json'),'utf8'));
 const manifest=JSON.parse(fs.readFileSync(path.join(base,'manifest.json'),'utf8'));
 const actual=list(site);if(JSON.stringify(actual)!==JSON.stringify(Object.keys(manifest.files).sort()))throw Error('Archivos inesperados o ausentes en site/.');
 let total=0;for(const name of actual){const file=path.join(site,name),size=fs.statSync(file).size;if(size>100*1024*1024)throw Error('Archivo supera 100 MiB: '+name);total+=size;if(/(?:gsk_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)/.test(fs.readFileSync(file).toString('latin1')))throw Error('Posible credencial privada en '+name);if(hash(file)!==manifest.files[name].sha256)throw Error('Contenido cambiado desde la preparación: '+name);}
 verifyLargeFiles(site,manifest);
 const logical=[...actual,...Object.keys(manifest.largeFiles||{})];
 if(total>1_000_000_000)throw Error('El sitio supera 1 GB.');
 for(const name of ['index.html','rover-config.js','.nojekyll','Build/Web.data.unityweb','Build/Web.wasm.unityweb','Build/Web.loader.js','StreamingAssets/RoverWeb/viewer-worker.mjs','StreamingAssets/RoverWeb/room-client.mjs','StreamingAssets/RoverWeb/edu-client.mjs','StreamingAssets/RoverWeb/pyodide/pyodide.asm.wasm','StreamingAssets/RoverWeb/python/rover_web_viewer.py'])if(!logical.includes(name))throw Error('Falta '+name);
 const lock=JSON.parse(fs.readFileSync(path.join(site,'StreamingAssets/RoverWeb/pyodide/pyodide-lock.json'),'utf8'));
 for(const file of actual.filter(n=>n.endsWith('.whl'))){const entry=Object.values(lock.packages).find(p=>p.file_name===path.basename(file));if(!entry||hash(path.join(site,file))!==entry.sha256)throw Error('Dependencia Python distinta del lock: '+file);}
 const c=parsePublicConfig(fs.readFileSync(path.join(site,'rover-config.js'),'utf8')),origin=httpsOrigin(meta.workerOrigin);
 if(c?.eduOrigin!==origin||c?.lunarProxy!==origin+'/v1/lunar'||c?.roomRelay!==origin+'/v1/rooms')throw Error('Faltan servicios o las direcciones no coinciden.');
 if(requireReady){if(!meta.githubOwner||!meta.siteUrl)throw Error('Falta GitHubOwner: ejecutar Tools/prepare_web_publish.ps1 -GitHubOwner TU_USUARIO.');if(repository&&repository.toLowerCase()!==(meta.githubOwner+'/'+meta.repository).toLowerCase())throw Error('El repositorio actual no coincide con publication.json.');}
 return {files:actual.length,bytes:total,siteUrl:meta.siteUrl,workerOrigin:origin,ready:!!meta.githubOwner};
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url))console.log(JSON.stringify(validate(path.resolve(process.argv[2]||'.'),{requireReady:process.argv.includes('--require-ready')}),null,2));
