'use strict';
const fs=require('node:fs');
const {app,plain,generate}=require('./scheduler.cjs');
(async()=>{
  const run=app();
  const input=JSON.parse(fs.readFileSync(0,'utf8')||'{}');
  run(`scheduleMap=${JSON.stringify(input.schedules||{})};noDutyMap=${JSON.stringify(input.noDuty||{})};commentMap=${JSON.stringify(input.comments||{})};absences=${JSON.stringify(input.absences||[])};customHolidays=${JSON.stringify(input.customHolidays||{})};`);
  await generate(run,Number(process.argv[2]),Number(process.argv[3]));
  process.stdout.write(JSON.stringify({payload:plain(run('savedPayload')),members:plain(run('memberList')),rules:plain(run('dutyRule'))}));
})().catch(err=>{console.error(err);process.exitCode=1;});
