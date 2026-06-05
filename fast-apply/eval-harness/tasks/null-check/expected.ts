export interface User {
  id: string;
  email: string;
  passwordHash: string;
}

export async function login(email: string, password: string): Promise<User> {
  const user = await findUserByEmail(email);

  if (!user) {
    throw new Error("Invalid credentials");
  }

  const passwordMatches = await verifyPassword(password, user.passwordHash);
  if (!passwordMatches) {
    throw new Error("Invalid credentials");
  }

  return user;
}
