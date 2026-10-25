import {createRequire} from 'node:module';
import {writeFile} from 'node:fs/promises';
const require=createRequire(import.meta.url);
const puppeteer=require('puppeteer-core');
const url=process.env.HALFLIFE_WEB_URL||'http://127.0.0.1:12081';
const browser=await puppeteer.launch({executablePath:'/opt/google/chrome/chrome',headless:true,pipe:true,args:['--no-sandbox','--disable-dev-shm-usage']});
const errors=[],inputs=[];
try {
 const page=await browser.newPage();
 page.on('pageerror',e=>errors.push(String(e)));
 page.on('response',r=>{if(r.url().includes('/input?'))inputs.push({url:r.url(),status:r.status()})});
 await page.setViewport({width:1000,height:1050});
 await page.goto(url,{waitUntil:'domcontentloaded',timeout:15000});
 await page.waitForFunction(()=>document.querySelector('#screen')?.naturalWidth===320,{timeout:15000});
 await page.click('h1');
 await page.keyboard.down('w');
 await new Promise(r=>setTimeout(r,500));
 await page.keyboard.up('w');
 await page.evaluate(async()=>{await queue});
 await new Promise(r=>setTimeout(r,1500));
 const report=await page.evaluate(()=>({title:document.title,status:document.querySelector('#status').textContent,width:screen.naturalWidth,height:screen.naturalHeight,held:[...held]}));
 report.station=await (await fetch(url+'/status')).json();
 report.javascript_errors=errors;report.inputs=inputs;
 await page.screenshot({path:'build/browser-page.png',fullPage:true});
 await writeFile('build/browser-check.json',JSON.stringify(report,null,2)+'\n');
 console.log(JSON.stringify(report,null,2));
 if(errors.length||!report.station.game_running||report.held.length||inputs.length<2||inputs.some(r=>r.status!==200))throw Error('Browser smoke check failed');
}finally {await fetch(url+'/input?type=reset').catch(()=>{});await browser.close();}
