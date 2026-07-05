// ---------------------------------------------------------------------------
// Win-prob model dataset client. Asks the engine (/api/models) which trained
// datasets are available (the all-patch backbone + any per-patch models) so the
// UI can offer a toggle between them. Returns null on failure, in which case the
// UI simply hides the toggle and the server uses its default dataset.
// ---------------------------------------------------------------------------
import type { ModelsResponse } from './types';

export async function getModels(): Promise<ModelsResponse | null> {
  try {
    const res = await fetch('/api/models');
    if (!res.ok) return null;
    return (await res.json()) as ModelsResponse;
  } catch {
    return null;
  }
}
