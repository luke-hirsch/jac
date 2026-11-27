import {
  createFileRoute,
  Outlet,
  retainSearchParams,
} from "@tanstack/react-router";
import { Page } from "@/components/page";
import { applicationsSearch } from "@/lib/queries/applications";

export const Route = createFileRoute("/_authenticated/applications")({
  // The list's filter (status, search, sort) lives on the section, so opening an application
  // and going back (its link, the nav) lands on the same list. Clearing one takes an explicit
  // `undefined`; leaving the section drops them.
  validateSearch: applicationsSearch,
  search: { middlewares: [retainSearchParams(["status", "q", "sort"])] },
  component: ApplicationsLayout,
});

function ApplicationsLayout() {
  return (
    <Page>
      <Outlet />
    </Page>
  );
}
