export type DrawJobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface DrawJob {
  id: string;
  sequence: number;
  requestId: string;
  chatId: string;
  userId: string;
  logId: string;
  prompt: string;
  status: DrawJobStatus;
  createdAt: number;
  updatedAt: number;
  startedAt?: number;
  finishedAt?: number;
  codexJobId?: string;
  brushJobId?: string;
  errorCode?: string;
  artifactPath?: string;
  artifactBytes?: number;
  artifactSha256?: string;
}

export interface CreateDrawJob {
  requestId: string;
  chatId: string;
  userId: string;
  logId: string;
  prompt: string;
}

export function toPublicDrawJob(job: DrawJob): Record<string, unknown> {
  return {
    jobId: job.id,
    requestId: job.requestId,
    chatId: job.chatId,
    status: job.status,
    queueSequence: job.sequence,
    createdAt: job.createdAt,
    updatedAt: job.updatedAt,
    error: job.errorCode ? { code: job.errorCode } : undefined,
    file: job.status === "succeeded" && job.artifactBytes !== undefined
      ? {
          mediaType: "video/mp4",
          bytes: job.artifactBytes,
          sha256: job.artifactSha256,
          href: `/v1/draw/jobs/${job.id}/file?chatId=${job.chatId}`,
        }
      : undefined,
  };
}
