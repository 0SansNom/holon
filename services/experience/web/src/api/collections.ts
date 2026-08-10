import { EXPERIENCE_URL } from "./config";
import { api } from "./client";

export interface Collection {
  id: number;
  tenant_id: string;
  name: string;
  description: string;
  created_by_urn: string;
  created_at: string;
}

export interface CollectionDetail extends Collection {
  members: string[];
}

export const collectionsApi = {
  list: () => api.get<Collection[]>(`${EXPERIENCE_URL}/api/collections`),
  get: (id: number) => api.get<CollectionDetail>(`${EXPERIENCE_URL}/api/collections/${id}`),
  create: (name: string, description?: string) =>
    api.post<Collection>(`${EXPERIENCE_URL}/api/collections`, { name, description }),
  delete: (id: number) => api.delete<{ status: string }>(`${EXPERIENCE_URL}/api/collections/${id}`),
  addMember: (id: number, urn: string) =>
    api.post<{ status: string }>(`${EXPERIENCE_URL}/api/collections/${id}/members/${encodeURIComponent(urn)}`),
  removeMember: (id: number, urn: string) =>
    api.delete<{ status: string }>(`${EXPERIENCE_URL}/api/collections/${id}/members/${encodeURIComponent(urn)}`),
  listForResource: (urn: string) =>
    api.get<Collection[]>(`${EXPERIENCE_URL}/api/resources/${encodeURIComponent(urn)}/collections`),
};
