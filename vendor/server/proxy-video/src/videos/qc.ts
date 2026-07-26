import { createHash } from 'node:crypto';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
const execFileAsync=promisify(execFile);
export interface VideoQcResult { bytes:number; sha256:string; durationSeconds:number; width:number; height:number; }
/** Verifies ISO-BMFF container plus a primary H.264 video stream. Secondary AAC/MJPEG streams are tolerated because Imagine output may include them. */
export async function validateMp4(path:string,data:Buffer,maximumBytes:number,ffprobeCommand:string):Promise<VideoQcResult>{
 if(data.length<16||data.length>maximumBytes)throw new Error('VIDEO_SIZE_INVALID'); if(data.subarray(4,8).toString('ascii')!=='ftyp')throw new Error('VIDEO_SIGNATURE_INVALID');
 let raw:string;try{({stdout:raw}=await execFileAsync(ffprobeCommand,['-v','error','-show_entries','format=duration,size:stream=codec_type,codec_name,width,height','-of','json',path],{timeout:15000,maxBuffer:64*1024}));}catch{throw new Error('VIDEO_FFPROBE_FAILED');}
 let parsed: {format?:{duration?:string;size?:string};streams?:Array<{codec_type?:string;codec_name?:string;width?:number;height?:number}>};try{parsed=JSON.parse(raw) as typeof parsed;}catch{throw new Error('VIDEO_FFPROBE_PROTOCOL');}
 const primary=parsed.streams?.find(s=>s.codec_type==='video');const duration=Number(parsed.format?.duration);if(!primary||primary.codec_name!=='h264'||!Number.isFinite(duration)||duration<5||duration>11||!primary.width||!primary.height||primary.width<256||primary.height<256||primary.width>1920||primary.height>1920)throw new Error('VIDEO_QC_FAILED');
 return {bytes:data.length,sha256:createHash('sha256').update(data).digest('hex'),durationSeconds:duration,width:primary.width,height:primary.height};
}
