import assert from "node:assert/strict";
import { test } from "node:test";
import { loadVisionConfig } from "../../src/config.js";
import { fetchSource, validateSource } from "../../src/source.js";

const config=loadVisionConfig({},"/tmp/vision-config-test");
const source=(url="https://talk.kakaocdn.net/fake/image.png")=>({url,width:100,height:100,declaredBytes:24,expiresAtMillis:Date.now()+60_000});

test("source contract is exact-host HTTPS and bounded",()=>{
  assert.doesNotThrow(()=>validateSource(source(),config));
  assert.throws(()=>validateSource(source("http://talk.kakaocdn.net/x"),config),/FORBIDDEN_SOURCE/);
  assert.throws(()=>validateSource(source("https://evil.example/x"),config),/FORBIDDEN_SOURCE/);
  assert.throws(()=>validateSource({...source(),declaredBytes:config.imageMaxBytes+1},config),/INVALID_SOURCE_METADATA/);
  assert.throws(()=>validateSource({...source(),expiresAtMillis:Date.now()-1},config),/SOURCE_EXPIRED/);
});

test("fetch rejects redirects and MIME-magic mismatch",async()=>{
  const original=globalThis.fetch;
  try{
    globalThis.fetch=(async()=>new Response(null,{status:302,headers:{location:"https://evil.example/x"}})) as typeof fetch;
    await assert.rejects(()=>fetchSource(source(),config,new AbortController().signal),/SOURCE_REDIRECT_FORBIDDEN/);
    globalThis.fetch=(async()=>new Response(new Uint8Array(Buffer.alloc(24)),{status:200,headers:{"content-type":"image/png","content-length":"24"}})) as typeof fetch;
    await assert.rejects(()=>fetchSource(source(),config,new AbortController().signal),/INVALID_IMAGE/);
  }finally{globalThis.fetch=original;}
});

test("fetch sniffs a headerless Kakao GIF by magic",async()=>{
  const original=globalThis.fetch;
  const gif=Buffer.alloc(24);Buffer.from("GIF89a","ascii").copy(gif);
  try{
    globalThis.fetch=(async()=>new Response(new Uint8Array(gif),{status:200,headers:{"content-length":String(gif.length)}})) as typeof fetch;
    const fetched=await fetchSource({...source(),declaredBytes:gif.length},config,new AbortController().signal);
    assert.equal(fetched.mediaType,"image/gif");
    assert.deepEqual(fetched.data,gif);
  }finally{globalThis.fetch=original;}
});
