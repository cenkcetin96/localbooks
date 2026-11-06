// A small Chrome DevTools pipe client. Node built-ins only; no listening debug port.
import {spawn} from 'node:child_process';
import {readFileSync,writeFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
const [source,destination,profile]=process.argv.slice(2);
const browser=spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',[
  '--headless=new','--remote-debugging-pipe','--disable-gpu','--no-first-run',
  '--disable-background-networking','--disable-component-update','--disable-sync',
  '--disable-extensions','--disable-default-apps','--no-default-browser-check',
  '--metrics-recording-only','--disable-features=MediaRouter,OptimizationHints',
  '--host-resolver-rules=MAP * ~NOTFOUND','--user-data-dir='+profile,'about:blank'
],{stdio:['ignore','ignore','pipe','pipe','pipe']});
let counter=0,buffer='',pending=new Map(),errors='';
browser.stderr.on('data',chunk=>{errors=(errors+chunk).slice(-2000)});
browser.stdio[4].on('data',chunk=>{
  buffer+=chunk.toString();let end;
  while((end=buffer.indexOf('\0'))!==-1){const raw=buffer.slice(0,end);buffer=buffer.slice(end+1);if(!raw)continue;
    const message=JSON.parse(raw);const request=pending.get(message.id);
    if(request){pending.delete(message.id);message.error?request.reject(Error(message.error.message)):request.resolve(message.result)}
  }
});
function send(method,params={},sessionId){return new Promise((resolve,reject)=>{const id=++counter;pending.set(id,{resolve,reject});browser.stdio[3].write(JSON.stringify({id,method,params,...(sessionId?{sessionId}:{})})+'\0')})}
const timeout=setTimeout(()=>{console.error('Chrome PDF timed out. '+errors);browser.kill('SIGKILL');process.exit(1)},35000);
browser.on('error',error=>{console.error(error.message);process.exit(1)});
try{
  const {targetId}=await send('Target.createTarget',{url:'about:blank'});
  const {sessionId}=await send('Target.attachToTarget',{targetId,flatten:true});
  await send('Page.enable',{},sessionId);
  await send('Page.navigate',{url:pathToFileURL(source).href},sessionId);
  let ready;
  for(let i=0;i<250;i++){
    const result=await send('Runtime.evaluate',{expression:'document.documentElement.dataset.ready || "waiting"',returnByValue:true},sessionId);
    ready=result.result?.value;if(ready==='true'||ready==='error')break;
    await new Promise(resolve=>setTimeout(resolve,40));
  }
  if(ready!=='true'){
    const result=await send('Runtime.evaluate',{expression:'document.body?.dataset.error || "Document did not finish laying out"',returnByValue:true},sessionId);
    throw Error(result.result?.value||'Document layout failed');
  }
  const {data}=await send('Page.printToPDF',{printBackground:true,displayHeaderFooter:false,preferCSSPageSize:true,marginTop:0,marginBottom:0,marginLeft:0,marginRight:0},sessionId);
  writeFileSync(destination,Buffer.from(data,'base64'));
  clearTimeout(timeout);
  await send('Browser.close');
}catch(error){console.error(error.message);clearTimeout(timeout);browser.kill('SIGKILL');process.exitCode=1}
