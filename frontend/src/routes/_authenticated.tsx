import { createFileRoute, redirect, isRedirect } from "@tanstack/react-router";
import { api } from "@/lib/api";

type Session = { meta?: { is_authenticated?: boolean } };

export const Route = createFileRoute("/_authenticated")({
  beforeLoad: async ({ location }) => {
    try {
      const session = await api<Session>("/_allauth/browser/v1/auth/session");
      if (!session.meta?.is_authenticated) {
        throw redirect({
          to: "/auth/login",
          search: { redirect: location.href },
        });
      }
    } catch (error) {
      if (isRedirect(error)) throw error;
      throw redirect({
        to: "/auth/login",
        search: { redirect: location.href },
      });
    }
  },
});
