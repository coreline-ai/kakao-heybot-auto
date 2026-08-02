export type VisionStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
export type VisionTask = "describe" | "ocr" | "translate_ko";

export interface VisionSource {
  url: string;
  width: number;
  height: number;
  declaredBytes: number;
  expiresAtMillis: number;
}

export interface CreateVisionJob {
  requestId: string;
  chatId: string;
  userId: string;
  logId: string;
  task: VisionTask;
  source: VisionSource;
}

export interface VisionResult {
  version: 2;
  task: VisionTask;
  answer: string;
  visibleObjects: string[];
  extractedText: string[];
  uncertainty: "low" | "medium" | "high";
}

export interface VisionJob extends CreateVisionJob {
  id: string;
  sequence: number;
  status: VisionStatus;
  createdAt: number;
  updatedAt: number;
  errorCode?: string;
  result?: VisionResult;
}

export function publicJob(job: VisionJob): Record<string, unknown> {
  return {
    jobId: job.id,
    requestId: job.requestId,
    chatId: job.chatId,
    status: job.status,
    queueSequence: job.sequence,
    createdAt: job.createdAt,
    updatedAt: job.updatedAt,
    error: job.errorCode ? { code: job.errorCode } : undefined,
    result: job.status === "succeeded" ? job.result : undefined,
  };
}
