export const DEM='https://planetarymaps.usgs.gov/mosaic/Lunar_LRO_LOLA_Global_LDEM_118m_Mar2014.tif';
export const WAC='https://trek.nasa.gov/tiles/Moon/EQ/LRO_WAC_Mosaic_Global_303ppd_v02/1.0.0/default/default028mm/';
export async function lunarFetch({url,start,limit,total,signal,proxy=globalThis.RoverWebConfig?.lunarProxy||'',fetcher=fetch}){
  let target=url,headers={};
  if(start>=0){if(url!==DEM||!Number.isSafeInteger(start)||limit<1||limit>1048576||start+limit>total)throw Error('Invalid lunar data range.');headers.Range=`bytes=${start}-${start+limit-1}`;}
  else if(!url.startsWith(WAC)||!/^\d+\/\d+\/\d+\.jpg$/.test(url.slice(WAC.length)))throw Error('Invalid lunar data source.');
  if(proxy){target=proxy.replace(/\/$/,'')+(start>=0?'/dem?start='+start+'&length='+limit:'/photo?tile='+url.slice(WAC.length));headers={};}
  const response=await fetcher(target,{headers,signal,credentials:'omit',redirect:'error'});
  const expected=`bytes ${start}-${start+limit-1}/${total}`;
  if(!response.ok||start>=0&&(response.status!==206||response.headers.get('Content-Range')!==expected)){
    await response.body?.cancel();throw Error('The lunar data could not be checked. Check your connection and the site data service.');
  }
  const declared=Number(response.headers.get('Content-Length'));if(declared>limit){await response.body.cancel();throw Error('The lunar data reply is too large.');}
  const reader=response.body.getReader(),parts=[];let count=0;
  try{while(true){const item=await reader.read();if(item.done)break;count+=item.value.length;if(count>limit){await reader.cancel();throw Error('The lunar download limit was exceeded.');}parts.push(item.value);}}
  finally{reader.releaseLock();}
  if(start>=0&&count!==limit)throw Error('The lunar download is incomplete.');
  const result=new Uint8Array(count);let offset=0;for(const part of parts){result.set(part,offset);offset+=part.length;}return result;
}
