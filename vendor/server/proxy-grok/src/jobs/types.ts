export type GrokJobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
export interface GrokJob {
  id: string; requestId: string; status: GrokJobStatus; prompt: string;
  createdAt: number; updatedAt: number; startedAt?: number; finishedAt?: number;
  errorCode?: string; artifactPath?: string; artifactBytes?: number; artifactSha256?: string;
}
export function publicJob(job: GrokJob): Record<string, unknown> {
  return { jobId: job.id, requestId: job.requestId, status: job.status, createdAt: job.createdAt, updatedAt: job.updatedAt,
    error: job.errorCode ? { code: job.errorCode } : undefined,
    artifacts: job.status === 'succeeded' && job.artifactPath ? [{ artifactId: 'primary', mediaType: 'video/mp4', bytes: job.artifactBytes, sha256: job.artifactSha256 }] : [] };
}
