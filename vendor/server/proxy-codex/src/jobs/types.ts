export type CodexJobStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface CodexJob {
  id: string;
  caller: string;
  requestId: string;
  capability: string;
  prompt: string;
  status: CodexJobStatus;
  createdAt: number;
  updatedAt: number;
  startedAt?: number;
  finishedAt?: number;
  errorCode?: string;
  artifactId?: string;
  artifactPath?: string;
  artifactBytes?: number;
  artifactSha256?: string;
}

export interface CreateCodexJob {
  caller: string;
  requestId: string;
  capability: "image.generate";
  prompt: string;
}

export interface PublicCodexJob {
  jobId: string;
  requestId: string;
  capability: string;
  status: CodexJobStatus;
  createdAt: number;
  updatedAt: number;
  error?: { code: string };
  artifacts: Array<{
    artifactId: string;
    mediaType: "image/png";
    bytes: number;
    sha256: string;
  }>;
}

export function toPublicJob(job: CodexJob): PublicCodexJob {
  return {
    jobId: job.id,
    requestId: job.requestId,
    capability: job.capability,
    status: job.status,
    createdAt: job.createdAt,
    updatedAt: job.updatedAt,
    error: job.errorCode ? { code: job.errorCode } : undefined,
    artifacts:
      job.artifactId && job.artifactBytes !== undefined && job.artifactSha256
        ? [
            {
              artifactId: job.artifactId,
              mediaType: "image/png",
              bytes: job.artifactBytes,
              sha256: job.artifactSha256,
            },
          ]
        : [],
  };
}
