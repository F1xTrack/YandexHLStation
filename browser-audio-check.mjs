import {createRequire} from 'node:module';
import {writeFile} from 'node:fs/promises';
const require=createRequire(import.meta.url);
const puppeteer=require('puppeteer-core');
const base=process.env.HALFLIFE_WEB_URL||'http://127.0.0.1:12081';
const browser=await puppeteer.launch({executablePath:'/opt/google/chrome/chrome',headless:true,pipe:true,args:['--no-sandbox','--disable-dev-shm-usage']});
const errors=[],inputs=[];
let controlsChanged=false;
const read=async()=>{const r=await fetch(base+'/audio');if(!r.ok)throw Error('Audio GET failed');return r.json()};
try {
 const page=await browser.newPage();
 page.on('pageerror',e=>errors.push(String(e)));
 page.on('request',r=>{if(r.url().includes('/input?'))inputs.push(r.url())});
 await page.setViewport({width:1024,height:1150});
 await page.goto(base,{waitUntil:'domcontentloaded'});
 await page.waitForFunction(()=>!document.querySelector('#volume').disabled);
 const initial=await read();
 if(initial.volume!==0||!initial.muted)throw Error('Expected initial hardware mute');
 await page.focus('#volume');
 await page.keyboard.press('w');
 controlsChanged=true;
 await page.evaluate(()=>{const s=document.querySelector('#volume');s.value='3';s.dispatchEvent(new Event('input',{bubbles:true}))});
 await page.waitForFunction(()=>document.querySelector('#volume-value').textContent==='3%'&&document.querySelector('#audio-state').textContent.startsWith('Звук из'));
 const low=await read();
 if(low.volume!==3||low.muted)throw Error('Volume slider failed');
 await page.focus('#mute');
 await page.keyboard.press('Space');
 await page.waitForFunction(()=>document.querySelector('#mute').disabled&&document.querySelector('#volume-value').textContent==='0%');
 const muted=await read();
 const invalid=[];
 for(const value of ['101','-1','abc','1.5','1%26volume=100']){
  const r=await fetch(base+'/audio?volume='+value,{method:'POST'});
  invalid.push({value,status:r.status});if(r.status!==400)throw Error('Invalid volume accepted: '+value);
 }
 const getWrite=await fetch(base+'/audio?volume=100');
 if(getWrite.ok)throw Error('GET changed volume');
 const final=await read();
 if(final.volume!==0||!final.muted||errors.length||inputs.length)throw Error('Audio or keyboard isolation check failed');
 await page.screenshot({path:'build/browser-audio-page.png',fullPage:true});
 const report={initial,low,muted,invalid,get_write_status:getWrite.status,final,javascript_errors:errors,unexpected_game_inputs:inputs};
 await writeFile('build/browser-audio-check.json',JSON.stringify(report,null,2)+'\n');
 console.log(JSON.stringify(report,null,2));
} finally {
 if(controlsChanged)await fetch(base+'/audio?volume=0',{method:'POST'}).catch(()=>{});
 await browser.close();
}
