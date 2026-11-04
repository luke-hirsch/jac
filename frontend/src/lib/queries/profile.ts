import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

/** `/api/spa/profile/` —  */
export type ProfileRow = {
  id: number;
  name: string;
  username: string;
  handle: string;
  portfolio_public: boolean;
  portfolio_always_public: boolean; // read-only: the site owner's portfolio can't be unpublished
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
  signature: string; // media URL of the uploaded signature image ("" when unset)
  street: string;
  address_line2: string;
  zip: string;
  city: string;
  country: string;
  theme: "system" | "light" | "dark";
  contrast: "normal" | "high";
  accent_color: string; // "" = keep the layout template's accent
};

/** False when the CV and the letter fall back to the username (jac cover_letter._candidate_name). */
export function hasOwnName(
  profile: Pick<ProfileRow, "display_name" | "first_name" | "last_name">,
): boolean {
  return Boolean(
    (profile.display_name || profile.first_name || profile.last_name).trim(),
  );
}

export function useProfile() {
  return useQuery({
    // Same key as the account profile page — one cache entry per session.
    queryKey: ["profile"],
    queryFn: () => api<ProfileRow>("/api/spa/profile/"),
  });
}

export function useUpdateProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Partial<ProfileRow>) =>
      api<ProfileRow>("/api/spa/profile/", {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: (data) => qc.setQueryData(["profile"], data),
  });
}
