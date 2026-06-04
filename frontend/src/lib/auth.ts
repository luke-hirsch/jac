import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";

export type AuthStatus = "anonymous" | "authenticated" | "mfa_required";

type SessionResponse = {
  status: number;
  data?: { user?: { id: number; email: string } };
  meta?: { is_authenticated?: boolean };
};

export function useAuth() {
  const query = useQuery({
    queryKey: ["auth", "session"],
    queryFn: async () => {
      try {
        return await api<SessionResponse>("/_allauth/browser/v1/auth/session");
      } catch (e) {
        if (e instanceof ApiError && (e.status === 401 || e.status === 410)) {
          return e.data as SessionResponse;
        }
        throw e;
      }
    },
    staleTime: 30_000,
  });

  const data = query.data;
  let status: AuthStatus = "anonymous";
  if (data?.meta?.is_authenticated) status = "authenticated";
  else if (data?.status === 401 && data?.data?.user) status = "mfa_required";

  return { ...query, status, user: data?.data?.user };
}
