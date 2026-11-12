import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';

const dialog={
  open:false,closed:0,
  set innerHTML(_html){
    const buttons={close:{disabled:false},submit:{disabled:false}};
    this.form={dataset:{},buttons,querySelector:selector=>selector==='[data-close]'?buttons.close:buttons.submit};
  },
  querySelector(){return this.form},
  showModal(){this.open=true},
  close(){this.open=false;this.closed++},
};
const notices=[];
const ctx=vm.createContext({document:{querySelector:()=>dialog},FormData:class {}});
vm.runInContext(readFileSync(new URL('../static/app.js',import.meta.url),'utf8').split("$('#quit').onclick")[0],ctx);
ctx.notify=message=>notices.push(message);
const submit=form=>form.onsubmit({preventDefault(){},submitter:form.buttons.submit});
let finish;
ctx.openDialog('Save',()=>new Promise(resolve=>finish=resolve));
const saving=dialog.form,pending=submit(saving);
assert.equal(saving.buttons.close.disabled,true,'Saving disables Cancel');
saving.buttons.close.onclick();assert.equal(dialog.open,true,'Cancel cannot dismiss a pending save');
let prevented=false;dialog.oncancel({preventDefault(){prevented=true}});
assert.equal(prevented,true,'Escape cannot dismiss a pending save');
finish();await pending;assert.equal(dialog.open,false,'Successful save closes its own dialog');

ctx.openDialog('Scan',()=>new Promise(resolve=>finish=resolve),{cancellable:true});
const scan=dialog.form,scanning=submit(scan);
assert.equal(scan.buttons.close.disabled,false,'Read-only scans remain cancellable');
scan.buttons.close.onclick();assert.equal(dialog.open,false);
ctx.openDialog('New editor',async()=>{});
const newForm=dialog.form;
finish();await scanning;
assert.equal(dialog.open,true,'A late scan cannot close a newer dialog');
assert.equal(dialog.form,newForm);

ctx.openDialog('Invalid save',async()=>{throw Error('Invalid details')});
await submit(dialog.form);
assert.equal(dialog.open,true,'Failed saves retain entered fields');
assert.equal(dialog.form.dataset.saving,'false');
assert.equal(dialog.form.buttons.close.disabled,false);
assert.equal(notices.at(-1),'Invalid details');
console.log('Dialog checks passed: busy saves, cancellable scans, stale completion and failed-save recovery.');
