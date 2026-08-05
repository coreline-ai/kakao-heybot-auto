export type AudioJobStatus =
  | "queued" | "fetching" | "validating" | "normalizing" | "transcribing"
  | "transcribed" | "failed" | "cancelled";

export interface AudioSourceInput {
  url: string;
  declaredBytes: number;
  expiresAtMillis: number;
  declaredExtension: "mp3" | "m4a" | "wav";
}

export interface AudioCreateInput {
  requestId: string;
  chatId: string;
  source: AudioSourceInput;
  language: "ko";
}

export interface AudioSegment {
  id: string;
  startMs: number;
  endMs: number;
  text: string;
}

export interface AudioTranscriptResult {
  version: 1;
  status: "transcribed";
  durationMs: number;
  language: "ko";
  segments: AudioSegment[];
  quality: { speechRatio: number; warnings: string[] };
}

export interface StoredAudioJob {
  id: string;
  requestId: string;
  chatId: string;
  status: AudioJobStatus;
  sourceUrl: string | null;
  declaredBytes: number;
  expiresAtMillis: number;
  declaredExtension: "mp3" | "m4a" | "wav";
  language: "ko";
  errorCode: string | null;
  result: AudioTranscriptResult | null;
  createdAtMillis: number;
  updatedAtMillis: number;
}

export interface PublicAudioJob {
  version: 1;
  jobId: string;
  requestId: string;
  chatId: string;
  status: AudioJobStatus;
  error?: { code: string };
  result?: AudioTranscriptResult;
}

export function toPublicJob(job: StoredAudioJob): PublicAudioJob {
  return {
    version: 1,
    jobId: job.id,
    requestId: job.requestId,
    chatId: job.chatId,
    status: job.status,
    ...(job.errorCode ? { error: { code: job.errorCode } } : {}),
    ...(job.result ? { result: job.result } : {}),
  };
}
