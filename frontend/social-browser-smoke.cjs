// Official-provider responses are mocked: this browser test never publishes externally.
const assert = require('node:assert/strict');
module.exports = async function socialSmoke(page, api, capture) {
  await page.getByRole('button',{name:'Review & publish',exact:true}).click();
  await page.getByRole('heading',{name:'Ready to Publish',exact:true}).waitFor();
  await page.getByText('Loading publishing settings…').waitFor({state:'hidden'});
  assert.equal(await page.getByRole('button',{name:'Publish Everywhere',exact:true}).isDisabled(),true);
  await capture('publishing-unconfigured');
  await page.getByRole('button',{name:'Back to your Short',exact:true}).click();
  const copy={youtube:{title:'The hiring mistake beginners make',description:'Plan for the decisions your next hire will own.',hashtags:['#Hiring','#Shorts']},tiktok:{caption:'Before your next hire, ask this question.',hashtags:['#hiring']},x:{text:'Hiring starts with the decisions someone will own. What would you define first?'}};
  const accounts={accounts:['youtube','tiktok','x'].map(platform=>({id:'acct-'+platform,platform,account_name:'Fred Creates',username:'@fred',status:'connected'})),platforms:['youtube','tiktok','x'].map(platform=>({platform,enabled:true,private_only:platform!=='x',notice:platform==='youtube'?'Private uploads until API verification.':platform==='tiktok'?'Private posts until TikTok audit.':null})),copy_available:true};
  const calls={publish:[],regenerate:[],retry:[]};let batch=null,saved={},polls=0;
  const route=async route=>{
    const request=route.request(),url=new URL(request.url()),method=request.method(),body=request.postDataJSON();
    const path=url.pathname;let result;
    if(path==='/v1/social-accounts')result=accounts;
    else if(path==='/v1/social-accounts/tiktok/creator-info')result={account_name:'Fred Creates',username:'fred',privacy_options:['SELF_ONLY'],comment_disabled:true,duet_disabled:false,stitch_disabled:false,max_duration:60};
    else if(path.endsWith('/social-copy/regenerate')){calls.regenerate.push(body);saved={...saved,...Object.fromEntries(body.platforms.map(p=>[p,copy[p]]))};result={copy:saved};}
    else if(path.endsWith('/social-copy')){if(method==='PATCH')saved={...saved,...body.copy};result={copy:saved,source_transcript:'Hire for the work your company will need in eighteen months.',transcript_verified:false};}
    else if(path.endsWith('/publishing-history'))result=batch?[batch]:[];
    else if(path.endsWith('/publish')){calls.publish.push(body);batch={id:'batch-ui',render_id:path.split('/')[3],created_at:Date.now(),jobs:body.targets.map(t=>({id:'job-'+t.platform,platform:t.platform,status:'queued',account_name:'Fred Creates'}))};result=batch;}
    else if(path==='/v1/publish-batches/batch-ui'){
      polls++;batch={...batch,jobs:batch.jobs.map(j=>({...j,status:polls===1?'processing':j.platform==='x'&&!calls.retry.length?'failed':'published',retryable:j.platform==='x'&&!calls.retry.length,error_code:j.platform==='x'&&!calls.retry.length?'PROVIDER_UNAVAILABLE':null,error_message_safe:j.platform==='x'&&!calls.retry.length?'X is temporarily unavailable.':null,platform_post_url:j.platform==='x'&&!calls.retry.length?null:'https://example.com/'+j.platform}))};result=batch;
    } else if(path==='/v1/publish-jobs/job-x/retry'){calls.retry.push('x');result={...batch.jobs.find(j=>j.platform==='x'),status:'queued'};}
    else return route.fallback();
    await route.fulfill({status:path.endsWith('/publish')?202:200,contentType:'application/json',body:JSON.stringify(result),headers:{'Access-Control-Allow-Origin':new URL(page.url()).origin,'Access-Control-Allow-Credentials':'true','Access-Control-Allow-Headers':'Authorization,Content-Type,Idempotency-Key','Access-Control-Allow-Methods':'GET,POST,PATCH,OPTIONS'}});
  };
  await page.route(api+'/v1/**',route);
  await page.getByRole('button',{name:'Review & publish',exact:true}).click();
  try { await page.getByRole('heading',{name:'Review the transcript',exact:true}).waitFor(); } catch(error) { await capture('publishing-test-error');throw error; }
  assert.equal(calls.regenerate.length,0,'Demo transcript must not automatically generate misleading copy');
  await page.getByLabel('I reviewed the transcript and it matches this Short.').check();
  for(const name of ['YouTube','TikTok','X'])await page.locator('.social-target').filter({has:page.getByText(name,{exact:true})}).getByRole('checkbox').check();
  await page.getByRole('button',{name:'Regenerate All Copy',exact:true}).click();
  await page.getByLabel('YouTube title',{exact:true}).filter({visible:true}).waitFor();
  await page.getByLabel('X post',{exact:true}).fill('My reviewed X text.');
  await page.getByRole('button',{name:'Regenerate YouTube',exact:true}).click();
  await page.getByRole('button',{name:'Regenerate YouTube',exact:true}).waitFor();
  assert.equal(await page.getByLabel('X post',{exact:true}).inputValue(),'My reviewed X text.');
  await page.getByLabel('YouTube title',{exact:true}).fill('My reviewed YouTube title');
  try { await page.getByLabel('TikTok privacy',{exact:true}).selectOption('SELF_ONLY'); } catch(error) { await capture('publishing-test-error');throw error; }
  assert.equal(await page.getByLabel('Allow comments',{exact:true}).isDisabled(),true);
  assert.equal(await page.getByRole('button',{name:'Publish Everywhere',exact:true}).isDisabled(),true,'TikTok needs explicit consent');
  await page.getByLabel(/By posting, I agree to TikTok/).check();
  assert.equal(calls.publish.length,0,'Review and regeneration never publish');
  await capture('publishing-review');
  await page.getByRole('button',{name:'Publish Everywhere',exact:true}).click();
  await page.getByRole('heading',{name:'Publishing your Short',exact:true}).waitFor();
  await capture('publishing-progress');
  await page.getByRole('button',{name:'Retry X',exact:true}).waitFor({timeout:10000});
  assert.equal(calls.publish.length,1);assert.equal(calls.publish[0].targets.length,3);assert.equal(calls.publish[0].approved,true);
  assert.equal(calls.publish[0].targets.find(t=>t.platform==='youtube').youtube.title,'My reviewed YouTube title');
  assert.equal(calls.publish[0].targets.find(t=>t.platform==='x').x.text,'My reviewed X text.');
  await capture('publishing-partial-failure');
  await page.getByRole('button',{name:'Retry X',exact:true}).click();
  await page.getByRole('heading',{name:'Published Successfully',exact:true}).waitFor();
  assert.deepEqual(calls.retry,['x']);assert.equal(calls.publish.length,1);
  assert.equal(await page.getByRole('link',{name:'View Post',exact:true}).count(),3);
  await capture('publishing-success');
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('heading',{name:'Published Successfully',exact:true}).waitFor();
  assert.equal(await page.getByRole('link',{name:'View Post',exact:true}).count(),3);
  await capture('publishing-mobile');
  await page.setViewportSize({width:1440,height:1050});
  await page.getByRole('button',{name:'Back to your Short',exact:true}).click();
  await page.getByRole('heading',{name:'Publishing History',exact:true}).waitFor();
  await page.unroute(api+'/v1/**',route);
};
