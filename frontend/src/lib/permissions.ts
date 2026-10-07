import type { AuthUser, Capability } from "./contracts";

// Missing capabilities (including an older API response) must fail closed.
export function can(user: Pick<AuthUser, "capabilities"> | null | undefined, capability: Capability) {
  return user?.capabilities?.includes(capability) === true;
}
