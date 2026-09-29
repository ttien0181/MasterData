let userId = "";
export function setActiveImportUser(id: string) { userId = id; }
const storageKey = () => `masterone.active-import.v2.${userId}`;

export function saveActiveImport(id: string) {
  if (userId) window.localStorage.setItem(storageKey(), id);
}
export function readActiveImport() {
  return userId ? window.localStorage.getItem(storageKey()) : null;
}
export function clearActiveImport() {
  if (userId) window.localStorage.removeItem(storageKey());
}
