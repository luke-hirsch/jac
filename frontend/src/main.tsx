import "@/index.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createRouter } from "@tanstack/react-router";
import { queryClient } from "@/lib/query";
import { routeTree } from "./routeTree.gen";

// "preserve": portfolio addresses end in a slash (portfolio.<base>/<handle>/<slug>/), and a
// navigation to one keeps it. Both forms match the same route either way.
const router = createRouter({ routeTree, trailingSlash: "preserve" });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
