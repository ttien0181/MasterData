type SavedChoice = { members: string[]; choices: Record<string, string> };
type ChoiceStore = Record<string, SavedChoice>;

const key = (runId: string) => `masterone.choices.v1.${runId}`;
const entryKey = (bucket: "merge" | "review", itemId: string) => `${bucket}:${itemId}`;

function read(runId: string): ChoiceStore {
  try {
    const value = JSON.parse(localStorage.getItem(key(runId)) ?? "{}");
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}

function write(runId: string, store: ChoiceStore) {
  localStorage.setItem(key(runId), JSON.stringify(store));
}

export function getSavedChoices(runId: string, bucket: "merge" | "review", itemId: string, members: string[]) {
  const saved = read(runId)[entryKey(bucket, itemId)];
  return saved && Array.isArray(saved.members) && saved.choices &&
    typeof saved.choices === "object" && !Array.isArray(saved.choices) &&
    saved.members.join("|") === [...members].sort().join("|")
    ? saved.choices : null;
}

export function saveChoices(runId: string, bucket: "merge" | "review", itemId: string,
  members: string[], choices: Record<string, string>) {
  const store = read(runId);
  store[entryKey(bucket, itemId)] = { members: [...members].sort(), choices };
  write(runId, store);
}

export function moveReviewChoices(runId: string, reviewId: string, proposalId: string) {
  const store = read(runId);
  const saved = store[entryKey("review", reviewId)];
  if (!saved) return;
  store[entryKey("merge", proposalId)] = saved;
  delete store[entryKey("review", reviewId)];
  write(runId, store);
}

export function discardReviewChoices(runId: string, reviewId: string) {
  const store = read(runId);
  delete store[entryKey("review", reviewId)];
  write(runId, store);
}

export function discardMergeChoices(runId: string, itemId: string) {
  const store = read(runId);
  delete store[entryKey("merge", itemId)];
  write(runId, store);
}

export function exportChoices(runId: string) {
  const result: Record<string, Record<string, string>> = {};
  for (const [name, saved] of Object.entries(read(runId))) {
    if (name.startsWith("merge:") && saved?.choices && typeof saved.choices === "object" &&
      !Array.isArray(saved.choices)) result[name.slice(6)] = saved.choices;
  }
  return result;
}
