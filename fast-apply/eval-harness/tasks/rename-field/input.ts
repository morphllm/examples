interface Profile {
  id: string;
  displayName: string;
}

export function formatProfile(profile: Profile): string {
  return `${profile.displayName} (${profile.id})`;
}
