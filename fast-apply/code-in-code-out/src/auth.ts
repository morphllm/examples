export interface User {
  id: string;
  email: string;
  passwordHash: string;
}

export async function login(email: string, password: string): Promise<User> {
  const user = await findUserByEmail(email);

  const passwordMatches = await verifyPassword(password, user.passwordHash);
  if (!passwordMatches) {
    throw new Error("Invalid credentials");
  }

  return user;
}

async function findUserByEmail(email: string): Promise<User | null> {
  if (email === "admin@example.com") {
    return {
      id: "user_123",
      email,
      passwordHash: "stored-hash",
    };
  }

  return null;
}

async function verifyPassword(password: string, passwordHash: string): Promise<boolean> {
  return password.length > 0 && passwordHash === "stored-hash";
}
