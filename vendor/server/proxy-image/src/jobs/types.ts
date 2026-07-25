export type ImageJobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface ImageJob {
  id: string;
  sequence: number;
  requestId: string;
  chatId: string;
  userId: string;
  logId: string;
  prompt: string;
  status: ImageJobStatus;
  createdAt: number;
  updatedAt: number;
  startedAt?: number;
  finishedAt?: number;
  codexJobId?: string;
  errorCode?: string;
  artifactPath?: string;
  artifactBytes?: number;
  artifactSha256?: string;
}

export interface CreateImageJob {
  requestId: string;
  chatId: string;
  userId: string;
  logId: string;
  prompt: string;
}

export function toPublicImageJob(job: ImageJob): Record<string, unknown> {
  return {
    jobId: job.id,
    requestId: job.requestId,
    chatId: job.chatId,
    status: job.status,
    queueSequence: job.sequence,
    createdAt: job.createdAt,
    updatedAt: job.updatedAt,
    error: job.errorCode ? { code: job.errorCode } : undefined,
    file:
      job.status === "succeeded" && job.artifactBytes !== undefined
        ? {
            mediaType: "image/png",
            bytes: job.artifactBytes,
            sha256: job.artifactSha256,
            href: `/v1/image/jobs/${job.id}/file`,
          }
        : undefined,
  };
}
