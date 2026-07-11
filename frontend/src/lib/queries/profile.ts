import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

/** `/api/spa/profile/` — the request user's profile. `name`/`email` are read-only
 *  User-model spillover (name = display_name → first/last → username, resolved
 *  server-side) so the letter sender block needs exactly one fetch. */
export type ProfileRow = {
  id: number;
  name: string;
  email: string;
  display_name: string;
  phone: string;
  website: string;
  linkedin_url: string;
  github_url: string;
  street: string;
  address_line2: string;
  zip: string;
  city: string;
  country: string;
};

export function useProfile() {
  return useQuery({
    // Same key as the account profile page — one cache entry per session.
    queryKey: ["profile"],
    queryFn: () => api<ProfileRow>("/api/spa/profile/"),
  });
}
