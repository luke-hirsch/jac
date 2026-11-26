import type { ReactNode } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { z } from "zod";
import { Page } from "@/components/page";
import { Button } from "@/components/ui/button";
import { jacOrigin, leaveTo, portfolioOrigin, siteHost } from "@/lib/host";
import { PageTitle } from "@/components/heading";

/** portfolio.<base>/about/: what a personalised portfolio is, for the visitor who wonders. The same
 *  page for every owner; `?from=<handle>` only adds the way back. "about" is a reserved handle
 *  (settings.RESERVED_HANDLES): this static route wins over `/$handle/`. */
const aboutSearchSchema = z.object({
  from: z
    .string()
    .regex(/^[a-z0-9_-]{1,64}$/i)
    .optional()
    .catch(undefined),
});

export const Route = createFileRoute("/about/")({
  validateSearch: aboutSearchSchema,
  head: () => ({
    meta: [
      { title: "About this page" },
      { name: "robots", content: "noindex" },
    ],
  }),
  beforeLoad: ({ location }) => {
    if (siteHost() !== "portfolio")
      return leaveTo(portfolioOrigin() + location.href);
  },
  component: About,
});

function Section({
  id,
  title,
  children,
}: {
  id: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <section id={id} className="space-y-3 scroll-mt-6">
      <h2 className="font-heading text-2xl font-semibold">{title}</h2>
      {children}
    </section>
  );
}

function About() {
  const { from } = Route.useSearch();
  return (
    <Page as="main" width="narrow" className="md:gap-12">
      {from ? (
        <Link
          to="/$handle/"
          params={{ handle: from.toLowerCase() }}
          className="text-sm text-muted-foreground hover:underline"
        >
          ← Back to the portfolio
        </Link>
      ) : null}

      <header className="space-y-3">
        <PageTitle>
          About this page
        </PageTitle>
        <p className="text-lg text-muted-foreground">
          This portfolio isn't the same for everyone. It's put together for each
          visitor from the career entries its owner keeps in jac, the same data
          their CVs are made from.
        </p>
      </header>

      <Section id="how" title="How it's put together">
        <ul className="list-disc space-y-2 pl-5">
          <li>
            <strong>From a QR code or a link in an application:</strong> the
            owner chose what's on it for that occasion, so you see first what
            they wanted you to see. You can explore the rest below it.
          </li>
          <li>
            <strong>From the questionnaire:</strong> your answers pick the
            topics, the order and the tone. If you wrote what you're looking
            for, the entries are sorted by how well they match it.
          </li>
          <li>
            <strong>Feeling lucky:</strong> the owner's highlights and a random
            handful of the rest.
          </li>
          <li>
            <strong>Skipping the questions:</strong> the page the owner put
            together for everyone, without AI.
          </li>
        </ul>
        <p>
          Every entry is in the owner's own words. “Start over” at the top
          always takes you back to the questionnaire.
        </p>
      </Section>

      <Section id="ai" title="Where AI comes in">
        <ul className="list-disc space-y-2 pl-5">
          <li>
            <strong>Sorting by your interest:</strong> what you wrote is
            compared with every entry, and the closest come first. Nothing is
            written here, only sorted.
          </li>
          <li>
            <strong>The welcome at the top:</strong> a short paragraph a
            language model writes from the entries on the page. It's told to
            invent nothing, but it can still get something wrong. The entries
            below it are what counts.
          </li>
        </ul>
        <p>
          Both use jac's own AI model, which runs on a server of its own rather
          than at a large AI provider. That server sleeps while nobody needs it.
          Waking it takes about three minutes, which is why you may have been
          asked to wait. You can always browse the standard portfolio meanwhile:
          the same entries, in their usual order and without the welcome.
        </p>
      </Section>

      <Section id="remembered" title="What's remembered">
        <ul className="list-disc space-y-2 pl-5">
          <li>
            Your browser remembers which page you saw here, so coming back takes
            you straight to it. That stays in your browser, and “Start over”
            forgets it.
          </li>
          <li>
            What you typed into the questionnaire never goes into the page's
            address. It's handed to the AI as a short job and deleted when the
            job ends. The owner doesn't see it.
          </li>
        </ul>
        <p>
          The{" "}
          <a href="/legal/privacy/" className="underline">
            privacy policy
          </a>{" "}
          (in German) has the details.
        </p>
      </Section>

      <Section id="try" title="Try it yourself">
        <p>
          jac is the tool behind this page. It keeps a career in one place,
          writes a CV and a cover letter tailored to a job posting, and builds a
          portfolio like this one. You can try it with your own data.
        </p>
        <Button asChild>
          <a href={`${jacOrigin()}/auth/signup`}>Try jac</a>
        </Button>
      </Section>
    </Page>
  );
}
