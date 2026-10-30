"use strict";
const $ = id => document.getElementById(id);
let token, busy = false, panorama, coverage;
async function json(response) {
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || `Request failed (${response.status})`);
  return value;
}
async function configure() {
  const config = await json(await fetch('/api/config'));
  token = config.token;
  $('start').disabled = Boolean(config.disabled_reason) || busy;
  $('status').textContent = config.disabled_reason || 'Ready. Starting sends original photo bytes to the approved Comfy managed deployment.';
}
$('photos').addEventListener('change', () => {
  $('selected').replaceChildren(...Array.from($('photos').files, file => {
    const row = document.createElement('li'); row.textContent = `${file.name} · ${(file.size/1048576).toFixed(1)} MiB`; return row;
  }));
});
function encoded(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({name:file.name, base64: reader.result.split(',')[1]});
    reader.onerror = () => reject(new Error(`Cannot read ${file.name}`));
    reader.readAsDataURL(file);
  });
}
$('form').addEventListener('submit', async event => {
  event.preventDefault();
  const files = Array.from($('photos').files);
  if (files.length < 2 || files.length > 6 || files.some(f => f.size > 12*1048576) || files.reduce((n,f) => n+f.size,0)>32*1048576) {
    $('status').textContent = 'Select 2–6 photos, at most 12 MiB each and 32 MiB total.'; return;
  }
  busy = true; $('start').disabled = true; $('results').hidden = true;
  try {
    $('status').textContent = 'Validating and submitting photographs…';
    const job = await json(await fetch('/api/jobs', {method:'POST', headers:{'Content-Type':'application/json','X-Panorama-Token':token},body:JSON.stringify({photos:await Promise.all(files.map(encoded))})}));
    $('job').textContent = `Local job ${job.id}`;
    await poll(job.id);
  } catch (error) { $('status').textContent = error.message; }
  finally { busy = false; await configureAfterJob(); }
});
async function configureAfterJob() {
  try { const config = await json(await fetch('/api/config')); $('start').disabled = Boolean(config.disabled_reason); }
  catch { $('start').disabled = true; }
}
async function poll(id) {
  for (;;) {
    const state = await json(await fetch(`/api/jobs/${id}`));
    $('status').textContent = `${state.state}: ${state.message}`;
    if (state.remote_job_id) $('job').textContent = `Cloud job ${state.remote_job_id} · local ${id}`;
    if (['succeeded','failed','incomplete'].includes(state.state)) {
      if (state.artifacts.length) await show(state.artifacts);
      return;
    }
    await new Promise(resolve => setTimeout(resolve, 2000));
  }
}
function loadImage(image, url) {
  return new Promise((resolve, reject) => {image.onload=resolve;image.onerror=()=>reject(new Error('Cannot load downloaded output.'));image.src=url;});
}
async function show(artifacts) {
  $('results').hidden = false; $('cropControls').hidden = true;
  $('panorama').removeAttribute('src'); $('coverage').removeAttribute('src');
  $('downloads').replaceChildren(...artifacts.map(artifact => {
    const li=document.createElement('li'), link=document.createElement('a');
    link.href=artifact.url;link.download=artifact.name;link.textContent=artifact.name;li.append(link);return li;
  }));
  const p = artifacts.find(a=>a.name==='panorama.png'), m = artifacts.find(a=>a.name==='coverage.png');
  if (!p || !m) return;
  panorama=$('panorama'); coverage=$('coverage');
  await Promise.all([loadImage(panorama,p.url), loadImage(coverage,m.url)]);
  if (panorama.naturalWidth!==coverage.naturalWidth || panorama.naturalHeight!==coverage.naturalHeight) throw new Error('Coverage dimensions do not match panorama.');
  $('w').value=panorama.naturalWidth;$('h').value=panorama.naturalHeight;
  $('cropControls').hidden=false;
}
$('crop').addEventListener('click', () => {
  try {
    const [x,y,w,h]=['x','y','w','h'].map(id=>Number($(id).value));
    if (![x,y,w,h].every(Number.isInteger) || x<0 || y<0 || w<1 || h<1 || x+w>panorama.naturalWidth || y+h>panorama.naturalHeight || w*h>24000000) throw new Error('Enter a nonempty rectangle within the panorama (24 million pixels maximum).');
    const mask=document.createElement('canvas');mask.width=w;mask.height=h;
    const context=mask.getContext('2d', {willReadFrequently:true});context.drawImage(coverage,x,y,w,h,0,0,w,h);
    const pixels=context.getImageData(0,0,w,h).data;
    for (let i=0;i<pixels.length;i+=4) if (pixels[i]!==255 || pixels[i+3]!==255) throw new Error('Crop contains uncovered pixels. Choose a smaller covered rectangle.');
    const canvas=document.createElement('canvas');canvas.width=w;canvas.height=h;
    canvas.getContext('2d').drawImage(panorama,x,y,w,h,0,0,w,h);
    canvas.toBlob(blob=>{
      if(!blob){$('cropStatus').textContent='Browser could not export crop.';return;}
      const url=URL.createObjectURL(blob), link=document.createElement('a');link.href=url;link.download=`crop-${x}-${y}-${w}-${h}.png`;link.click();setTimeout(()=>URL.revokeObjectURL(url),60000);
    },'image/png');
    $('cropStatus').textContent='Covered crop exported locally. No stitch rerun. Browser export is SDR and may omit source metadata.';
  } catch(error) { $('cropStatus').textContent=error.message; }
});
configure().catch(error=>{$('status').textContent=error.message;});
