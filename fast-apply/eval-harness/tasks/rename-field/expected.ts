interface Profile {
  id: string;
  fullName: string;
}

export function formatProfile(profile: Profile): string {
  return `${profile.fullName} (${profile.id})`;
}
