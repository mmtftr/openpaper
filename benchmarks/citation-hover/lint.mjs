// Compatibility adapter for the repo's pre-existing minimatch v9 override:
// @eslint/eslintrc still imports the v3 default callable. No dependency edits.
import { registerHooks } from 'node:module';
import { mkdirSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
const shim=new URL('./.data/minimatch-compat.cjs',import.meta.url);
const original=createRequire(new URL('../../client/package.json',import.meta.url)).resolve('minimatch');
mkdirSync(new URL('./.data/',import.meta.url),{recursive:true});
writeFileSync(shim,`const m=require(${JSON.stringify(original)}); module.exports=Object.assign(m.minimatch,m);`);
registerHooks({resolve(specifier,context,next){
  if(specifier==='minimatch' && context.parentURL?.includes('@eslint/eslintrc/'))return{url:shim.href,shortCircuit:true};
  return next(specifier,context);
}});
