// Browser replacement for edu_bridge.py. Provider secrets remain on the server.
const messages={unavailable:'Could not reach Edu. Try connecting again.',disconnected:'Your session expired. Connect again to continue.',quota:'The free limit has been reached. Please wait before sending again.',invalid_input:'Write a question of up to 1200 characters.',invalid_output:'Edu could not prepare a reply. Please try again.',busy:'Please wait for the current reply.',disabled:'Edu is not available right now.',configuration:'The administrator needs to check Edu settings.',forbidden:'This site is not yet allowed to connect to Edu.'};
class Failure extends Error{constructor(code,status=503,retry=0){super(messages[code]||messages.unavailable);this.code=code;this.status=status;this.retry=retry;}}
export class EduBrowserClient{
  constructor({origin,fetcher=(...args)=>globalThis.fetch(...args),now=()=>performance.now()/1000}={}){
    this.origin=(origin||globalThis.RoverWebConfig?.eduOrigin||'https://edu-rover.roverchatbot248.workers.dev').replace(/\/$/,'');
    this.fetcher=fetcher;this.now=now;this.token='';this.until=0;this.retry=0;this.history=[];this.busy=false;this.closed=false;this.controllers=new Set();this.connecting=null;
  }
  status(){return {state:this.token?'connected':'disconnected',connected:!!this.token,message:this.token?'Connected. You can chat with Edu.':messages.disconnected,retryAfter:Math.max(0,Math.ceil(this.retry-this.now()))};}
  async remote(path,body,authenticated=false){
    if(this.closed)throw new Failure('disconnected');
    const controller=new AbortController();this.controllers.add(controller);const timer=setTimeout(()=>controller.abort(),25000);
    try{
      const response=await this.fetcher(this.origin+path,{method:body===undefined?'GET':'POST',mode:'cors',credentials:'omit',redirect:'error',signal:controller.signal,headers:{...(body===undefined?{}:{'Content-Type':'application/json'}),...(authenticated?{Authorization:'Bearer '+this.token}:{})},...(body===undefined?{}:{body:JSON.stringify(body)})});
      const reader=response.body.getReader(),parts=[];let count=0;
      try{while(true){const part=await reader.read();if(part.done)break;count+=part.value.length;if(count>32768){await reader.cancel();throw new Failure('unavailable');}parts.push(part.value);}}finally{reader.releaseLock();}
      const raw=new Uint8Array(count);let at=0;for(const part of parts){raw.set(part,at);at+=part.length;}
      const value=JSON.parse(new TextDecoder().decode(raw));
      if(!response.ok){
        if(response.status===401){this.token='';throw new Failure('disconnected',401);}
        if(response.status===429){const retry=Math.min(86400,Math.max(1,Number(response.headers.get('Retry-After'))||60));this.retry=this.now()+retry;throw new Failure('quota',429,retry);}
        throw new Failure(Object.hasOwn(messages,value.error)?value.error:'unavailable',response.status);
      }
      return value;
    }catch(error){if(error instanceof Failure)throw error;throw new Failure('unavailable');}
    finally{clearTimeout(timer);this.controllers.delete(controller);}
  }
  async ensure(){
    if(this.token && this.now()<this.until)return;
    this.token='';if(this.retry>this.now())throw new Failure('quota',429,Math.ceil(this.retry-this.now()));
    if(!this.connecting)this.connecting=(async()=>{
      const result=await this.remote('/v1/web/session',{});
      if(!/^[a-f0-9]{64}$/.test(result.token||'') || !Number.isInteger(result.expiresIn) || result.expiresIn<=0 || result.expiresIn>3600)throw new Failure('invalid_output');
      this.token=result.token;this.until=this.now()+result.expiresIn;
    })().finally(()=>{this.connecting=null;});
    await this.connecting;
  }
  async request(path,json='{}'){
    try{
      if(path==='/history/clear'){this.history=[];return {...this.status(),cleared:true};}
      if(path==='/check'){
        const health=await this.remote('/health');if(health.service!=='edu' || health.configured!==true)throw new Failure('disabled');
        await this.ensure();return this.status();
      }
      if(path!=='/chat')throw new Failure('invalid_input',400);
      if(this.busy)throw new Failure('busy',409);
      if(this.retry>this.now())throw new Failure('quota',429,Math.ceil(this.retry-this.now()));
      const input=JSON.parse(json),message=input.message?.trim();
      if(typeof message!=='string' || !message || message.length>1200)throw new Failure('invalid_input',400);
      const context=input.context||{};
      for(const [key,value] of Object.entries(context)){
        if(['speedMps','knownWaypoints','roverNumber'].includes(key)){if(typeof value!=='number' || !Number.isFinite(value) || value<0 || value>100000)throw new Failure('invalid_input',400);}
        else if(['manualControl','searchWaypoints','lidarVisible'].includes(key)){if(typeof value!=='boolean')throw new Failure('invalid_input',400);}
        else throw new Failure('invalid_input',400);
      }
      this.busy=true;
      try{
        await this.ensure();const payload={message,context,history:[...this.history]};
        while(payload.history.length && new TextEncoder().encode(JSON.stringify(payload)).length>8000)payload.history.shift();
        let result;
        try{result=await this.remote('/v1/chat',payload,true);}catch(error){if(error.status!==401)throw error;await this.ensure();result=await this.remote('/v1/chat',payload,true);}
        if(typeof result.answer!=='string' || !result.answer.trim() || result.answer.length>2200 || typeof result.refused!=='boolean' || !Array.isArray(result.sources)||result.sources.length>4)throw new Failure('invalid_output',502);
        for(const source of result.sources)for(const key of ['id','title','source'])if(typeof source[key]!=='string' || source[key].length>240)throw new Failure('invalid_output',502);
        if(!result.refused){this.history.push({question:message,answer:result.answer});this.history=this.history.slice(-2);}
        return result;
      }finally{this.busy=false;}
    }catch(error){const failure=error instanceof Failure?error:new Failure('invalid_input',400);return {...this.status(),error:failure.code,message:failure.message,retryAfter:failure.retry};}
  }
  dispose(){this.closed=true;this.token='';this.history=[];for(const controller of this.controllers)controller.abort();this.controllers.clear();}
}
