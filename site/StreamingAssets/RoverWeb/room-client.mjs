// The relay only routes bounded packets. Unity's host validates commands and owns physics.
export function createRoom({room,host,onEvent,WebSocketClass=WebSocket,config=globalThis.RoverWebConfig||{}}){
  if(!/^[a-f0-9]{64}$/.test(room))throw Error('Invalid room code.');
  if(!config.roomRelay)throw Error('The administrator needs to set up online rooms.');
  const url=new URL(config.roomRelay.replace(/\/$/,'')+'/'+room);url.searchParams.set('role',host?'host':'guest');
  if(url.protocol==='https:')url.protocol='wss:';else if(url.protocol==='http:' && ['localhost','127.0.0.1','[::1]'].includes(url.hostname))url.protocol='ws:';
  if(url.protocol!=='wss:' && !(url.protocol==='ws:'&&['localhost','127.0.0.1','[::1]'].includes(url.hostname)))throw Error('This room needs a secure connection.');
  const socket=new WebSocketClass(url);socket.binaryType='arraybuffer';let closed=false,bytes=0;const queue=[];const decoder=new TextDecoder();
  const fail=message=>{if(closed)return;onEvent({kind:'error',message});close();};
  socket.onmessage=event=>{
    if(closed)return;
    if(typeof event.data==='string'){
      try{if(event.data.length>2048)throw Error();const value=JSON.parse(event.data);if(!['ready','join','leave','error'].includes(value.kind))throw Error();onEvent(value);}catch{fail('Invalid room reply.');}return;
    }
    const packet=new Uint8Array(event.data);if(packet.length<2 || packet.length>4*1024*1024+2){fail('Invalid room data packet.');return;}
    let key=null;
    if(packet[1]===11){
      try{const state=JSON.parse(decoder.decode(packet.subarray(2)));key=packet[0]+':state:'+(state.motionOnly===true?'motion':'world');}
      catch{fail('Invalid room state.');return;}
    }else if(packet[1]===14)key=packet[0]+':heartbeat';
    if(key!==null){for(let i=queue.length-1;i>=0;i--)if(queue[i].replaceKey===key){bytes-=queue[i].length;queue.splice(i,1);}packet.replaceKey=key;}
    // View images are disposable previews, unlike commands and terrain chunks.
    if(packet[1]===15){let images=0;for(let i=queue.length-1;i>=0;i--)if(queue[i][0]===packet[0]&&queue[i][1]===15){if(++images>=4){bytes-=queue[i].length;queue.splice(i,1);}}}
    if(queue.length>=64 || bytes+packet.length>8*1024*1024){fail('The connection cannot keep up with the room. Please connect again.');return;}
    queue.push(packet);bytes+=packet.length;
  };
  socket.onerror=()=>fail('Could not open the online room. Check your connection and server settings.');
  socket.onclose=event=>fail(event.reason||'The room closed. Return to the menu to connect again.');
  function close(){if(closed)return;closed=true;queue.length=0;bytes=0;socket.close(1000,'Saliendo');}
  return {peek:()=>queue[0],read(){const packet=queue.shift();if(packet)bytes-=packet.length;return packet;},
    send(peer,kind,data){if(closed || socket.readyState!==1 || socket.bufferedAmount>256*1024)return false;if(data.length>4*1024*1024)throw Error('The data packet is too large.');const packet=new Uint8Array(data.length+2);packet[0]=peer;packet[1]=kind;packet.set(data,2);socket.send(packet);return true;},
    kick(slot){if(!closed && socket.readyState===1)socket.send(JSON.stringify({kind:'kick',slot}));},close};
}
