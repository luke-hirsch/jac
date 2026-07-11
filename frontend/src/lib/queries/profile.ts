import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

/** `/api/spa/profile/` —  */
export type ProfileRow = {
  id: number;
  name: string;
  username: string;
  email: string;
  first_name: string;
  last_name: string;
  display_name: string;
  bio: string;
  phone: string;
  website: string;
  linkedin_url: string;
  github_url: string;
  show_socials: boolean;
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
