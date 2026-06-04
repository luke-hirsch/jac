import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

type Profile = {
  display_name: string;
  bio: string;
  timezone: string;
};

export const Route = createFileRoute("/_authenticated/account")({
  component: Account,
});

function Account() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["profile"],
    queryFn: () => api<Profile>("/api/spa/profile/"),
  });

  if (isLoading) return <div className="p-8">loading…</div>;
  if (error) return <div className="p-8">error: {String(error)}</div>;
  return <pre className="p-8 text-sm">{JSON.stringify(data, null, 2)}</pre>;
}
