import { EXPERIENCE_URL } from "./config";
import { api } from "./client";

export interface ResourceTag {
  resource_urn: string;
  tenant_id: string;
  tags: string[];
  featured: boolean;
}

export const resourceApi = {
  list: (filter?: { tag?: string; featured?: boolean }) => {
    const params = new URLSearchParams();
    if (filter?.tag) params.set("tag", filter.tag);
    if (filter?.featured !== undefined) params.set("featured", String(filter.featured));
    const query = params.toString();
    return api.get<ResourceTag[]>(`${EXPERIENCE_URL}/api/resources${query ? `?${query}` : ""}`);
  },
  setTags: (urn: string, tags: string[]) =>
    api.put<ResourceTag>(`${EXPERIENCE_URL}/api/resources/${encodeURIComponent(urn)}/tags`, { tags }),
  setFeatured: (urn: string) =>
    api.post<ResourceTag>(`${EXPERIENCE_URL}/api/resources/${encodeURIComponent(urn)}/featured`),
  unsetFeatured: (urn: string) =>
    api.delete<ResourceTag>(`${EXPERIENCE_URL}/api/resources/${encodeURIComponent(urn)}/featured`),
};
