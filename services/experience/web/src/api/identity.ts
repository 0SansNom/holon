import { IDENTITY_URL } from "./config";
import { api } from "./client";

interface TokenResponse {
  access_token: string;
}

export async function mintToken(principalUrn: string, clientSecret: string): Promise<string> {
  const response = await api.post<TokenResponse>(`${IDENTITY_URL}/token`, {
    principal_urn: principalUrn,
    client_secret: clientSecret,
  });
  return response.access_token;
}
