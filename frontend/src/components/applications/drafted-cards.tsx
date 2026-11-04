import { useBlocker } from "@tanstack/react-router";
import { type ApplicationRow } from "@/lib/queries/applications";
import { AttachmentsCard } from "./attachments-card";
import { ApplicationContentCard } from "./content-card";
import { ExportCard } from "./export-card";
import { useApplicationDraft } from "./use-application-draft";
import { type Fresh } from "./use-fresh-highlight";

// Stable, so the blocker isn't re-registered on every render. True = stay.
const confirmLeave = () =>
  !confirm("This application has unsaved edits. Leave and lose them?");

/** The cards that share one application's drafts. Mount it keyed by the application: another
 *  application's drafts start over, whatever the three-way says. */
export function DraftedCards({
  app,
  fresh,
}: {
  app: ApplicationRow;
  fresh: Fresh;
}) {
  const draft = useApplicationDraft(app);
  // Registered only while dirty, and the router's blocker also arms `beforeunload` for as
  // long as it is registered: the same guard covers reload and closing the tab.
  useBlocker({ shouldBlockFn: confirmLeave, disabled: !draft.dirty });
  return (
    <div className="space-y-6">
      <ApplicationContentCard app={app} fresh={fresh} draft={draft} />
      <AttachmentsCard app={app} />
      <ExportCard app={app} dirty={draft.dirty} onSave={draft.save} />
    </div>
  );
}
